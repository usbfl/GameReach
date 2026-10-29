"""Capability registry and normalized adapters. No arbitrary URL-fetch tool is exposed."""

import asyncio
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field
from .config import ROOT
from .db import now
from .schemas import Action
from .discovery import PLATFORMS, GENRES


class BudgetExceeded(Exception):
    pass


class Stopped(Exception):
    pass


class SourceFailure(Exception):
    def __init__(self, code, retry_after=None):
        super().__init__(code)
        self.code, self.retry_after = code, retry_after


class ToolResult(BaseModel):
    status: str = "available"
    provider: str
    capability: str
    data: dict = Field(default_factory=dict)
    source_refs: list[str] = Field(default_factory=list)
    fetched_at: str = Field(default_factory=now)
    from_cache: bool = False
    stale: bool = False
    warnings: list[str] = Field(default_factory=list)
    error_code: str | None = None
    retry_after: float | None = None
    provenance_type: str = "live"


class Gate:
    def __init__(self, settings, context=None, check=None, checkpoint=None):
        self.settings = settings
        self.ctx = context if context is not None else {}
        self.ctx.setdefault(
            "budget",
            {"tool_calls": 0, "external_requests": 0, "llm_calls": 0, "planning_rounds": 0, "active_seconds": 0},
        )
        self.check = check or (lambda: None)
        self.checkpoint = checkpoint or (lambda: None)
        self.started = time.monotonic()

    def guard(self):
        self.check()
        elapsed = self.ctx["budget"].get("active_seconds", 0) + time.monotonic() - self.started
        if elapsed >= self.settings.max_active_seconds:
            raise BudgetExceeded("活跃执行时间已达上限")

    def use(self, kind):
        self.guard()
        n = self.ctx["budget"].get(kind, 0)
        if n >= getattr(self.settings, "max_" + kind):
            raise BudgetExceeded(f"{kind} 预算耗尽")
        self.ctx["budget"][kind] = n + 1
        self.checkpoint()

    async def sleep(self, seconds):
        # Interruptible rate-limit/retry wait, with a finite active-time budget.
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            self.guard()
            await asyncio.sleep(min(0.2, until - time.monotonic()))


def clean_text(text, limit=30000):
    soup = BeautifulSoup(str(text), "html.parser")
    for tag in soup(["script", "style", "iframe", "object"]):
        tag.decompose()
    return re.sub(r"\[/?(?:url|img|b|i|h[1-6]|list|\*|quote|code)[^\]]*\]", "", soup.get_text(" ", strip=True))[:limit]


def safe_link(url):
    if not url:
        return None
    p = urlparse(url)
    return url if p.scheme == "https" and p.hostname and not p.username and not p.password else None


def redact(text):
    text = re.sub(
        r'(?i)(authorization|cookie|api[_-]?key|client_secret|access_token|token|key)([\s"\x27:=?&]+)[^\s&,\x27"}]+',
        r"\1\2[REDACTED]",
        str(text),
    )
    return re.sub(r"sk-[A-Za-z0-9_-]{6,}", "[REDACTED]", text)


def stamp(value):
    try:
        return datetime.fromtimestamp(int(value), timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError):
        return None


def retry_seconds(value):
    try:
        return max(0, float(value))
    except (TypeError, ValueError):
        try:
            return max(0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError):
            return 1.0


class HTTP:
    def __init__(self, settings, client=None):
        self.s = settings
        self.client = client or httpx.AsyncClient(timeout=15, follow_redirects=False)
        self.last = {}

    async def request(self, gate, method, url, *, interval=0, attempts=None, **kwargs):
        host = urlparse(url).hostname
        for attempt in range((self.s.max_retries_per_call if attempts is None else attempts) + 1):
            delay = interval - (time.monotonic() - self.last.get(host, 0))
            if delay > 0:
                await gate.sleep(delay)
            gate.use("external_requests")
            self.last[host] = time.monotonic()
            retry = False
            failure_code = "unavailable"
            wait = min(2**attempt, 4)
            try:
                # Streaming caps bytes before a response is materialized.
                remaining = max(0.001, self.s.max_active_seconds - gate.ctx["budget"]["active_seconds"]
                                - (time.monotonic() - gate.started))
                async with asyncio.timeout(remaining), self.client.stream(method, url, **kwargs) as r:
                    if r.status_code in [401, 403]:
                        raise SourceFailure("requires_auth")
                    if r.status_code == 429:
                        wait = retry_seconds(r.headers.get("Retry-After"))
                        if wait > 5 or attempt >= (self.s.max_retries_per_call if attempts is None else attempts):
                            raise SourceFailure("rate_limited", wait)
                        retry = True
                    elif r.status_code >= 500:
                        retry = True
                        failure_code = f"http_{r.status_code}"
                    elif not 200 <= r.status_code < 300:
                        raise SourceFailure(f"http_{r.status_code}")
                    else:
                        chunks, size = [], 0
                        async for b in r.aiter_bytes():
                            gate.guard()
                            size += len(b)
                            if size > 2_000_000:
                                raise SourceFailure("response_too_large")
                            chunks.append(b)
                        try:
                            return json.loads(b"".join(chunks))
                        except (ValueError, UnicodeError):
                            raise SourceFailure("invalid_json")
            except TimeoutError:
                raise BudgetExceeded("活跃执行时间已达上限") from None
            except httpx.ProxyError:
                # A blocked proxy is not a malformed model response or permission
                # to retry via another route. Preserve a report and stop honestly.
                raise SourceFailure("proxy_blocked") from None
            except httpx.TimeoutException as exc:
                retry = True
                failure_code = {httpx.ReadTimeout: "read_timeout", httpx.ConnectTimeout: "connect_timeout",
                                httpx.WriteTimeout: "write_timeout", httpx.PoolTimeout: "pool_timeout"}.get(type(exc), "request_timeout")
            except httpx.NetworkError:
                retry = True
                failure_code = "network_error"
            if retry:
                if attempt >= (self.s.max_retries_per_call if attempts is None else attempts):
                    gate.ctx.setdefault("request_failures", []).append({"host": host, "code": failure_code,
                        "attempts": attempt + 1, "game_id": gate.ctx.get("active_game")})
                    raise SourceFailure(failure_code)
                await gate.sleep(wait)
        raise SourceFailure("unavailable")


class FixtureSource:
    name = "fixture"
    capabilities = ["search_games", "get_game_details", "get_reviews", "get_updates"]
    auth_requirements = []

    def __init__(self, db):
        corpus = json.loads((ROOT / "data/fixtures/catalog.json").read_text(encoding="utf-8"))
        self.games = {g["id"]: g for g in corpus["games"]}
        self.docs = corpus["documents"]
        self.db = db

    async def call(self, cap, args, gate):
        if cap == "search_games":
            q = args.get("q", "").lower()
            games = [
                g
                for g in self.games.values()
                if not q or q in (g["name"] + g["subtitle"] + " ".join(g["tags"])).lower()
            ]
            return {"games": games[args.get("catalog_offset", 0):args.get("catalog_offset", 0) + (args.get("catalog_page_size", 100) if args.get("tag_recall") else gate.settings.max_candidates)], "search_info": {"candidates_scanned": len(games), "exhausted": True}}
        gid = args.get("game_id")
        if gid not in self.games:
            raise SourceFailure("unknown_game")
        kind = {"get_game_details": "details", "get_reviews": "reviews", "get_updates": "updates"}[cap]
        docs = [d for d in self.docs if d["game_id"] == gid and d["kind"] == kind]
        if args.get("after"):
            docs = [d for d in docs if d.get("published_at") and d["published_at"] > args["after"]]
        result = {"documents": docs}
        if kind == "details":
            result["games"] = [self.games[gid]]
        if kind == "reviews":
            result["sampling"] = {
                "sample_size": len(docs),
                "filter": "synthetic sample",
                "after": args.get("after"),
                "representative": False,
            }
        return result


class IGDBSource:
    name = "igdb"
    capabilities = ["search_games", "get_game_details"]
    auth_requirements = ["IGDB_CLIENT_ID", "IGDB_CLIENT_SECRET"]
    fields = "name,summary,storyline,url,first_release_date,platforms.name,language_supports.language.locale,language_supports.language_support_type.name,game_type.type,game_status.status,parent_game,version_parent,genres.name,keywords.name,total_rating_count,websites.url,external_games.url,external_games.uid,external_games.external_game_source.name"

    def __init__(self, settings, http):
        self.s, self.http = settings, http
        self.token = None
        self.expires = 0

    async def resolve_steam(self, appids, gate):
        if not appids or len(appids) > 30 or any(not re.fullmatch(r"\d+", x) for x in appids):
            raise SourceFailure("invalid_steam_ids")
        token = await self.authenticate(gate)
        headers = {"Client-ID": self.s.igdb_client_id, "Authorization": "Bearer " + token}
        fields = ",".join("game." + f for f in self.fields.split(","))
        rows = await self.http.request(
            gate,
            "POST",
            "https://api.igdb.com/v4/external_games",
            headers=headers,
            content="fields uid,external_game_source.name,"
            + fields
            + "; where uid = ("
            + ",".join(json.dumps(x) for x in appids)
            + ') & external_game_source.name = "Steam"; limit 100;',
            interval=self.s.igdb_min_interval,
        )
        if not isinstance(rows, list):
            raise SourceFailure("invalid_schema")
        matches = {}
        for row in rows:
            appid = str(row.get("uid", ""))
            game = row.get("game", {})
            if (
                appid in appids
                and row.get("external_game_source", {}).get("name", "").lower() == "steam"
                and isinstance(game, dict)
                and game.get("name")
                and game.get("id")
            ):
                normalized = self.normalize(game)
                if normalized.get("external_ids", {}).get("steam") not in [None, appid]:
                    continue
                normalized["external_ids"]["steam"] = appid
                normalized["store_links"]["steam"] = f"https://store.steampowered.com/app/{appid}/"
                matches.setdefault(appid, {})[normalized["id"]] = normalized
        return {appid: next(iter(games.values())) for appid, games in matches.items() if len(games) == 1}

    async def authenticate(self, gate):
        if not self.s.igdb_client_id or not self.s.igdb_client_secret.get_secret_value():
            raise SourceFailure("requires_auth")
        if self.token and time.monotonic() < self.expires:
            return self.token
        obj = await self.http.request(
            gate,
            "POST",
            "https://id.twitch.tv/oauth2/token",
            data={
                "client_id": self.s.igdb_client_id,
                "client_secret": self.s.igdb_client_secret.get_secret_value(),
                "grant_type": "client_credentials",
            },
        )
        if not isinstance(obj, dict) or not obj.get("access_token"):
            raise SourceFailure("invalid_schema")
        self.token = obj["access_token"]
        self.expires = time.monotonic() + max(0, int(obj.get("expires_in", 0)) - 60)
        return self.token

    def normalize(self, g):
        gid = f"igdb-{g['id']}"
        links = [x.get("url", "") for x in g.get("websites", []) + g.get("external_games", [])]
        steam = next(
            (
                re.search(r"^https?://store\.steampowered\.com/app/(\d+)(?:/|$)", u).group(1)
                for u in links
                if re.search(r"^https?://store\.steampowered\.com/app/(\d+)(?:/|$)", u)
            ),
            None,
        )
        stores = {}
        for u in links:
            # Exact domain boundaries prevent lookalike domains becoming trusted stores.
            host = (urlparse(u).hostname or "").lower()
            key = next(
                (
                    k
                    for k, domains in {
                        "steam": ("store.steampowered.com",),
                        "gog": ("gog.com", "www.gog.com"),
                        "epic": ("store.epicgames.com", "www.epicgames.com"),
                    }.items()
                    if host in domains
                ),
                None,
            )
            if host == "itch.io" or host.endswith(".itch.io"):
                key = "itch"
            if key and safe_link(u):
                stores.setdefault(key, u)
        if steam:
            stores["steam"] = f"https://store.steampowered.com/app/{steam}/"
        platforms = [p.get("name", "") for p in g.get("platforms", [])]
        platforms = [("PC" if "Windows" in p else p) for p in platforms] or None
        langs = [
            x.get("language", {}).get("locale")
            for x in g.get("language_supports", [])
            if x.get("language_support_type", {}).get("name", "").lower() in ["interface", "subtitles"]
        ]
        langs = list({language.replace("_", "-") for language in langs if language}) or None
        typename = g.get("game_type", {}).get("type", "").lower()
        kind = {
            "main_game": "base",
            "main game": "base",
            "dlc_addon": "dlc",
            "dlc addon": "dlc",
            "remake": "remake",
            "remaster": "remaster",
        }.get(typename, "other" if typename else None)
        state = g.get("game_status", {}).get("status", "").lower()
        released = (
            True
            if state == "released"
            else False
            if state in ["early_access", "early access", "alpha", "beta", "cancelled", "unreleased", "offline"]
            else None
        )
        # A past date alone does not prove formal release (early access can have one).
        return {
            "id": gid,
            "name": g["name"],
            "subtitle": "IGDB 元数据 · " + (" / ".join(stores) or "商店未知"),
            "store_links": stores,
            "source_url": safe_link(g.get("url")),
            "summary": g.get("summary", ""),
            "catalog_keywords": [k.get("name", "") for k in g.get("keywords", [])],
            "platforms": platforms,
            "languages": langs,
            "released": released,
            "kind": kind,
            "price": None,
            "parent_id": f"igdb-{g['parent_game']}" if g.get("parent_game") else None,
            "external_ids": {"igdb": str(g["id"]), **({"steam": steam} if steam else {})},
            "tags": [x["name"] for x in g.get("genres", []) if "name" in x],
            "dataset_id": "live",
            "provenance_type": "live",
        }

    async def call(self, cap, args, gate):
        token = await self.authenticate(gate)
        if cap == "search_games":
            q = args.get("q", "")
            # JSON string escaping is valid for the API query's search string.
            platform = args.get("platform", "all")
            platform_ids = {"pc": 6, "switch": 130, "ps5": 167, "xbox_series": 169}
            selected_platforms = args.get("platforms", []) or ([platform] if platform in platform_ids else [])
            conditions = []
            if selected_platforms:
                conditions.append("platforms = (" + ",".join(str(platform_ids[x]) for x in selected_platforms) + ")")
            genres = [x for x in args.get("genre_ids", []) if x in GENRES]
            if genres:
                conditions.append("genres = (" + ",".join(map(str, genres)) + ")")
            hints = args.get("keyword_hints", [])
            if args.get("tag_recall") and args.get("tag_keywords"):
                # Vocabulary-derived hints are escaped as API strings, never executable queries.
                terms = args["tag_keywords"]
                conditions.append("(" + " | ".join("keywords.name ~ *" + json.dumps(term) + "*" for term in terms) + ")")
            if hints:
                terms = list(
                    dict.fromkeys(
                        term
                        for hint in hints
                        for term in (
                            ["deckbuilding", "deck-building", "deck building", "deckbuilder"]
                            if hint == "deckbuilding"
                            else ["action-rpg", "action rpg", "action role-playing", "action role playing",
                                  "soulslike", "souls-like", "character action", "hack and slash"]
                            if hint == "action-rpg"
                            else ["action-roguelike", "action roguelike", "roguelike", "roguelite", "rogue-lite"]
                            if hint == "action-roguelike"
                            else [hint]
                        )
                    )
                )
                conditions.append(
                    "(" + " | ".join("keywords.name ~ *" + json.dumps(term) + "*" for term in terms) + ")"
                )
            if args.get("discover"):
                conditions.append("version_parent = null")
                # IGDB can sort null rating counts ahead of rated titles. Prefer the
                # bounded, reviewable catalog; this is recall, never recommendation proof.
                conditions.append("total_rating_count > 0")
            where = "where " + " & ".join(conditions) + "; " if conditions else ""
            # Store filters inspect a bounded batch, not the complete catalog.
            limit = (
                100
                if args.get("discover") or args.get("stores") or args.get("store", "all") != "all"
                else gate.settings.max_candidates
            )
            if args.get("tag_recall"):
                limit = args.get("catalog_page_size", 100)
            query = (
                (f"search {json.dumps(q)}; " if q else "")
                + where
                + ("sort total_rating_count desc; " if args.get("discover") and not q else "")
                + f"fields {self.fields}; limit {limit};"
                + (f" offset {args.get('catalog_offset', 0)};" if args.get("tag_recall") else "")
            )
        else:
            gid = args.get("game_id", "")
            if not re.fullmatch(r"igdb-\d+", gid):
                raise SourceFailure("unknown_game")
            query = f"fields {self.fields}; where id = {int(gid[5:])}; limit 1;"
        try:
            rows = await self.http.request(
                gate,
                "POST",
                "https://api.igdb.com/v4/games",
                content=query,
                headers={"Client-ID": self.s.igdb_client_id, "Authorization": "Bearer " + token},
                interval=self.s.igdb_min_interval,
            )
        except SourceFailure as e:
            if e.code == "requires_auth":
                self.token = None
            raise
        if not isinstance(rows, list) or any(not isinstance(g, dict) or "id" not in g or "name" not in g for g in rows):
            raise SourceFailure("invalid_schema")
        docs = []
        for g in rows:
            if cap == "get_game_details":
                docs.append(
                    {
                        "id": f"igdb-{g['id']}-details",
                        "game_id": f"igdb-{g['id']}",
                        "provider": "igdb",
                        "external_id": str(g["id"]),
                        "title": g["name"],
                        "url": safe_link(g.get("url")),
                        "text": clean_text(g.get("summary", "") + "\n" + g.get("storyline", "")),
                        "published_at": None,
                        "kind": "details",
                        "language": "unknown",
                        "dataset_id": "live",
                        "provenance_type": "live",
                    }
                )
        games = [self.normalize(g) for g in rows]
        store = args.get("store", "all")
        stores = args.get("stores", []) or ([store] if store != "all" else [])
        if cap == "search_games" and stores:
            games = [g for g in games if set(stores).intersection(g["store_links"])]
        if cap == "search_games" and args.get("discover"):
            games = [g for g in games if g.get("kind") in ["base", "remake", "remaster", None]]
            if any(h in args.get("keyword_hints", []) for h in ["action-rpg", "action-roguelike"]):
                # Investigation order only. Catalog clues never enter verified DNA.
                def action_priority(game):
                    text = " ".join(game.get("catalog_keywords", [])) + " " + game.get("summary", "")
                    action = bool(re.search(r"action|combat|hack.and.slash", text, re.I))
                    builds = bool(re.search(r"build|customi[sz]|weapon|loadout|skill.tree|boon", text, re.I))
                    close_combat = bool(re.search(r"melee|hack.and.slash|dodge|parry", text, re.I))
                    return -(int(action) + int(builds) + int(close_combat))

                games.sort(key=action_priority)
            # Round-robin across selected scopes prevents a single store/platform consuming the cap.
            buckets = []
            if "action-rpg" in args.get("keyword_hints", []):
                def rogue(game):
                    return bool(re.search(r"rogue", " ".join(game.get("catalog_keywords", [])), re.I))
                # One popular subgenre must not consume a two-game investigation.
                # These are recall buckets, not verified mechanic assessments.
                buckets += [[g for g in games if not rogue(g)], [g for g in games if rogue(g)]]
            buckets += [[g for g in games if name in g.get("store_links", {})] for name in stores]
            buckets += [
                [g for g in games if PLATFORMS[name][1] in (g.get("platforms") or [])]
                for name in args.get("platforms", [])
            ]
            if buckets:
                ordered, seen = [], set()
                for i in range(len(games)):
                    for bucket in buckets:
                        if i < len(bucket) and bucket[i]["id"] not in seen:
                            ordered.append(bucket[i])
                            seen.add(bucket[i]["id"])
                games = ordered

        cap_limit = args.get("catalog_page_size", 100) if args.get("tag_recall") else gate.settings.max_candidates
        return {
            "games": games[:cap_limit],
            "documents": docs,
            "search_info": {
                "provider": "igdb",
                "candidates_scanned": len(rows),
                "exhausted": len(rows) < limit if cap == "search_games" else True,
                "offset": args.get("catalog_offset", 0),
                "store": store,
                "stores": stores,
                "platform": args.get("platform", "all"),
                "platforms": args.get("platforms", []),
                "keyword_hints": args.get("keyword_hints", []),
                "genre_hints": [GENRES[x] for x in args.get("genre_ids", []) if x in GENRES],
                "matched_before_cap": len(games),
                "store_counts": {
                    name: sum(name in g.get("store_links", {}) for g in games[:cap_limit])
                    for name in (stores or ["steam", "gog", "epic", "itch"])
                },
                "recall_order": "按评价量与动作/构筑目录线索召回，动作RPG与动作肉鸽交替，并兼顾所选商店/平台；类型不是玩法适配评分"
                if args.get("discover")
                else "名称相关性",
                "scope": "IGDB 收录信息；商店链接不等于当前地区可购买。最多检查 100 个候选，非全库穷尽。",
            },
        }


class SteamStoreSource:
    """Optional public Store endpoint; exact catalog AppID, bounded response, no key."""

    name = "steam_store"
    capabilities = ["get_game_details"]
    auth_requirements = []

    def __init__(self, settings, http):
        self.s, self.http = settings, http

    async def enrich(self, game, gate):
        appid = str(game.get("external_ids", {}).get("steam", ""))
        if not re.fullmatch(r"\d+", appid):
            return None
        obj = await self.http.request(
            gate,
            "GET",
            "https://store.steampowered.com/api/appdetails",
            params={"appids": appid, "l": "english"},
            interval=self.s.steam_min_interval,
        )
        body = obj.get(appid, {}) if isinstance(obj, dict) else {}
        data = body.get("data", {})
        if body.get("success") is not True or str(data.get("steam_appid")) != appid:
            raise SourceFailure("steam_store_identity_unconfirmed")
        game["catalog_url"] = game.get("catalog_url") or game.get("source_url")
        game["source_url"] = f"https://store.steampowered.com/app/{appid}/"
        game["source_identity_verified_by"] = "steam_store_appid"
        genres = data.get("genres")
        coming = data.get("release_date", {}).get("coming_soon")
        # Explicit coming_soon=false + checked genre list can confirm formal release.
        # A past catalog date alone still cannot. Price remains unknown (region-specific).
        if isinstance(coming, bool) and isinstance(genres, list):
            early = any(str(g.get("id")) == "37" or g.get("description", "").lower() == "early access" for g in genres)
            game["released"] = not coming and not early
            game["release_source_url"] = f"https://store.steampowered.com/app/{appid}/"
        text = clean_text(data.get("detailed_description") or data.get("short_description") or "")
        return {
            "id": f"steam-store-{appid}",
            "game_id": game["id"],
            "provider": self.name,
            "external_id": appid,
            "title": data.get("name", game["name"]) + " · Steam 商店玩法说明",
            "text": text,
            "url": f"https://store.steampowered.com/app/{appid}/",
            "published_at": None,
            "kind": "details",
            "language": "english",
            "dataset_id": "live",
            "provenance_type": "live",
        }


class SteamSource:
    auth_requirements = []

    def __init__(self, settings, http, db, reviews=False):
        self.s, self.http, self.db, self.reviews = settings, http, db, reviews
        self.name = "steam_reviews" if reviews else "steam_news"
        self.capabilities = ["get_reviews"] if reviews else ["get_updates"]

    async def call(self, cap, args, gate):
        game = self.db.get("game", args.get("game_id"), {})
        appid = game.get("external_ids", {}).get("steam")
        if not appid or not re.fullmatch(r"\d+", str(appid)):
            raise SourceFailure("missing_steam_id")
        if self.reviews:
            # Separate sentiment and time strata, then deduplicate by immutable review ID.
            # This is a deliberately balanced reading sample, NOT a population rating.
            limit = min(self.s.max_reviews_per_game, args.get("review_limit") or self.s.max_reviews_per_game)
            single = args.get("after") or args.get("cursor", "*") != "*"
            buckets = (
                [("recent", 1, 0, limit)]
                if single
                else [
                    # Obtain the all-sentiment population summary before spending on
                    # narrower strata, so a later rate limit cannot erase the rating.
                    ("recent", 1, 0, limit - 2 * (limit * 3 // 8)),
                    ("positive_helpful", 0, 1, limit * 3 // 8),
                    ("negative_helpful", 0, 2, limit * 3 // 8),
                ]
            )
            docs, seen, samples, failures = [], set(), [], []
            rating = None
            last_cursor = None
            for label, order, sentiment, quota in buckets:
                cursor = args.get("cursor", "*") if single else "*"
                received = 0
                while received < quota:
                    params = {
                        "appid": int(appid),
                        "filter": order,
                        "day_range": 365,
                        "use_review_quality": True,
                        "languages": ["all"],
                        "cursor": cursor,
                        "review_type": sentiment,
                        "purchase_type": 0,
                        "num_per_page": min(100, quota - received),
                        "filter_offtopic_activity": True,
                    }
                    if args.get("after"):
                        params |= {
                            "date_range_start": int(datetime.fromisoformat(args["after"]).timestamp()),
                            "date_range_end": int(time.time()),
                        }
                    try:
                        # Public review API does not need the player's personal Web API key.
                        # Its optional key is a publisher key associated with this app.
                        obj = await self.http.request(
                            gate,
                            "GET",
                            "https://api.steampowered.com/IUserReviewsService/GetAppReviews/v1/",
                            params={"input_json": json.dumps(params)},
                            interval=self.s.steam_min_interval,
                        )
                        body = obj.get("response", obj) if isinstance(obj, dict) else None
                        # Protobuf JSON may omit an empty repeated field. Only accept
                        # omission when a zero count explicitly proves the empty page.
                        if isinstance(body, dict) and "reviews" not in body and (
                            body.get("total_matching") == 0 or body.get("query_summary", {}).get("num_reviews") == 0
                        ):
                            body["reviews"] = []
                        if not isinstance(body, dict) or not isinstance(body.get("reviews"), list):
                            raise SourceFailure("invalid_schema")
                    except (SourceFailure, BudgetExceeded) as exc:
                        if not docs:
                            raise
                        failures.append(getattr(exc, "code", str(exc)))
                        break
                    page = body["reviews"][: params["num_per_page"]]
                    # Only the all-sentiment initial page has a population summary.
                    summary = body.get("query_summary")
                    if sentiment == 0 and cursor == "*" and isinstance(summary, dict):
                        positive, negative = summary.get("total_positive"), summary.get("total_negative")
                        if type(positive) is int and type(negative) is int and min(positive, negative) >= 0:
                            rating = {"positive": positive, "negative": negative, "provider": "steam_reviews",
                                      "scope": "Steam purchases; all languages; off-topic filtered", "fetched_at": now(),
                                      "purchase_type": 0, "source_url": f"https://store.steampowered.com/app/{appid}/#app_reviews_hash"}
                    samples.append(
                        {"stratum": label, "received": len(page), "day_range_used": body.get("day_range_used")}
                    )
                    received += len(page)
                    added = 0
                    for r in page:
                        if not isinstance(r, dict) or "recommendationid" not in r or "review" not in r:
                            raise SourceFailure("invalid_schema")
                        eid = str(r.get("recommendationid", ""))
                        text = clean_text(r.get("review", ""), limit=None)
                        if not eid or not text or eid in seen:
                            continue
                        seen.add(eid)
                        added += 1
                        author = str(r.get("author", {}).get("steamid", ""))
                        url = (
                            f"https://steamcommunity.com/profiles/{author}/recommended/{appid}/"
                            if re.fullmatch(r"\d{17}", author)
                            else f"https://store.steampowered.com/app/{appid}/#app_reviews_hash"
                        )
                        docs.append(
                            self.doc(
                                args["game_id"],
                                eid,
                                text,
                                stamp(r.get("timestamp_created")),
                                f"Steam 评论 {eid}",
                                url,
                                language=r.get("language", "unknown"),
                                updated_at=stamp(r.get("timestamp_updated")),
                                voted_up=r.get("voted_up"),
                                helpful_votes=r.get("votes_up", 0),
                                playtime_at_review=r.get("author", {}).get("playtime_at_review"),
                                sampling_stratum=label,
                                complete=True,
                                author_key=hashlib.sha256(("steam:" + author).encode()).hexdigest() if author else None,
                            )
                        )
                    next_cursor = body.get("cursor")
                    last_cursor = next_cursor
                    if not page or not added or not next_cursor or next_cursor == cursor:
                        break
                    cursor = next_cursor
                if failures:
                    break
            return {
                "documents": docs[:limit],
                "rating": rating,
                "cursor": last_cursor if single else None,
                "sampling": {
                    "sample_size": min(len(docs), limit),
                    "limit": limit,
                    "filter": "stratified_positive_negative_recent" if not single else "Recent",
                    "strata": samples,
                    "languages": ["all"],
                    "after": args.get("after"),
                    "representative": False,
                    "errors": failures,
                },
            }
        obj = await self.http.request(
            gate,
            "GET",
            "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/",
            params={"appid": int(appid), "count": 10, "maxlength": 3000},
            interval=self.s.steam_min_interval,
        )
        rows = obj.get("appnews", {}).get("newsitems") if isinstance(obj, dict) else None
        if not isinstance(rows, list):
            raise SourceFailure("invalid_schema")
        docs = []
        for r in rows:
            if "contents" not in r:
                raise SourceFailure("invalid_schema")
            text = clean_text(r["contents"])
            eid = str(r.get("gid") or hashlib.sha256((str(safe_link(r.get("url"))) + text).encode()).hexdigest())
            docs.append(
                self.doc(
                    args["game_id"],
                    eid,
                    text,
                    stamp(r.get("date")),
                    r.get("title", "Steam 公告"),
                    safe_link(r.get("url")),
                    developer=r.get("feedname") == "steam_community_announcements",
                    feedname=r.get("feedname"),
                )
            )
        return {"documents": docs}

    def doc(self, gid, eid, text, published, title, url, **extra):
        return {
            "id": f"{self.name}-{eid}",
            "game_id": gid,
            "external_id": str(eid),
            "provider": self.name,
            "title": title,
            "text": text,
            "published_at": published,
            "url": url,
            "language": "unknown",
            "kind": "reviews" if self.reviews else "updates",
            "dataset_id": "live",
            "provenance_type": "live",
            **extra,
        }


class Registry:
    def __init__(self, settings, db, client=None):
        self.s, self.db = settings, db
        self.http = HTTP(settings, client)
        self.fixture = FixtureSource(db)
        self.adapters = (
            [self.fixture]
            if settings.data_mode == "fixtures"
            else [
                IGDBSource(settings, self.http),
                SteamSource(settings, self.http, db),
                SteamSource(settings, self.http, db, True),
            ]
        )

    def specs(self):
        return [
            {
                "name": a.name,
                "capabilities": a.capabilities,
                "auth_requirements": a.auth_requirements,
                "supported_fields": ["games", "documents"],
                "estimated_cost": "unknown" if self.s.data_mode == "live" else "no external request",
                "timeout": 15,
                "cache_ttl": min(self.cache_ttl(cap) for cap in a.capabilities),
                "cache_ttl_by_capability": {cap: self.cache_ttl(cap) for cap in a.capabilities},
                **self.db.get("tool_health", a.name, {"health_status": "unknown", "last_checked_at": None}),
            }
            for a in self.adapters
        ]

    def cache_key(self, provider, cap, args):
        return hashlib.sha256(
            json.dumps(
                [
                    "sources-0102-ttl-contract",
                    self.s.data_mode,
                    provider,
                    cap,
                    args,
                    self.s.max_reviews_per_game if cap == "get_reviews" else None,
                    self.cache_ttl(cap),
                ],
                sort_keys=True,
            ).encode()
        ).hexdigest()

    def estimated_external(self, action):
        parsed = Action.model_validate(action)
        cap, args = parsed.tool_name, parsed.arguments.model_dump(exclude_none=True)
        for adapter in self.adapters:
            if cap not in adapter.capabilities:
                continue
            cached = self.db.get("cache", self.cache_key(adapter.name, cap, args))
            if cached and time.time() < cached["expires"]:
                return 0
            # The first capable adapter is attempted before a later cache can be used.
            return 0 if self.s.data_mode == "fixtures" else {"get_game_details": 2, "get_reviews": 3}.get(cap, 1)
        return 0

    def cache_ttl(self, cap):
        return {
            "search_games": self.s.source_catalog_ttl_seconds,
            "get_game_details": self.s.source_details_ttl_seconds,
            "get_reviews": self.s.source_reviews_ttl_seconds,
        }.get(cap, 600)

    async def call(self, action, gate, event=lambda *a: None, fault="none", force=False, provider=None):
        action = Action.model_validate(action)
        cap, args = action.tool_name, action.arguments.model_dump(exclude_none=True)
        gate.use("tool_calls")
        adapters = [a for a in self.adapters if cap in a.capabilities and (not provider or a.name == provider)]
        if not adapters:
            return ToolResult(
                status="unavailable",
                provider="none",
                capability=cap,
                error_code="no_equivalent_capability",
                provenance_type=self.s.data_mode,
            )
        last = None
        for a in adapters:
            event(
                "tool_started",
                f"调用 {a.name} / {cap}：按能力、模式、缓存与健康状态路由。",
                {"provider": a.name, "capability": cap, "arguments": args},
            )
            key = self.cache_key(a.name, cap, args)
            cache = self.db.get("cache", key)
            health = self.db.get("tool_health", a.name, {})
            if cache and not force and fault == "none" and time.time() < cache["expires"]:
                result = ToolResult.model_validate(cache["result"])
                result.from_cache = True
                stats = gate.ctx.setdefault("savings", {})
                stats["source_cache_hits"] = stats.get("source_cache_hits", 0) + 1
            else:
                try:
                    if fault != "none" and self.s.data_mode == "fixtures" and cap != "search_games":
                        raise SourceFailure(
                            "rate_limited" if fault == "rate_limited" else "unavailable",
                            60 if fault == "rate_limited" else None,
                        )
                    if not force and time.time() < health.get("blocked_until", 0):
                        raise SourceFailure(
                            health.get("error_code", "unavailable"), health["blocked_until"] - time.time()
                        )
                    data = await a.call(cap, args, gate)
                    if cap == "get_game_details" and a.name == "igdb":
                        for game in data.get("games", []):
                            try:
                                doc = await SteamStoreSource(self.s, self.http).enrich(game, gate)
                                if doc:
                                    data.setdefault("documents", []).append(doc)
                            except SourceFailure as exc:
                                data.setdefault("source_warnings", []).append("steam_store:" + exc.code)
                    result = ToolResult(
                        provider=a.name,
                        capability=cap,
                        data=data,
                        source_refs=[d["id"] for d in data.get("documents", [])],
                        provenance_type="synthetic" if self.s.data_mode == "fixtures" else "live",
                    )
                    # Partial source retrieval must remain retryable, not become a fresh full result.
                    if not data.get("source_warnings") and not data.get("sampling", {}).get("errors"):
                        from .reuse import put as cache_put
                        cache_put(self.db, "cache", key, {"result": result.model_dump()}, self.cache_ttl(cap))
                    self.db.put(
                        "tool_health",
                        a.name,
                        {"health_status": "available", "last_checked_at": now(), "error_code": None},
                    )
                except SourceFailure as e:
                    status = e.code if e.code in ["requires_auth", "rate_limited"] else "unavailable"
                    result = ToolResult(
                        status=status,
                        provider=a.name,
                        capability=cap,
                        error_code=e.code,
                        retry_after=e.retry_after,
                        provenance_type="synthetic" if self.s.data_mode == "fixtures" else "live",
                        warnings=["未使用跨能力或合成数据替代。"],
                    )
                    if fault == "none":
                        self.db.put(
                            "tool_health",
                            a.name,
                            {
                                "health_status": status,
                                "last_checked_at": now(),
                                "error_code": e.code,
                                "blocked_until": time.time() + (e.retry_after or 30),
                            },
                        )
            event(
                "tool_finished",
                f"{a.name}：{result.status}" + ("（缓存）" if result.from_cache else ""),
                result.model_dump(),
            )
            for g in result.data.get("games", []):
                self.db.put("game", g["id"], g)
            if result.status == "available":
                return result
            last = result
        return last

    async def close(self):
        await self.http.client.aclose()
