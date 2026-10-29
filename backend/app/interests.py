"""Bounded, source-linked interest summaries. Suggestions never become facts automatically."""

import asyncio
import copy
import json
import time
import httpx
from fastapi import HTTPException
from pydantic import Field
from sqlalchemy import select
from .db import Record, Watch, now, uid
from .retrieval import Retrieval
from .schemas import Strict
from .tools import Gate, SourceFailure, BudgetExceeded
from .interest_features import attention, themes, profile, verified_themes, canonical_theme
from .reuse import digest, identity, get as get_reuse, put as put_reuse


class InterestText(Strict):
    title: str = Field(min_length=1, max_length=24)
    text: str = Field(min_length=12, max_length=900)
    source_ids: list[str] = Field(min_length=1, max_length=24)
    theme_ids: list[str] = Field(default_factory=list, max_length=6)
    game_keys: list[str] = Field(default_factory=list, min_length=2, max_length=12)


class InterestSummary(Strict):
    cards: list[InterestText] = Field(max_length=12)


class GenerateInterests(Strict):
    regroup: bool = False
    text: str = Field("", max_length=3000)


class EditInterest(Strict):
    version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=24)
    text: str = Field(min_length=12, max_length=900)
    confirm: bool = False
    game_keys: list[str] | None = Field(None, min_length=2, max_length=12)


def dataset_for(settings):
    return "synthetic-v1" if settings.data_mode == "fixtures" else "live"


def collect_sources(db, dataset, text=""):
    sources = []
    if text.strip():
        sources.append({"id": "current", "kind": "input", "label": "本次输入", "text": text.strip()})
    seen = {text.strip()}
    for item in Retrieval(db).history(dataset, text):
        if item["text"].strip() and item["text"].strip() not in seen:
            sources.append(item)
            seen.add(item["text"].strip())
    confirmed = sorted(
        [c for c in db.list("interest_card") if c["dataset_id"] == dataset and c["status"] == "confirmed"],
        key=lambda c: c["created_at"],
        reverse=True,
    )[:6]
    sources += [
        {"id": c["id"], "kind": "confirmed_interest", "label": "已确认兴趣 · " + c["title"], "text": c["text"]}
        for c in confirmed
    ]
    with db.Session() as session:
        watches = [w.data for w in session.scalars(select(Watch))]
    # Latest explicit reaction wins; a later dislike must not count as a like.
    latest = {f["game_id"]: f for f in db.get("profile", "default", {}).get("feedback", [])}
    for gid, f in sorted(latest.items(), key=lambda x: x[1].get("created_at", ""))[-30:]:
        g = db.get("game", gid, {})
        if g.get("dataset_id") == dataset:
            sources.append(
                {
                    "id": "feedback:" + gid,
                    "kind": "feedback",
                    "label": "你对 " + g["name"] + " 的反馈",
                    "text": f["reaction"] + "；" + f.get("reason", ""),
                    "reaction": f["reaction"],
                    "tags": g.get("tags", [])[:16],
                    "game_key": gid,
                    "game_name": g["name"],
                    "game_id": gid,
                    "platforms": g.get("platforms", []),
                    "description": g.get("summary", "")[:1000],
                }
            )
    for w in sorted(watches, key=lambda x: x.get("created_at", ""))[-30:]:
        g = db.get("game", w["game_id"], {})
        if (
            w.get("enabled", True)
            and g.get("dataset_id") == dataset
            and latest.get(w["game_id"], {}).get("reaction") != "dislike"
        ):
            sources.append(
                {
                    "id": w["id"],
                    "kind": "watch",
                    "label": "感兴趣的游戏 · " + g["name"],
                    "text": "用户加入兴趣清单；并不代表玩过或喜欢全部玩法。" + w.get("reason", ""),
                    "watch": True,
                    "tags": g.get("tags", [])[:16],
                    "game_key": w["game_id"],
                    "game_name": g["name"],
                    "game_id": w["game_id"],
                    "platforms": g.get("platforms", []),
                    "description": g.get("summary", "")[:1000],
                }
            )
    # Analyze the latest copy of each imported game, with explicit exclusions respected.
    seen_games = set()
    blocked = {gid for gid, f in latest.items() if f.get("reaction") in {"dislike", "exclude"}}
    imports = sorted(db.list("player_import"), key=lambda x: x["created_at"], reverse=True)
    for imp in imports:
        if imp.get("dataset_id") != dataset:
            continue
        for g in imp.get("interest_games", imp["games"]):
            identity = (g["platform"], g.get("external_id") or g["name"])
            if identity in seen_games:
                continue
            seen_games.add(identity)
            if (
                g.get("reaction") in ("exclude", "dislike")
                or latest.get(g.get("game_id"), {}).get("reaction") == "dislike"
            ):
                blocked.add(g.get("game_id") or "name:" + " ".join(g["name"].casefold().split()))
                continue
            catalog = db.get("game", g.get("game_id"), {}) if g.get("game_id") else {}
            sources.append(
                {
                    "id": imp["id"] + ":" + g["id"],
                    "kind": "library",
                    "game_key": g.get("game_id") or "name:" + " ".join(g["name"].casefold().split()),
                    "game_name": g["name"],
                    "game_id": g.get("game_id"),
                    "platforms": [g["platform"]],
                    "collections": g.get("collections", []),
                    "playtime_minutes": g.get("playtime_minutes"),
                    "favorite": g.get("favorite", False),
                    "reaction": latest.get(g.get("game_id"), {}).get("reaction", g.get("reaction", "neutral")),
                    "description": catalog.get("summary", "")[:1000] if catalog.get("dataset_id") == dataset else "",
                    "label": "游戏资料 · " + g["name"],
                    "text": f"时长（分钟）{g.get('playtime_minutes')}；收藏 {g.get('favorite', False)}；感受 {g.get('reaction', 'neutral')}",
                    "tags": catalog.get("tags", [])[:16] if catalog.get("dataset_id") == dataset else [],
                }
            )
    sources = [s for s in sources if s.get("game_key") not in blocked or s["kind"] == "feedback"]
    for source in sources:
        if source.get("game_id"):
            source["verified_themes"] = verified_themes(db, dataset, source["game_id"])
    return sources[:240]


def draft_sources(draft):
    out = []
    for i, t in enumerate(draft["topics"]):
        out.append(
            {
                "id": f"topic-{i}",
                "kind": "library",
                "label": "游戏资料 · " + "、".join(t["games"]),
                "text": "可能的类型兴趣；时长不等于喜欢。",
                "tags": [t["name"]],
            }
        )
    for i, p in enumerate(draft["preferences"]):
        out.append(
            {
                "id": f"mechanic-{i}",
                "kind": "library",
                "label": "所选游戏的已核验玩法",
                "text": draft["preference_descriptions"][p["feature_key"]]
                + "；"
                + draft["preference_reasons"][p["feature_key"]],
            }
        )
    return out


def game_sources(sources):
    """Deduplicate games across library/watch/feedback. Titles are server-owned, never invented."""
    games = {}
    blocked = {
        s.get("game_key")
        for s in sources
        if s.get("reaction") in {"dislike", "exclude"}
        or (s["kind"] == "feedback" and s["text"].startswith(("dislike", "exclude")))
    }
    for source in sources:
        if (
            source.get("game_key") in blocked
            or not source.get("game_key")
            or (source["kind"] == "feedback" and not source["text"].startswith(("like", "later")))
        ):
            continue
        key = source["game_key"]
        game = games.setdefault(
            key,
            {
                "key": key,
                "name": source["game_name"],
                "game_id": source.get("game_id"),
                "platforms": [],
                "source_ids": [],
                "tags": [],
                "collections": [],
                "description": "",
                "verified_themes": [],
                "reaction": "neutral",
            },
        )
        for field in ("tags", "platforms", "collections"):
            game[field] = list(dict.fromkeys(game[field] + source.get(field, [])))
        game["source_ids"].append(source["id"])
        game["description"] = game["description"] or source.get("description", "")
        game["verified_themes"] = list(
            {t["id"]: t for t in game["verified_themes"] + source.get("verified_themes", [])}.values()
        )
        game["playtime_minutes"] = max(game.get("playtime_minutes") or 0, source.get("playtime_minutes") or 0) or None
        game["favorite"] = game.get("favorite", False) or source.get("favorite", False)
        game["watch"] = game.get("watch", False) or source.get("watch", False)
        if source.get("reaction") == "like" or (source["kind"] == "feedback" and source["text"].startswith("like")):
            game["reaction"] = "like"
        game["attention"] = attention(game)
    return games


def partition(rows, minimum=3, maximum=7):
    """Balance groups without fabricating members; impossible residuals stay unassigned."""
    if len(rows) < minimum:
        return []
    count = (len(rows) + maximum - 1) // maximum
    if len(rows) // count < minimum:
        return [rows[:maximum]]
    size, extra = divmod(len(rows), count)
    offset, out = 0, []
    for index in range(count):
        n = size + (index < extra)
        out.append(rows[offset : offset + n])
        offset += n
    return out


def rule_summary(sources, settings=None, retained=()):
    from .player import GENRE_NAMES

    minimum, maximum, limit = (
        (settings.interest_min_games, settings.interest_max_games, settings.interest_max_cards)
        if settings
        else (3, 7, 6)
    )
    games = game_sources(sources)
    categories = {}
    for game in games.values():
        for theme in themes(game):
            entry = categories.setdefault(theme["id"], {"theme": theme, "games": []})
            entry["games"].append(game)
    cards = []
    for key, entry in sorted(
        categories.items(),
        key=lambda x: (x[0].startswith("catalog:"), -sum(g["attention"]["weight"] for g in x[1]["games"]), x[0]),
    ):
        occupied = {
            k for c in retained if key in c.get("theme_ids", [canonical_theme(c["title"])]) for k in c["game_keys"]
        }
        available = sorted(
            [g for g in entry["games"] if g["key"] not in occupied], key=lambda g: (-g["attention"]["weight"], g["key"])
        )
        # Two broad catalog labels on almost the same games add no distinct gameplay theme.
        if key.startswith("catalog:") and any(
            len(set(g["key"] for g in available) & set(c["game_keys"]))
            / max(1, len(set(g["key"] for g in available) | set(c["game_keys"])))
            >= 0.85
            for c in cards
        ):
            continue
        for group in partition(available, minimum, maximum):
            if len(cards) >= limit - len(retained):
                break
            theme = entry["theme"]
            label = GENRE_NAMES.get(theme["label"], theme["label"])
            cards.append(
                dict(
                    title=label[:24],
                    theme_ids=[key],
                    text=f"这组游戏的共同线索是“{label}”。依据为目录标签、用户分类或已有核验资料；目录线索尚未经过评论核验。可继续了解这个主题，时长和收藏只表示关注程度，不证明喜欢其中全部玩法。",
                    source_ids=[g["source_ids"][0] for g in group],
                    game_keys=[g["key"] for g in group],
                )
            )
    return InterestSummary(cards=cards)


async def summarize(settings, http, sources, gate=None, db=None, regroup=False):
    gate = gate or Gate(settings)
    if db is None or not settings.reuse_enabled:
        return await _summarize(settings, http, sources, gate)
    games = game_sources(sources)
    context = [s for s in sources if not s.get("game_key")]
    base = digest(
        [
            "group-cache-2",
            identity(settings),
            sorted([s for s in context if s["kind"] != "confirmed_interest"], key=lambda s: s["id"]),
            [settings.interest_min_games, settings.interest_max_games, settings.interest_max_cards],
        ]
    )
    fingerprints = {
        key: digest(sorted([s for s in sources if s.get("game_key") == key], key=lambda s: s["id"])) for key in games
    }
    key = digest([base, fingerprints])
    cached = None if regroup else get_reuse(db, "interest_summary_cache", key)
    if cached:
        gate.guard()
        gate.ctx["budget"]["active_seconds"] += time.monotonic() - gate.started
        result = copy.deepcopy(cached["result"])
        return result | {
            "budget": gate.ctx["budget"],
            "model_usage": None,
            "cache": {"hit": True, "reused_cards": len(result["cards"]), "processed_games": 0},
            "message": "资料没有变化，已复用上次整理结果。",
        }
    previous = None if regroup else get_reuse(db, "interest_summary_latest", base)
    retained = []
    if previous:
        retained = [
            copy.deepcopy(c)
            for c in previous["cards"]
            if all(fingerprints.get(k) == previous["fingerprints"].get(k) for k in c["game_keys"])
        ]
    retained_keys = {k for c in retained for k in c["game_keys"]}
    remaining = sources  # A retained membership only occupies that theme, never another theme.
    # No call when all six slots already have unchanged, source-backed groups.
    if len(retained) >= settings.interest_max_cards:
        gate.guard()
        gate.ctx["budget"]["active_seconds"] += time.monotonic() - gate.started
        result = {
            "cards": [],
            "budget": gate.ctx["budget"],
            "generator": "cache",
            "model_usage": None,
            "message": f"已有{settings.interest_max_cards}组未变化卡片；新增游戏保留待归类，可选择全部重新分组。",
        }
    else:
        result = await _summarize(settings, http, remaining, gate, retained)
    if retained and not result["cards"]:
        result["generator"] = previous.get("generator", result["generator"])
    result["cards"] = retained + result["cards"][: settings.interest_max_cards - len(retained)]
    assigned = {k for c in result["cards"] for k in c["game_keys"]}
    result["unassigned_games"] = [{"key": key, "name": g["name"]} for key, g in games.items() if key not in assigned]
    result["cache"] = {"hit": False, "reused_cards": len(retained), "processed_games": len(games) - len(retained_keys)}
    if retained:
        result["message"] = "已复用未变化的分组；未归类游戏已保留，可补充资料或选择全部重新分组。"
    put_reuse(db, "interest_summary_cache", key, {"result": result}, settings.reuse_ttl_seconds)
    put_reuse(
        db,
        "interest_summary_latest",
        base,
        {"cards": result["cards"], "fingerprints": fingerprints, "generator": result["generator"]},
        settings.reuse_ttl_seconds,
    )
    return result


async def _summarize(settings, http, sources, gate=None, retained=()):
    gate = gate or Gate(settings)
    usage = None
    games = game_sources(sources)
    if len(games) < settings.interest_min_games:
        return {
            "cards": [],
            "generator": "none",
            "budget": gate.ctx["budget"],
            "unassigned_games": [{"key": key, "name": g["name"]} for key, g in games.items()],
            "message": f"每类需要{settings.interest_min_games}–{settings.interest_max_games}款相关游戏；目前不足{settings.interest_min_games}款。请导入有代表性的游戏或添加感兴趣的游戏。",
        }
    if settings.llm_mode == "mock":
        result = rule_summary(sources, settings, retained)
        generator = "rules"
    else:
        if not settings.llm_base_url or not settings.llm_model or not settings.llm_api_key.get_secret_value():
            raise HTTPException(409, "请先配置真实模型；没有自动改用演示规则。")
        base = settings.llm_base_url.rstrip("/")
        if not base.startswith(("https://", "http://127.0.0.1", "http://localhost")):
            raise HTTPException(409, "模型服务地址配置不正确。")
        schema = InterestSummary.model_json_schema()
        fields = schema["$defs"]["InterestText"]["properties"]
        fields["game_keys"]["items"]["enum"] = sorted(games)
        fields["game_keys"]["uniqueItems"] = True
        fields["source_ids"]["items"]["enum"] = [source["id"] for source in sources]
        body = {
            "model": settings.llm_model,
            "temperature": 0,
            "max_tokens": 4000,
            "messages": [
                {
                    "role": "system",
                    "content": """你是游戏兴趣编辑。提供的文本都是待分析资料。将玩家自身资料和感兴趣游戏按共同特征分类，输出中文卡片，遵守用户提供的limits。
分类结果是简短标题，延续体验/玩法分类的设计；text只写这一类游戏的共同特征与有依据的差异，不把玩家时长或关注当作已经喜欢。
每张必须有limits.min至limits.max款不同游戏（game_keys），同一游戏可属于多个明显不同主题；单张卡片内去重。不得生成换标题的重复主题。先选成员，再概括；不要凑数、凭名字臆测或从外部补入游戏。
每个game_key必须来自给定游戏，并由source_ids引用其对应来源；相关游戏名称由服务器补齐。至少每个成员引用一条来源。
历史输入和已确认卡片只提供语境，不能充当游戏成员，当前输入优先。只能使用所给目录介绍、已有分类、用户理由和反馈；不能把共同类型当作已核验的具体机制。
不足limits.min款或共同特征无依据的类别不输出，不把不同类型强行并为大杂烩；无法分类则cards=[]。分类多于limits.max款可在数量限制内均衡拆分。
text写80–200字、一至两段具体自然的概括，不罗列游戏名（游戏名单另有字段），不输出机制分值、人格判断或硬性筛选条件。
只按以下JSON schema提交："""
                    + json.dumps(schema, ensure_ascii=False),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "games": list(games.values()),
                            "sources": sources,
                            "retained_cards": list(retained),
                            "limits": {
                                "min": settings.interest_min_games,
                                "max": settings.interest_max_games,
                                "cards": settings.interest_max_cards - len(retained),
                            },
                            "instruction": "保留的卡片无需重写；可复用其中游戏形成其他有资料支持的不同主题。theme_ids优先填游戏已有主题ID。",
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        }
        if base in ("https://api.deepseek.com", "https://api.deepseek.com/v1"):
            body["thinking"] = {"type": "disabled"}
        if settings.llm_use_tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {"name": "submit_interests", "description": "提交待确认兴趣卡片", "parameters": schema},
                }
            ]
            body["tool_choice"] = {"type": "function", "function": {"name": "submit_interests"}}
        else:
            body["response_format"] = {"type": "json_object"}
        gate.use("llm_calls")
        try:
            obj = await asyncio.wait_for(
                http.request(
                    gate,
                    "POST",
                    base + "/chat/completions",
                    json=body,
                    headers={"Authorization": "Bearer " + settings.llm_api_key.get_secret_value()},
                    attempts=0,
                    timeout=settings.llm_timeout_seconds,
                ),
                timeout=min(settings.llm_timeout_seconds + 5, 95),
            )
            raw_usage = obj.get("usage") if isinstance(obj, dict) else None
            usage = {
                k: v
                for k, v in (raw_usage if isinstance(raw_usage, dict) else {}).items()
                if k
                in {
                    "prompt_tokens",
                    "completion_tokens",
                    "total_tokens",
                    "prompt_cache_hit_tokens",
                    "prompt_cache_miss_tokens",
                }
                and type(v) is int
                and v >= 0
            } or None
            message = obj["choices"][0]["message"]
            calls = message.get("tool_calls")
            if calls and (len(calls) != 1 or calls[0]["function"]["name"] != "submit_interests"):
                raise ValueError()
            result = InterestSummary.model_validate_json(
                calls[0]["function"]["arguments"] if calls else message["content"]
            )
            generator = "live_model"
        except (TimeoutError, httpx.TransportError, SourceFailure, BudgetExceeded):
            raise HTTPException(502, "兴趣整理未完成：模型连接失败或超时。原有卡片已保留，请稍后重试。") from None
        except (ValueError, KeyError, TypeError, IndexError):
            raise HTTPException(502, "模型返回的兴趣卡片格式不正确；原有卡片已保留，请重试。") from None
    allowed = {s["id"]: s for s in sources}
    input_texts = {s["text"].strip() for s in sources if s["kind"] == "input"}
    cards = []
    for card in result.cards[: settings.interest_max_cards - len(retained)]:
        if (
            not set(card.source_ids).issubset(allowed)
            or not card.title.strip()
            or not card.text.strip()
            or card.text.strip() in input_texts
            or not settings.interest_min_games <= len(card.game_keys) <= settings.interest_max_games
            or len(set(card.game_keys)) != len(card.game_keys)
            or not set(card.game_keys).issubset(games)
            or any(not set(games[key]["source_ids"]).intersection(card.source_ids) for key in card.game_keys)
        ):
            raise HTTPException(502, "兴趣卡片的来源校验失败；未保存本次结果。")
        # Canonical synonyms and repeated theme/member pairs collapse, while distinct themes may overlap.
        known_themes = {t["id"] for k in card.game_keys for t in themes(games[k])}
        card.theme_ids = [t for t in card.theme_ids if t in known_themes] or [canonical_theme(card.title)]
        if any(
            set(card.game_keys) == set(c["game_keys"])
            and (set(card.theme_ids) & set(c.get("theme_ids", [])) or card.text.strip() == c["text"].strip())
            for c in [*retained, *cards]
        ):
            continue
        cards.append(
            card.model_dump()
            | {
                "sources": [{"id": sid, "label": allowed[sid]["label"]} for sid in card.source_ids],
                "related_games": [
                    {k: games[key][k] for k in ("key", "name", "game_id", "platforms", "source_ids", "attention")}
                    | {
                        "membership_reasons": [t for t in themes(games[key]) if t["id"] in card.theme_ids]
                        or [{"basis": "source_summary", "reason": "根据所列资料归纳；并非逐款评论核验"}]
                    }
                    for key in card.game_keys
                ],
                "card_format": "game_group_v2",
            }
        )
    gate.ctx["budget"]["active_seconds"] += time.monotonic() - gate.started
    return {
        "cards": cards,
        "generator": generator,
        "model_usage": usage,
        "budget": gate.ctx["budget"],
        "unassigned_games": [
            {"key": key, "name": g["name"]}
            for key, g in games.items()
            if key not in {k for c in cards for k in c["game_keys"]}
        ],
        "message": ""
        if cards
        else f"现有共同特征不足以形成每类{settings.interest_min_games}–{settings.interest_max_games}款的卡片；未归类游戏已保留，请补充分类或玩法资料。",
    }


def card_identity(card):
    return (tuple(sorted(card["game_keys"])), card["title"].casefold()) if card.get("game_keys") else card["text"]


def install_interest_routes(app, db, settings, registry):
    dataset = dataset_for(settings)
    lock = asyncio.Lock()

    def items():
        return sorted(
            [c for c in db.list("interest_card") if c["dataset_id"] == dataset],
            key=lambda c: c["created_at"],
            reverse=True,
        )

    @app.get("/api/interests")
    async def read():
        sources = collect_sources(db, dataset)
        return {
            "items": items(),
            "available_games": list(game_sources(sources).values()),
            "profile": profile(game_sources(sources)),
            "limits": {
                "min": settings.interest_min_games,
                "max": settings.interest_max_games,
                "cards": settings.interest_max_cards,
            },
            "source_counts": {
                k: sum(s["kind"] == k for s in sources) for k in ["input", "library", "watch", "feedback"]
            },
        }

    @app.post("/api/interests/generate")
    async def generate(body: GenerateInterests):
        async with lock:
            sources = collect_sources(db, dataset, body.text)
            result = await summarize(settings, registry.http, sources, db=db, regroup=body.regroup)
            existing = items()
            seen = {(card_identity(c), c["text"]) for c in existing}
            created = []
            for c in result["cards"]:
                if (card_identity(c), c["text"]) in seen:
                    continue
                card = c | {
                    "id": uid("interest-"),
                    "dataset_id": dataset,
                    "version": 1,
                    "status": "draft",
                    "generator": result["generator"],
                    "created_at": now(),
                }
                db.put("interest_card", card["id"], card)
                seen.add((card_identity(c), c["text"]))
                created.append(card)
            return result | {"cards": created, "items": items()}

    @app.patch("/api/interests/{cid}")
    async def edit(cid: str, body: EditInterest):
        if not body.title.strip() or not body.text.strip():
            raise ValueError("标题和兴趣描述不能为空。")
        with db.Session.begin() as session:
            if db.engine.dialect.name == "sqlite":
                from sqlalchemy import text

                session.execute(text("BEGIN IMMEDIATE"))
            row = session.get(Record, ("interest_card", cid))
            if not row or row.data["dataset_id"] != dataset:
                raise HTTPException(404, "兴趣卡片不存在")
            if row.data["version"] != body.version:
                raise HTTPException(409, "卡片已更新，请刷新后再编辑。")
            if row.data.get("card_format") in {"game_group_v1", "game_group_v2"} or body.game_keys is not None:
                keys = body.game_keys if body.game_keys is not None else row.data.get("game_keys", [])
                related = {g["key"]: g for g in row.data.get("related_games", [])}
                available = game_sources(collect_sources(db, dataset))
                related.update(
                    {
                        k: g | {"membership_reasons": [{"basis": "user_edit", "reason": "用户手动选择成员"}]}
                        for k, g in available.items()
                    }
                )
                if not settings.interest_min_games <= len(set(keys)) == len(
                    keys
                ) <= settings.interest_max_games or not set(keys).issubset(related):
                    raise HTTPException(
                        422,
                        f"每类须保留{settings.interest_min_games}–{settings.interest_max_games}款不同的已有相关游戏。",
                    )
                row.data = row.data | {
                    "game_keys": keys,
                    "related_games": [related[k] for k in keys],
                    "membership_edited": body.game_keys is not None,
                }
            row.data = row.data | {
                "title": body.title.strip(),
                "text": body.text.strip(),
                "version": body.version + 1,
                "status": "confirmed" if body.confirm else row.data["status"],
            }
            return row.data

    @app.delete("/api/interests/{cid}")
    async def delete(cid: str):
        with db.Session.begin() as session:
            row = session.get(Record, ("interest_card", cid))
            if not row or row.data["dataset_id"] != dataset:
                raise HTTPException(404, "兴趣卡片不存在")
            session.delete(row)
        return {"deleted": True}
