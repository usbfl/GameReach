"""Read-only player imports. All inferences remain drafts until explicit confirmation."""

import asyncio
import httpx
import csv
import io
import json
import re
from collections import defaultdict
from typing import Literal
from fastapi import HTTPException
from pydantic import Field
from sqlalchemy import select
from .schemas import Strict, Preference
from .db import Record, uid, now
from .tools import Gate, SourceFailure, BudgetExceeded, IGDBSource
from .dna.projection import current_features
from .dna.ranking import describe
from .interests import InterestText, summarize, collect_sources, card_identity


class LibraryGame(Strict):
    platform: Literal["steam", "gog", "epic", "itch", "manual", "fixture"] = "steam"
    external_id: str = Field("", max_length=100)
    name: str = Field(min_length=1, max_length=200)
    game_id: str | None = Field(None, max_length=100)
    playtime_minutes: int | None = Field(None, ge=0, le=100_000_000)
    recent_minutes: int | None = Field(None, ge=0, le=20160)
    favorite: bool = False
    collections: list[str] = Field(default_factory=list, max_length=30)
    reaction: Literal["neutral", "like", "dislike", "exclude"] = "neutral"


class FileImport(Strict):
    format: Literal["json", "csv"]
    content: str = Field(max_length=500_000)


class SteamImport(Strict):
    steam_id: str = Field(max_length=150)


class Selection(Strict):
    selected_ids: list[str] = Field(max_length=2000)
    reactions: dict[str, Literal["neutral", "like", "dislike", "exclude"]] = Field(default_factory=dict)


class ConfirmDraft(Strict):
    interest_cards: list[InterestText] = Field(default_factory=list, max_length=12)
    preference_keys: list[str] = Field(default_factory=list, max_length=16)
    topic_names: list[str] = Field(default_factory=list, max_length=12)
    style: Literal["plain", "analytical", "vivid"] = "plain"


def steam_id(value):
    value = value.strip()
    m = re.fullmatch(r"https://steamcommunity\.com/profiles/(\d{17})/?", value)
    value = m[1] if m else value
    if not re.fullmatch(r"7656119\d{10}", value):
        raise ValueError("请填写自己的 17 位 SteamID64 或 /profiles/数字 链接；自定义 /id/ 昵称暂不支持。")
    return value


def parse_file(body):
    if body.format == "json":
        try:
            rows = json.loads(body.content)
        except ValueError:
            raise ValueError("JSON 格式不正确，请使用下载的模板。")
        if isinstance(rows, dict):
            rows = rows.get("games")
    else:
        rows = list(csv.DictReader(io.StringIO(body.content.lstrip("\ufeff"))))
        for r in rows:
            for k in ["playtime_minutes", "recent_minutes"]:
                if k in r:
                    r[k] = None if r[k].strip() == "" else r[k]
            if "collections" in r:
                r["collections"] = [x.strip() for x in r["collections"].split("|") if x.strip()]
            if "game_id" in r and not r["game_id"]:
                r["game_id"] = None
    if not isinstance(rows, list) or not 1 <= len(rows) <= 2000:
        raise ValueError("一次导入 1–2000 款游戏；JSON 使用 games 数组，CSV 使用模板表头。")
    output, seen = [], set()
    for i, row in enumerate(rows):
        try:
            g = LibraryGame.model_validate(row).model_dump()
        except ValueError:
            raise ValueError(f"第 {i + 1} 款字段不正确；时长单位为分钟，收藏夹使用数组或 CSV 的 | 分隔。")
        if any(len(c) > 100 for c in g["collections"]):
            raise ValueError("分类名称最多 100 字符。")
        key = (g["platform"], g["external_id"]) if g["external_id"] else ("row", str(i))
        if key in seen:
            raise ValueError(f"第 {i + 1} 款外部 ID 重复，请先合并，避免重复计算时长。")
        seen.add(key)
        output.append(g)
    return output


GENRE_NAMES = {
    "Role-playing (RPG)": "角色扮演",
    "Strategy": "策略",
    "Tactical": "战术",
    "Puzzle": "解谜",
    "Adventure": "冒险",
    "Indie": "独立",
    "Shooter": "射击",
    "Simulator": "模拟",
    "Turn-based strategy (TBS)": "回合制策略",
    "Hack and slash/Beat 'em up": "动作",
    "Platform": "平台跳跃",
    "Sport": "体育",
    "Racing": "竞速",
}


def signal(g):
    from .interest_features import attention

    return attention(g)["weight"]


def infer(db, dataset, games):
    topics, votes = defaultdict(list), defaultdict(list)
    unique_games = {}
    for game in games:
        key = game.get("game_id") or (game["platform"], game.get("external_id") or game["name"].casefold())
        prior = unique_games.get(key)
        if (
            prior is None
            or game.get("reaction") in {"dislike", "exclude"}
            or (prior.get("reaction") not in {"dislike", "exclude"} and signal(game) > signal(prior))
        ):
            unique_games[key] = game
    usable = sorted(unique_games.values(), key=lambda g: (-signal(g), g["id"]))
    evidence = []
    for g in usable:
        weight = signal(g)
        if weight == 0:
            continue
        catalog = db.get("game", g.get("game_id"), {})
        if catalog.get("dataset_id") != dataset:
            continue
        evidence.append(
            {
                "name": g["name"],
                "hours": None if g["playtime_minutes"] is None else round(g["playtime_minutes"] / 60, 1),
                "favorite": g["favorite"],
                "reaction": g["reaction"],
                "game_id": catalog["id"],
            }
        )
        for tag in catalog.get("tags", []):
            topics[GENRE_NAMES.get(tag, tag)].append((weight, g["name"]))
        for key, f in current_features(db, dataset, catalog["id"]).items():
            if f.get("value") is not None and f.get("evidence_status") == "supported" and f.get("freshness") != "stale":
                votes[key].append((weight, f["value"], g["name"]))
    preferences, reasons = [], {}
    for key, records in votes.items():
        groups = defaultdict(list)
        for weight, value, name in records:
            groups[json.dumps(value, sort_keys=True)].append((weight, name))
        target, support = max(groups.items(), key=lambda x: sum(v[0] for v in x[1]))
        share = sum(x[0] for x in support) / sum(x[0] for x in records)
        if share < 0.65:
            continue
        preferences.append(Preference(feature_key=key, target=json.loads(target), weight=1).model_dump())
        reasons[key] = (
            "你选中的 " + "、".join(x[1] for x in support[:3]) + " 有已核验的这一玩法；暂推测你也喜欢它，可取消。"
        )
    ranked = sorted(topics.items(), key=lambda x: (-sum(v[0] for v in x[1]), x[0]))[:6]
    topic_rows = [
        {
            "name": k,
            "games": list(dict.fromkeys(n for _, n in v))[:4],
            "basis": "选中游戏的类型与游玩投入；只用于发现候选",
        }
        for k, v in ranked
    ]
    style = (
        "analytical"
        if any(x["name"] in ["策略", "解谜", "战术", "回合制策略"] for x in topic_rows[:2])
        else "vivid"
        if any(x["name"] in ["冒险", "角色扮演"] for x in topic_rows[:2])
        else "plain"
    )
    return {
        "preferences": preferences[:6],
        "preference_reasons": reasons,
        "preference_descriptions": {p["feature_key"]: describe(p["feature_key"], p["target"]) for p in preferences[:6]},
        "topics": topic_rows,
        "style": style,
        "style_reason": "仅依据暂定的游戏兴趣建议一种阅读风格，不推断人格；可随时更改。",
        "evidence": evidence[:12],
        "mapped_count": sum(bool(g.get("game_id")) for g in games),
        "selected_count": len(games),
        "notes": [
            "时长不等于喜欢；未游玩不等于讨厌。",
            "不喜欢的游戏不会反推所有相反机制；请在探索输入中具体说明讨厌什么。",
            "类型线索不等于机制画像；只有已核验机制才能进入机制偏好。",
        ],
    }


def install_player_routes(app, db, settings, registry):
    dataset = "synthetic-v1" if settings.data_mode == "fixtures" else "live"

    def get_import(iid):
        obj = db.get("player_import", iid)
        if not obj or obj["dataset_id"] != dataset:
            raise HTTPException(404, "找不到当前数据模式的导入记录")
        return obj

    def save(games, source):
        if dataset == "live" and any(g["platform"] == "fixture" for g in games):
            raise ValueError("虚构示例只能在合成数据模式导入。")
        catalog = [g for g in db.list("game") if g.get("dataset_id") == dataset]
        for i, g in enumerate(games):
            matches = [
                c
                for c in catalog
                if (
                    g.get("game_id") == c["id"]
                    and (
                        g["platform"] == "fixture"
                        or (g["external_id"] and str(c.get("external_ids", {}).get(g["platform"])) == g["external_id"])
                    )
                )
                or (g["external_id"] and str(c.get("external_ids", {}).get(g["platform"])) == g["external_id"])
            ]
            g["game_id"] = matches[0]["id"] if len(matches) == 1 else None
            g["id"] = f"entry-{i}"
        obj = {"id": uid("import-"), "source": source, "dataset_id": dataset, "created_at": now(), "games": games}
        db.put("player_import", obj["id"], obj)
        return obj

    @app.post("/api/player/import/file")
    async def file_import(body: FileImport):
        return save(parse_file(body), "用户文件")

    @app.post("/api/player/import/steam")
    async def import_steam(body: SteamImport):
        sid = steam_id(body.steam_id)
        if dataset != "live":
            raise HTTPException(409, "演示模式不访问个人账号；请使用虚构示例文件，或切换 DATA_MODE=live。")
        if not settings.steam_web_api_key.get_secret_value().strip():
            raise HTTPException(
                409, "【由你配置】在后端 .env 填写 STEAM_WEB_API_KEY；无需在页面输入 Key 或 Steam 密码。"
            )
        key = settings.steam_web_api_key.get_secret_value().strip()
        if re.search(r"https?://|steamcommunity\.com|[/\s]", key, re.I) or re.fullmatch(r"\d{17}", key):
            raise HTTPException(
                409,
                "STEAM_WEB_API_KEY 填成了主页链接、账号 ID 或含空白的内容。请从 https://steamcommunity.com/dev/apikey 复制 Key，填写到项目根目录 .env 后重启后端；页面仅填写 SteamID64 或数字主页链接。",
            )
        gate = Gate(settings)
        try:
            obj = await asyncio.wait_for(
                registry.http.request(
                    gate,
                    "GET",
                    "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/",
                    params={
                        "key": key,
                        "input_json": json.dumps(
                            {"steamid": sid, "include_appinfo": True, "include_played_free_games": True}
                        ),
                    },
                    attempts=0,
                ),
                timeout=20,
            )
        except (TimeoutError, httpx.TransportError):
            raise HTTPException(504, "Steam 连接超时或网络连接失败。请检查后端所在电脑的网络与代理后重试。") from None
        except SourceFailure as e:
            raise HTTPException(
                502,
                {
                    "code": e.code,
                    "message": {
                        "requires_auth": "Steam 拒绝了密钥。请核对官方页面的 Key，更新根目录 .env 并重启后端。",
                        "read_timeout": "Steam 网络读取超时，请稍后重试或使用文件导入。",
                        "connect_timeout": "Steam 网络连接超时，请稍后重试或使用文件导入。",
                        "network_error": "Steam 网络连接失败，请稍后重试或使用文件导入。",
                        "unavailable": "Steam 暂时不可用、连接失败或等待超时。请检查后端所在电脑的网络与代理，稍后重试。",
                        "proxy_blocked": "网络代理阻止了 Steam 请求。请检查后端所在电脑的网络与代理，或使用文件导入。",
                    }.get(e.code, "Steam 游戏库读取失败（" + e.code + "），请稍后重试或使用文件导入。"),
                },
            )
        response = obj.get("response") if isinstance(obj, dict) else None
        if not isinstance(response, dict) or "game_count" not in response:
            raise HTTPException(
                409, "Steam 未提供可读取的游戏库；请检查“游戏详情”和游戏时长的隐私设置。未把它当成空库。"
            )
        if response["game_count"] == 0:
            return save([], "Steam 只读导入（空库）")
        rows = response.get("games")
        if not isinstance(rows, list) or not rows:
            raise HTTPException(502, "Steam 响应缺少游戏明细，请稍后重试。")
        if len(rows) > 2000:
            raise HTTPException(422, "当前最多导入 2000 款；请用文件选择特定收藏夹或分类。")
        try:
            games = [
                LibraryGame(
                    name=r.get("name") or f"Steam App {r['appid']}",
                    external_id=str(r["appid"]),
                    playtime_minutes=r.get("playtime_forever"),
                    recent_minutes=r.get("playtime_2weeks"),
                ).model_dump()
                for r in rows
            ]
        except (ValueError, KeyError, TypeError):
            raise HTTPException(502, "Steam 返回了无法识别的游戏字段；没有保存不完整的结果。")
        return save(games, "Steam 只读导入")

    @app.get("/api/player/imports")
    async def imports():
        return {
            "items": [
                {"id": x["id"], "source": x["source"], "created_at": x["created_at"], "count": len(x["games"])}
                for x in db.list("player_import")
                if x["dataset_id"] == dataset
            ]
        }

    @app.get("/api/player/imports/{iid}")
    async def read_import(iid: str):
        return get_import(iid)

    @app.delete("/api/player/imports/{iid}")
    async def delete_import(iid: str):
        get_import(iid)
        with db.Session.begin() as s:
            s.delete(s.get(Record, ("player_import", iid)))
            for row in s.scalars(select(Record).where(Record.kind == "player_draft")):
                if row.data.get("import_id") == iid:
                    s.delete(row)
        return {"deleted": True, "note": "原始导入与未应用草稿已删除；已确认偏好可单独撤销。"}

    @app.post("/api/player/imports/{iid}/analyze")
    async def analyze(iid: str, body: Selection):
        obj = get_import(iid)
        if not body.selected_ids or len(set(body.selected_ids)) != len(body.selected_ids):
            raise ValueError("请至少选择一款游戏，且不要重复选择。")
        allowed = {g["id"] for g in obj["games"]}
        if not set(body.selected_ids).issubset(allowed) or not set(body.reactions).issubset(allowed):
            raise ValueError("选择中包含当前导入以外的游戏。")
        chosen = [
            dict(g, reaction=body.reactions.get(g["id"], g["reaction"]))
            for g in obj["games"]
            if g["id"] in body.selected_ids
        ]
        gate, warnings = Gate(settings), []
        # Resolve only a bounded, high-signal subset via authoritative external IDs, never name similarity.
        unresolved = [
            g
            for g in sorted(chosen, key=lambda g: -signal(g))
            if not g.get("game_id")
            and g["platform"] == "steam"
            and signal(g) > 0
            and re.fullmatch(r"\d+", g["external_id"])
        ][:30]
        if dataset == "live" and unresolved:
            adapter = next(a for a in registry.adapters if isinstance(a, IGDBSource))
            try:
                mapping = await adapter.resolve_steam([g["external_id"] for g in unresolved], gate)
                for g in unresolved:
                    if g["external_id"] in mapping:
                        game = mapping[g["external_id"]]
                        db.put("game", game["id"], game)
                        g["game_id"] = game["id"]
            except (SourceFailure, BudgetExceeded) as e:
                warnings.append(
                    "部分游戏未能匹配 IGDB："
                    + (e.code if isinstance(e, SourceFailure) else "预算到达上限")
                    + "；不会凭游戏名猜测机制。"
                )
        draft = infer(db, dataset, chosen) | {
            "id": uid("draft-"),
            "import_id": iid,
            "dataset_id": dataset,
            "status": "pending",
            "profile_version": db.get("profile", "default")["version"],
            "created_at": now(),
            "warnings": warnings,
            "budget": gate.ctx["budget"],
        }
        # Preserve the chosen scope and explicit exclusions for later memory summaries.
        resolved_ids = {g["id"]: g["game_id"] for g in chosen if g.get("game_id")}
        for imported in obj["games"]:
            if imported["id"] in resolved_ids:
                imported["game_id"] = resolved_ids[imported["id"]]
        obj["interest_games"] = chosen
        db.put("player_import", iid, obj)
        sources = [s for s in collect_sources(db, dataset) if s["kind"] != "library" or s["id"].startswith(iid + ":")]
        draft["interest_summary"] = await summarize(settings, registry.http, sources, gate=gate, db=db)
        db.put("player_draft", draft["id"], draft)
        return draft

    @app.post("/api/player/drafts/{did}/confirm")
    async def confirm(did: str, body: ConfirmDraft):
        # The revision/profile update is atomic and rejects stale or repeated confirmation.
        if len(body.interest_cards) > settings.interest_max_cards:
            raise HTTPException(422, f"最多确认{settings.interest_max_cards}张卡片。")
        with db.Session.begin() as session:
            if db.engine.dialect.name == "sqlite":
                from sqlalchemy import text

                session.execute(text("BEGIN IMMEDIATE"))
            row = session.get(Record, ("player_draft", did))
            if not row or row.data.get("dataset_id") != dataset:
                raise HTTPException(404, "偏好草稿不存在")
            d = dict(row.data)
            p = session.get(Record, ("profile", "default"))
            before = dict(p.data)
            if d["status"] != "pending" or before["version"] != d["profile_version"]:
                raise HTTPException(409, "偏好已经改变或草稿已应用，请重新分析后确认。")
            available = {x["feature_key"]: x for x in d["preferences"]}
            if not set(body.preference_keys).issubset(available) or not set(body.topic_names).issubset(
                {t["name"] for t in d["topics"]}
            ):
                raise ValueError("只能确认草稿中实际存在的兴趣。")
            allowed_sources = {sid for c in d.get("interest_summary", {}).get("cards", []) for sid in c["source_ids"]}
            if any(not set(c.source_ids).issubset(allowed_sources) for c in body.interest_cards):
                raise ValueError("卡片包含当前草稿之外的来源。")
            originals = d.get("interest_summary", {}).get("cards", [])
            for card in body.interest_cards:
                if (
                    not settings.interest_min_games
                    <= len(set(card.game_keys))
                    == len(card.game_keys)
                    <= settings.interest_max_games
                ):
                    raise ValueError(
                        f"每类卡片须包含{settings.interest_min_games}–{settings.interest_max_games}款不同游戏。"
                    )
                if not any(
                    set(card.game_keys).issubset(original.get("game_keys", []))
                    and set(card.source_ids).issubset(original["source_ids"])
                    and all(
                        set(g.get("source_ids", [])).intersection(card.source_ids)
                        for g in original.get("related_games", [])
                        if g["key"] in card.game_keys
                    )
                    for original in originals
                ):
                    raise ValueError("只能确认当前分类草稿内的游戏成员。")
            prefs = {x["feature_key"]: x for x in before["preferences"]}
            # Existing explicit interests win. A user can adjust them in the normal editor.
            for k in body.preference_keys:
                prefs.setdefault(k, available[k])
            after = before | {
                "version": before["version"] + 1,
                "preferences": list(prefs.values()),
                "interest_topics": list(dict.fromkeys(body.topic_names))
                if body.topic_names
                else before.get("interest_topics", []),
                "reading_style": body.style,
            }
            rid = uid("rev-")
            revision = {
                "id": rid,
                "before": before,
                "after": after,
                "reason": "用户确认游戏库偏好草稿",
                "status": "confirmed",
                "created_at": now(),
            }
            card_ids = []
            existing = {
                card_identity(r.data)
                for r in session.scalars(select(Record).where(Record.kind == "interest_card"))
                if r.data["dataset_id"] == dataset
            }
            source_labels = {x["id"]: x for c in d.get("interest_summary", {}).get("cards", []) for x in c["sources"]}
            for c in body.interest_cards:
                if card_identity(c.model_dump()) in existing:
                    continue
                cid = uid("interest-")
                card_ids.append(cid)
                existing.add(card_identity(c.model_dump()))
                session.add(
                    Record(
                        kind="interest_card",
                        id=cid,
                        data=c.model_dump()
                        | {
                            "id": cid,
                            "dataset_id": dataset,
                            "version": 1,
                            "status": "confirmed",
                            "created_at": now(),
                            "generator": d["interest_summary"]["generator"],
                            "source_revision": rid,
                            "card_format": "game_group_v2",
                            "related_games": list(
                                {
                                    g["key"]: g
                                    for original in originals
                                    for g in original.get("related_games", [])
                                    if g["key"] in c.game_keys
                                }.values()
                            ),
                            "sources": [source_labels[sid] for sid in c.source_ids],
                        },
                    )
                )
            revision["interest_card_ids"] = card_ids
            session.add(Record(kind="revision", id=rid, data=revision))
            p.data = after
            row.data = d | {"status": "confirmed", "revision_id": rid}
        return {
            "profile": after,
            "revision_id": rid,
            "query_seed": body.interest_cards[0].text
            if body.interest_cards
            else ("我想找" + "、".join(body.topic_names[:4]) + "类游戏。" if body.topic_names else ""),
        }
