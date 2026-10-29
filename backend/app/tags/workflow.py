"""Persistent tag-first research; legacy DNA tasks keep their original protocol."""

import copy
import json
import time
from sqlalchemy import select
from .catalog import VERSION, TAGS, mapping_plan, catalog_priority, recall_branches
from .intent import demo_interpret, ground_interpretation, confirmed_interpretation
from .llm import TagLLM, PROTOCOL
from .ranking import rank, POLICY, independent_ids
from ..config import ROOT
from ..db import Task, Report, now, uid
from ..tools import Gate, BudgetExceeded, SourceFailure, Stopped, safe_link
from ..ranking import hard_filter, constraints_from_text
from ..discovery import scope_constraints
from ..reuse import load_batch, save_batch
from .strategy import policy, diversified, evidence_gap, useful_unread


def resolve_cards(db, dataset, attachments):
    cards = []
    for attachment in attachments:
        card = db.get("interest_card", attachment.id)
        if (
            not card
            or card["dataset_id"] != dataset
            or card["status"] != "confirmed"
            or card["version"] != attachment.version
        ):
            raise ValueError("附加卡片不存在、未确认或已更新；请重新选择")
        if card["id"] not in {c["id"] for c in cards}:
            cards.append(
                {
                    k: card[k]
                    for k in ("id", "version", "title", "text", "sources", "game_keys", "related_games", "card_format")
                    if k in card
                }
            )
    return cards


def create(engine, request):
    s, db = engine.s, engine.db
    if s.diagnostics():
        raise ValueError("需要由你配置：" + ", ".join(s.diagnostics()))
    if s.data_mode == "live" and s.llm_mode != "live":
        raise ValueError("真实标签研究需要真实 LLM；不能用演示规则代替语义理解")
    if request.demo_fault != "none" and (s.data_mode != "fixtures" or not s.enable_demo_controls):
        raise ValueError("故障注入仅限演示模式")
    if request.dna_query or request.preferences:
        raise ValueError("标签研究不接收旧 DNA 条件或 0–4 偏好")
    dataset = "synthetic-v1" if s.data_mode == "fixtures" else "live"
    cards = resolve_cards(db, dataset, request.attachments)
    if not request.query.strip() and not cards:
        raise ValueError("请填写研究输入，或附加已确认的兴趣卡片")
    for gid in request.game_ids:
        if db.get("game", gid, {}).get("dataset_id") != dataset:
            raise ValueError("候选不属于当前数据集")
    if request.confirmed_conditions:
        confirmed_interpretation(request.confirmed_conditions)  # Validate before persisting.
    tid = uid("task-")
    ctx = {
        "task_id": tid,
        "dataset_id": dataset,
        "data_mode": s.data_mode,
        "llm_mode": s.llm_mode,
        "prompt_version": PROTOCOL,
        "tag_version": VERSION,
        "attached_cards": cards,
        "budget": {"tool_calls": 0, "external_requests": 0, "llm_calls": 0, "planning_rounds": 0, "active_seconds": 0},
        "candidates": list(request.game_ids),
        "selected": [],
        "target_count": request.recommendation_count,
        "strategy": policy(s, request.research_strategy, request.recommendation_count),
        "savings": {},
        "strategy_decisions": [],
        "catalog_scan": {"rows": 0, "pages": [], "unique_ids": []},
        "document_ids": [],
        "queue": [],
        "full_reviewed_document_ids": [],
        "reading_ledger": [],
        "verdicts": [],
        "human_done": [],
        "errors": [],
        "gaps": [],
        "pending_human": None,
        "ratings": {},
        "sampling": {},
        "investigations": {},
        "constraints": scope_constraints(
            request.confirmed_conditions.constraints.model_dump()
            if request.confirmed_conditions
            else constraints_from_text(request.query, request.constraints.model_dump()),
            request.discovery.model_dump(),
            "" if request.confirmed_conditions else request.query,
        ),
        "assumptions": [
            "标签是偏好与检索线索，不是 0–4 机制评分。",
            "目录映射只做候选召回；原生商店标签接口尚未接入，当前使用 IGDB 类型和关键词代理。",
            "完整评价按好评、差评、近期分层抽取；这是有限的目的性样本，不能推断全体玩家比例。",
            "附加卡片为本次快照，只补充当前输入没有明确的意愿。",
        ],
    }
    if request.confirmed_conditions:
        ctx["constraints"]["platform"] = request.confirmed_conditions.constraints.platform
    if request.intent_preview_id:
        from ..reuse import get as get_reuse, digest

        preview = get_reuse(db, "intent_preview", request.intent_preview_id)
        if not preview or preview["signature"] != digest([request.query, cards, dataset]):
            raise ValueError("需求预览已过期或输入/卡片已变化，请重新解析")
        ctx["budget"] = dict(preview["budget"])
        ctx["intent_preview_id"] = request.intent_preview_id
    with db.Session.begin() as session:
        session.add(Task(id=tid, request=request.model_dump(), context=ctx))
    db.event(tid, "system", "task_started", "标签研究已排队；先理解输入和附加卡片，再检索候选。")
    return tid


def human_gate(engine, task, ctx, role):
    if task["request"]["role_modes"][role] != "human" or role in ctx["human_done"]:
        return True
    body = ctx.pop("human_input", None)
    if body:
        ctx["pending_human"] = None
        ctx["human_done"].append(role)
        if role == "planner" and body["game_ids"]:
            ctx["selected"] = body["game_ids"][
                : ctx.get("strategy", {}).get("investigation_limit", engine.s.tag_investigation_limit)
            ]
            ctx["reserve_candidates"] = []
        if role == "researcher" and body["source_text"]:
            gid = (body["game_ids"] or ctx["selected"])[0]
            ids = engine.knowledge.ingest(
                [
                    {
                        "game_id": gid,
                        "provider": "user",
                        "external_id": uid("note-"),
                        "title": body["source_title"] or "用户补充资料",
                        "text": body["source_text"],
                        "url": safe_link(body["source_url"]),
                        "kind": "note",
                        "dataset_id": ctx["dataset_id"],
                        "provenance_type": "user_provided",
                        "published_at": None,
                    }
                ]
            )
            ctx["document_ids"] += ids
            ctx["gaps"].append("用户补充资料已保存为待核验笔记，不计作独立玩家评价。")
        if role == "verifier":
            for c in engine.knowledge.claims(task["id"]):
                if c["id"] in body["claim_ids"]:
                    ctx["verdicts"].append(
                        {
                            "claim_id": c["id"],
                            "verdict": "rejected" if body["decision"] == "reject" else "supported",
                            "reason": "人工逐项核验：" + (body["note"] or "已阅读来源并作出判断"),
                            "evidence_ids": c["evidence_ids"],
                        }
                    )
        return True
    ctx["pending_human"] = {
        "role": role,
        "phase": task["phase"],
        "instructions": {
            "planner": "检查已解释的标签和候选范围，选择需要优先调查的游戏。",
            "researcher": "核对完整来源；可补充待核验笔记。恢复后，模型辅助提取标签证据。",
            "verifier": "阅读完整评价后勾选接受或驳回的主张；未选择的主张保持证据不足。",
        }[role],
    }
    engine.commit(task["id"], ctx, task["phase"])
    with engine.db.Session.begin() as s:
        s.get(Task, task["id"]).status = "paused"
    engine.db.event(task["id"], role, "human_required", ctx["pending_human"]["instructions"], ctx["pending_human"])
    return False


async def model_call(engine, tid, ctx, gate, stage, payload):
    ctx["inflight"] = {"kind": "llm", "stage": stage, "protocol": PROTOCOL}
    engine.checkpoint(tid, ctx)
    try:
        result = await TagLLM(engine.s, engine.registry.http).run(stage, payload, gate)
    except (SourceFailure, BudgetExceeded) as exc:
        traces = ctx.get("model_requests", [])
        if traces and traces[-1]["status"] == "submitted":
            traces[-1].update(status="failed", error_code=str(exc))
        raise
    ctx.pop("inflight", None)
    return result


def batches(documents, count_limit, char_limit=32000, intents=None):
    groups = {}
    for d in documents:
        if d["kind"] == "reviews" and d.get("complete", d["provenance_type"] == "synthetic"):
            groups.setdefault((d["game_id"], d.get("sampling_stratum", "unknown")), []).append(d)
    if intents is not None:
        terms = list(
            dict.fromkeys(
                k.casefold()
                for i in intents
                for k in TAGS[i["tag_id"]]["igdb"]["keywords"] + TAGS[i["tag_id"]]["aliases"]
            )
        )
        # A targeted, balanced sample, not a claim of statistical representativeness.
        for group in groups.values():
            group.sort(
                key=lambda d: (sum(t in d["text"].casefold() for t in terms), min(len(d["text"]), 1000)), reverse=True
            )
    ordered = []
    for n in range(max((len(v) for v in groups.values()), default=0)):
        for group in groups.values():
            if n < len(group):
                ordered.append(group[n])
    by_game = {}
    identities = independent_ids(ordered)
    included = set()
    for d in ordered:
        identity_key = (d["game_id"], identities[d["id"]])
        if identity_key in included:
            continue
        batch = by_game.setdefault(d["game_id"], [])
        if len(batch) < min(count_limit, 12) and sum(len(x["text"]) for x in batch) + len(d["text"]) <= char_limit:
            batch.append(d)
            included.add(identity_key)
    if intents is not None:
        for gid, batch in by_game.items():
            metadata = [d for d in documents if d["game_id"] == gid and d["kind"] == "details" and d["text"]]
            # Prefer a compact complete catalog description; never cut a source's tail.
            for d in sorted(metadata, key=lambda x: len(x["text"])):
                if batch and sum(len(x["text"]) for x in batch) + len(d["text"]) <= char_limit:
                    batch.append(d)
                    break
    return [[d["id"] for d in group] for group in by_game.values() if group]


def demo_findings(documents, intents):
    allowed = {i["tag_id"] for i in intents}
    data = json.loads((ROOT / "data/fixtures/tag_reviews.json").read_text())
    annotations = {d["external_id"]: d.get("annotations", []) for d in data}
    result = []
    for d in documents:
        for a in annotations.get(d["external_id"], []):
            if a.get("tag_id") and a["tag_id"] not in allowed:
                continue
            result.append(dict(a, game_id=d["game_id"], document_id=d["id"], feature_key=None))
    for d in documents:
        if d["kind"] == "details" and d["provenance_type"] == "synthetic":
            # Explicit synthetic catalog text, never a fallback for live source failures.
            quote = d["text"].split("。", 1)[0] + "。"
            if quote in d["text"] and len(quote) <= 400:
                result.insert(
                    0,
                    dict(
                        aspect="core_loop",
                        tag_id=None,
                        tag_polarity="present",
                        game_id=d["game_id"],
                        document_id=d["id"],
                        feature_key=None,
                        quote=quote,
                        claim_text=quote,
                        scope={},
                    ),
                )
    return result[:16]


def expand_queue(engine, ctx, request):
    if (
        ctx["queue"]
        or not ctx.get("reserve_candidates")
        or any(request["role_modes"][r] == "human" for r in ("planner", "researcher", "verifier"))
    ):
        return
    gid = ctx["reserve_candidates"].pop(0)
    ctx["selected"].append(gid)
    ctx["investigations"][gid].update(selected=True, stage="queued")
    ctx["queue"] = [
        {
            "tool_name": cap,
            "arguments": {
                "game_id": gid,
                **({"review_limit": engine.s.tag_review_fetch_limit} if cap == "get_reviews" else {}),
            },
        }
        for cap in ("get_game_details", "get_reviews")
        if engine.supports_action({"tool_name": cap, "arguments": {"game_id": gid}})
    ]
    ctx.setdefault("strategy_decisions", []).append(
        {"game_id": gid, "action": "expand", "reason": "完整卡片未达目标，从初筛候选递补一款"}
    )


async def step(engine, task):
    tid, request = task["id"], task["request"]
    ctx, phase = copy.deepcopy(task["context"]), task["phase"]
    gate = Gate(engine.s, ctx, lambda: engine.guard(tid), lambda: engine.checkpoint(tid, ctx))
    started = time.monotonic()
    strategy = ctx.setdefault(
        "strategy", policy(engine.s, request.get("research_strategy", "balanced"), ctx.get("target_count", 5))
    )
    with engine.db.Session.begin() as s:
        s.get(Task, tid).status = "running"
    next_phase = phase
    try:
        if phase == "report":
            return make_report(engine, tid, ctx)
        gate.guard()
        if phase == "interpret":
            if request.get("confirmed_conditions") is not None:
                from ..schemas import ConfirmedConditions

                conditions = ConfirmedConditions.model_validate(request["confirmed_conditions"])
                result = confirmed_interpretation(conditions)
                ctx["custom_requirements"] = [c.model_dump() for c in conditions.custom if c.status != "ignored"]
            elif ctx["llm_mode"] == "mock":
                result = demo_interpret(request["query"], ctx["attached_cards"])
            else:
                result = await model_call(
                    engine,
                    tid,
                    ctx,
                    gate,
                    "interpret",
                    {"query": request["query"], "attachments": ctx["attached_cards"]},
                )
                try:
                    result = ground_interpretation(result, request["query"], ctx["attached_cards"])
                except ValueError as e:
                    raise SourceFailure("ungrounded_interpretation:" + str(e)) from None
            ctx.setdefault(
                "custom_requirements",
                [
                    {"text": x, "status": "unmapped", "polarity": "want", "priority": 2, "explicit_redline": False}
                    for x in result.unmapped
                ],
            )
            ctx["interpretation"] = result.model_dump()
            ctx["mappings"] = mapping_plan(ctx["interpretation"]["intents"], request["discovery"])
            ctx["gaps"] += result.unmapped
            engine.db.event(tid, "planner", "tags_interpreted", result.summary, ctx["interpretation"])
            next_phase = "plan" if result.intents else "report"
        elif phase == "plan":
            if not ctx.get("discovery_complete") and not request["game_ids"]:
                scope = copy.deepcopy(request["discovery"])
                if not scope.get("platforms") and ctx["constraints"].get("text_platform") == "PC":
                    scope["platforms"] = ["pc"]
                if "catalog_queue" not in ctx:
                    ctx["catalog_queue"] = recall_branches(ctx["interpretation"]["intents"], scope)
                    ctx.setdefault("catalog_scan", {"rows": 0, "pages": [], "unique_ids": []})
                scan = ctx["catalog_scan"]
                action = ctx["catalog_queue"].pop(0)
                page_size = min(100, strategy["scan_limit"] - scan["rows"])
                action["arguments"]["catalog_page_size"] = max(1, page_size)
                ctx["inflight"] = {"kind": "tool", "action": action}
                engine.checkpoint(tid, ctx)
                result = await engine.registry.call(
                    action, gate, lambda a, b, c: engine.db.event(tid, "researcher", a, b, c)
                )
                ctx.pop("inflight", None)
                if result.status != "available":
                    if not ctx["candidates"]:
                        raise SourceFailure(result.error_code or result.status)
                    ctx["gaps"].append("部分目录分页失败：" + (result.error_code or result.status))
                else:
                    info = result.data.get("search_info", {})
                    games = result.data.get("games", [])
                    scan["rows"] += info.get("candidates_scanned", len(games))
                    scan["pages"].append(
                        {"arguments": action["arguments"], "returned": len(games), "from_cache": result.from_cache}
                    )
                    scan["unique_ids"] = list(dict.fromkeys(scan["unique_ids"] + [g["id"] for g in games]))
                    ctx["candidates"] = list(
                        dict.fromkeys(
                            ctx["candidates"]
                            + [g["id"] for g in games if hard_filter(g, ctx["constraints"])[0] != "fail"]
                        )
                    )
                    ctx["candidates"].sort(
                        key=lambda gid: catalog_priority(engine.db.get("game", gid), ctx["interpretation"]["intents"]),
                        reverse=True,
                    )
                    ctx["candidates"] = ctx["candidates"][: engine.s.tag_candidate_limit]
                    if not info.get("exhausted", True):
                        more = copy.deepcopy(action)
                        more["arguments"]["catalog_offset"] = more["arguments"].get("catalog_offset", 0) + page_size
                        ctx["catalog_queue"].append(more)
                # Leave enough requests to attempt at least one complete evidence chain.
                reserve = engine.s.max_external_requests - ctx["budget"]["external_requests"] <= 8
                tool_reserve = engine.s.max_tool_calls - ctx["budget"]["tool_calls"] <= 2
                ctx["discovery_complete"] = (
                    not ctx["catalog_queue"] or scan["rows"] >= strategy["scan_limit"] or reserve or tool_reserve
                )
                if reserve or tool_reserve:
                    ctx["gaps"].append("目录扫描提前结束，为完整评价与核验保留预算。")
            else:
                ctx["discovery_complete"] = True
            if ctx.get("discovery_complete"):
                if not ctx["selected"]:
                    ordered = diversified(
                        engine.db,
                        [
                            gid
                            for gid in ctx["candidates"]
                            if engine.supports_action({"tool_name": "get_reviews", "arguments": {"game_id": gid}})
                        ],
                        ctx["interpretation"]["intents"],
                    )[: strategy["shortlist_limit"]]
                    ctx["shortlist"] = ordered
                    ctx["selected"] = ordered[: min(strategy["initial_games"], strategy["investigation_limit"])]
                    ctx["reserve_candidates"] = ordered[len(ctx["selected"]) : strategy["investigation_limit"]]
                    ctx["investigation_limit"] = strategy["investigation_limit"]
                if not human_gate(engine, task, ctx, "planner"):
                    return
                ctx["candidates"] = list(dict.fromkeys(ctx["selected"] + ctx["candidates"]))[
                    : engine.s.tag_candidate_limit
                ]
                for gid in ctx["candidates"]:
                    supported = engine.supports_action({"tool_name": "get_reviews", "arguments": {"game_id": gid}})
                    ctx.setdefault("investigations", {}).setdefault(
                        gid,
                        {
                            "selected": gid in ctx["selected"],
                            "review_adapter": supported,
                            "stage": "queued" if gid in ctx["selected"] else "not_selected",
                            "errors": [],
                            "source_warnings": [],
                            "extraction_rejections": [],
                        },
                    )
                ctx["queue"] = [
                    {
                        "tool_name": cap,
                        "arguments": {
                            "game_id": gid,
                            **({"review_limit": engine.s.tag_review_fetch_limit} if cap == "get_reviews" else {}),
                        },
                    }
                    for gid in ctx["selected"]
                    for cap in ("get_game_details", "get_reviews")
                    if engine.supports_action({"tool_name": cap, "arguments": {"game_id": gid}})
                ]
                next_phase = "retrieve" if ctx["queue"] else "rank"
        elif phase == "retrieve":
            action = ctx["queue"][0]
            gid = action["arguments"]["game_id"]
            if ctx.get("active_game") != gid and ctx["data_mode"] == "live":
                remaining = engine.s.max_external_requests - ctx["budget"]["external_requests"]
                required = sum(
                    engine.registry.estimated_external(a) for a in ctx["queue"] if a["arguments"]["game_id"] == gid
                )
                if remaining < required:
                    ctx["active_game"] = gid
                    raise BudgetExceeded("剩余外部请求预算不足以获取下一款的完整来源")
            ctx["active_game"] = gid
            investigation = ctx.setdefault("investigations", {}).setdefault(gid, {"selected": True, "errors": []})
            investigation["stage"] = "retrieve"
            ctx["inflight"] = {"kind": "tool", "action": action}
            engine.checkpoint(tid, ctx)
            result = await engine.registry.call(
                action, gate, lambda a, b, c: engine.db.event(tid, "researcher", a, b, c), fault=request["demo_fault"]
            )
            ctx.pop("inflight", None)
            ctx["queue"].pop(0)
            investigation[action["tool_name"]] = {
                "status": result.status,
                "error_code": result.error_code,
                "from_cache": result.from_cache,
            }
            if result.status == "available":
                docs = result.data.get("documents", [])
                if ctx["data_mode"] == "fixtures" and action["tool_name"] == "get_reviews":
                    docs = json.loads((ROOT / "data/fixtures/tag_reviews.json").read_text())
                    docs = [
                        {k: v for k, v in d.items() if k != "annotations"}
                        for d in docs
                        if d["game_id"] == action["arguments"]["game_id"]
                    ]
                ctx["document_ids"] = list(dict.fromkeys(ctx["document_ids"] + engine.knowledge.ingest(docs)))
                investigation.setdefault("source_warnings", []).extend(result.data.get("source_warnings", []))
                if "sampling" in result.data:
                    ctx["sampling"][gid] = (
                        result.data["sampling"]
                        if ctx["data_mode"] == "live"
                        else {
                            "filter": "synthetic authored complete reviews",
                            "sample_size": len(docs),
                            "representative": False,
                        }
                    )
                    for error in result.data["sampling"].get("errors", []):
                        investigation["errors"].append({"stage": "retrieve", "code": error})
                        ctx["errors"].append(f"{gid} / get_reviews: {error}")
                if result.data.get("rating"):
                    ctx["ratings"][gid] = result.data["rating"]
            else:
                investigation["errors"].append({"stage": "retrieve", "code": result.error_code or result.status})
                ctx["errors"].append(
                    f"{action['arguments']['game_id']} / {action['tool_name']}: {result.error_code or result.status}"
                )
            game_finished = not ctx["queue"] or ctx["queue"][0]["arguments"]["game_id"] != gid
            # Finish one game's evidence before spending on the next candidate. Human
            # researcher mode retains its single review gate after all source retrieval.
            if not ctx["queue"] or (game_finished and request["role_modes"]["researcher"] != "human"):
                next_phase = "extract"
        elif phase == "extract":
            if not human_gate(engine, task, ctx, "researcher"):
                return
            if "read_batches" not in ctx:
                ctx["read_batches"] = batches(
                    engine.knowledge.documents(
                        [
                            d["id"]
                            for d in engine.knowledge.documents(ctx["document_ids"])
                            if d["id"] not in ctx["full_reviewed_document_ids"]
                            and (
                                request["role_modes"]["researcher"] == "human" or d["game_id"] == ctx.get("active_game")
                            )
                        ]
                    ),
                    engine.s.tag_review_batch_size,
                    engine.s.tag_review_batch_chars,
                    ctx["interpretation"]["intents"],
                )
            if ctx["read_batches"]:
                dids = ctx["read_batches"][0]
                docs = engine.knowledge.documents(dids)
                gid = docs[0]["game_id"]
                ctx["active_game"] = gid
                investigation = ctx.setdefault("investigations", {}).setdefault(gid, {"selected": True, "errors": []})
                investigation["stage"] = "extract"
                investigation["batch_attempts"] = investigation.get("batch_attempts", 0) + 1
                automatic = all(request["role_modes"][r] != "human" for r in ("researcher", "verifier"))
                reused = load_batch(engine, tid, ctx, docs) if automatic else None
                if reused:
                    findings = []
                elif ctx["llm_mode"] == "mock":
                    findings = demo_findings(docs, ctx["interpretation"]["intents"])
                else:
                    result = await model_call(
                        engine,
                        tid,
                        ctx,
                        gate,
                        "extract",
                        {"intents": ctx["interpretation"]["intents"], "documents": docs},
                    )
                    findings = [f.model_dump() | {"feature_key": None} for f in result.findings]
                    ctx["gaps"] += result.missing_information
                accepted, rejected = (
                    (reused["claim_ids"], [])
                    if reused
                    else engine.knowledge.extract(tid, findings, dids, ctx["dataset_id"])
                )
                ctx["gaps"] += rejected
                investigation["findings_returned"] = investigation.get("findings_returned", 0) + (
                    len(accepted) if reused else len(findings)
                )
                investigation["findings_accepted"] = investigation.get("findings_accepted", 0) + len(accepted)
                investigation["extraction_rejections"] = rejected
                ctx["full_reviewed_document_ids"] += [d["id"] for d in docs if d["kind"] == "reviews"]
                ctx["reading_ledger"] += [
                    {
                        "document_id": d["id"],
                        "kind": d["kind"],
                        "content_hash": d["content_hash"],
                        "start": 0,
                        "end": len(d["text"]),
                        "mode": "verified_cache"
                        if reused
                        else "synthetic_annotations"
                        if ctx["llm_mode"] == "mock"
                        else "model_full_text",
                        **({"origin_task_id": reused["origin_task_id"]} if reused else {}),
                    }
                    for d in docs
                ]
                ctx["read_batches"].pop(0)
                if accepted and not reused:
                    ctx.setdefault("verify_batches", []).append(accepted)
                    if automatic and not rejected and not ctx.get("model_protocol_errors"):
                        ctx.setdefault("cache_pending", {})[accepted[0]] = dids
                if reused:
                    investigation["reused_batches"] = investigation.get("reused_batches", 0) + 1
                engine.db.event(
                    tid,
                    "researcher",
                    "complete_reviews_read",
                    f"已将 {sum(d['kind'] == 'reviews' for d in docs)} 篇完整评价纳入分析；提取 {len(accepted)} 条待核验主张。",
                )
            if not ctx.get("read_batches"):
                ctx.pop("read_batches", None)
                next_phase = "retrieve" if ctx["queue"] and request["role_modes"]["verifier"] == "human" else "verify"
        elif phase == "verify":
            if request["role_modes"]["verifier"] == "human":
                if not human_gate(engine, task, ctx, "verifier"):
                    return
                ctx["verify_batches"] = []
            elif ctx.get("verify_batches") and engine.s.enable_verifier:
                ids = ctx["verify_batches"][0]
                claims = [c for c in engine.knowledge.claims(tid) if c["id"] in ids]
                if claims:
                    ctx["active_game"] = claims[0]["game_id"]
                    ctx.setdefault("investigations", {}).setdefault(ctx["active_game"], {})["stage"] = "verify"
                    if ctx["llm_mode"] == "mock":
                        verdicts = [
                            {
                                "claim_id": c["id"],
                                "verdict": "supported",
                                "reason": "虚构演示：与独立编写的完整评论注释一致",
                                "evidence_ids": c["evidence_ids"],
                            }
                            for c in claims
                        ]
                    else:
                        docs = engine.knowledge.documents([c["document_id"] for c in claims])
                        result = await model_call(
                            engine,
                            tid,
                            ctx,
                            gate,
                            "verify",
                            {"intents": ctx["interpretation"]["intents"], "claims": claims, "documents": docs},
                        )
                        verdicts = [v.model_dump() for v in result.verdicts if v.claim_id in ids]
                    ctx["verdicts"] += verdicts
                    # Persist each completed batch; a later failure cannot discard it.
                    engine.knowledge.verify(tid, verifier_enabled=engine.s.enable_verifier, verdicts=ctx["verdicts"])
                    dids = ctx.setdefault("cache_pending", {}).pop(ids[0], None)
                    if dids:
                        save_batch(engine, tid, ctx, ids, dids)
                ctx["verify_batches"].pop(0)
            else:
                ctx["verify_batches"] = []
            if not ctx.get("verify_batches"):
                engine.knowledge.verify(tid, verifier_enabled=engine.s.enable_verifier, verdicts=ctx["verdicts"])
                if ctx.get("active_game"):
                    ctx.setdefault("investigations", {}).setdefault(ctx["active_game"], {})["stage"] = "finished"
                current_rows = rank(
                    engine.db,
                    [engine.db.get("game", gid) for gid in ctx["candidates"]],
                    engine.knowledge.claims(tid),
                    engine.knowledge.documents(ctx["document_ids"]),
                    ctx,
                )
                qualified = [
                    r for r in current_rows if r["group"] in {"recommended", "tradeoffs"} and r["card"]["complete"]
                ]
                current = next((r for r in current_rows if r["game"]["id"] == ctx.get("active_game")), None)
                state = ctx.get("investigations", {}).get(ctx.get("active_game"), {})
                game_docs = [
                    d for d in engine.knowledge.documents(ctx["document_ids"]) if d["game_id"] == ctx.get("active_game")
                ]
                reviewed = set(ctx["full_reviewed_document_ids"])
                gap = evidence_gap(current, game_docs, reviewed)
                more = []
                budget = ctx["budget"]
                has_next = bool(ctx["queue"] or ctx.get("reserve_candidates"))
                reserve = (
                    7
                    if strategy["reserve_next_game"]
                    and has_next
                    and len(qualified) < ctx.get("target_count", 5)
                    and ctx["data_mode"] == "live"
                    else 0
                )
                within_budget = (
                    engine.s.max_llm_calls - budget["llm_calls"] >= 2 + (2 if reserve else 0)
                    and engine.s.max_external_requests - budget["external_requests"] >= 2 + reserve
                    and (
                        ctx["llm_mode"] == "mock"
                        or engine.s.max_active_seconds - budget["active_seconds"] - (time.monotonic() - started)
                        >= 2 * engine.s.llm_timeout_seconds
                    )
                )
                progress = (
                    [[t["tag_id"], t["support_count"], t["oppose_count"]] for t in current["tags"]]
                    + [bool(current["card"]["introduction"])]
                    if current
                    else []
                )
                stalled = state.get("last_progress") == progress and state.get("batch_attempts", 0) > 1
                state["last_progress"] = progress
                reason = gap["reason"]
                if (
                    gap["needed"]
                    and not stalled
                    and state.get("batch_attempts", 0) < strategy["max_batches"]
                    and within_budget
                    and all(request["role_modes"][r] != "human" for r in ("researcher", "verifier"))
                ):
                    attempted = {
                        x for m in ctx.get("model_requests", []) if m["stage"] == "extract" for x in m["document_ids"]
                    }
                    unread = useful_unread(game_docs, reviewed | attempted, ctx["interpretation"]["intents"], gap)
                    more = batches(
                        unread, engine.s.tag_review_batch_size, engine.s.tag_review_batch_chars, gap["intents"]
                    )
                    if not more:
                        reason = "没有能够补充当前缺口的可读独立全文；保留未知项"
                elif gap["needed"]:
                    reason = (
                        "追加后没有新增有效证据，停止重复调查"
                        if stalled
                        else "达到本档批次上限或为后续候选保留预算；保留未知项"
                    )
                state["stop_or_continue_reason"] = reason
                ctx.setdefault("strategy_decisions", []).append(
                    {
                        "game_id": ctx.get("active_game"),
                        "action": "additional_batch" if more else "stop_game",
                        "reason": reason,
                    }
                )
                if more:
                    ctx["read_batches"] = more
                    next_phase = "extract"
                elif len(qualified) >= ctx.get("target_count", 5):
                    ctx["completion_reason"] = "target_reached"
                    for pending in ctx["investigations"].values():
                        if pending.get("stage") == "queued":
                            pending.update(
                                stage="skipped_target_reached",
                                stop_or_continue_reason="完整卡片已达到目标，停止后续调查；未判断该游戏是否符合",
                            )
                    ctx["queue"] = []
                    next_phase = "rank"
                else:
                    expand_queue(engine, ctx, request)
                    next_phase = "retrieve" if ctx["queue"] else "rank"
        elif phase == "rank":
            next_phase = "report"
        ctx["budget"]["active_seconds"] += time.monotonic() - started
        engine.commit(tid, ctx, next_phase)
    except Stopped:
        ctx["budget"]["active_seconds"] += time.monotonic() - started
        engine.checkpoint(tid, ctx)
    except (BudgetExceeded, SourceFailure) as exc:
        ctx["budget"]["active_seconds"] += time.monotonic() - started
        ctx["errors"].append(str(exc))
        if ctx.get("active_game"):
            investigation = ctx.setdefault("investigations", {}).setdefault(ctx["active_game"], {})
            investigation.setdefault("errors", []).append({"stage": phase, "code": str(exc)})
        if isinstance(exc, BudgetExceeded):
            ctx["stop_reason"] = str(exc)
        ctx.pop("inflight", None)
        engine.knowledge.verify(tid, verifier_enabled=engine.s.enable_verifier, verdicts=ctx["verdicts"])
        if isinstance(exc, SourceFailure) and phase in {"extract", "verify"}:
            # A malformed or timed-out model result belongs to this game. Continue
            # bounded work for other games, without approving the failed findings.
            if phase == "extract":
                if ctx.get("read_batches"):
                    ctx["read_batches"].pop(0)
                if not ctx.get("read_batches"):
                    ctx.pop("read_batches", None)
                next_phase = "extract" if ctx.get("read_batches") else "verify"
            else:
                if ctx.get("verify_batches"):
                    ctx["verify_batches"].pop(0)
                if not ctx.get("verify_batches"):
                    expand_queue(engine, ctx, request)
                next_phase = "verify" if ctx.get("verify_batches") else "retrieve" if ctx["queue"] else "rank"
            engine.commit(tid, ctx, next_phase)
        else:
            engine.commit(tid, ctx, "report")
            make_report(engine, tid, ctx)
    except Exception as exc:
        ctx["errors"].append(type(exc).__name__)
        engine.commit(tid, ctx, phase, "failed")
        engine.db.event(tid, "system", "task_failed", "标签研究执行失败：" + type(exc).__name__)
        # A malformed external response must not kill the persistent worker for other tasks.
        return


def make_report(engine, tid, ctx):
    games = [engine.db.get("game", gid) for gid in ctx["candidates"]]
    games = [g for g in games if g and g["dataset_id"] == ctx["dataset_id"]]
    docs, claims = engine.knowledge.documents(ctx["document_ids"]), engine.knowledge.claims(tid)
    report = {
        "mode": "player_tags",
        "task_id": tid,
        "query": engine.db.task(tid)["request"]["query"],
        "kind": "scout",
        "created_at": now(),
        "data_mode": ctx["data_mode"],
        "llm_mode": ctx["llm_mode"],
        "dataset_id": ctx["dataset_id"],
        "tag_version": VERSION,
        "ranking_policy": POLICY,
        "interpretation": ctx.get("interpretation", {"summary": "需求解释未完成", "intents": [], "unmapped": []}),
        "attached_cards": ctx["attached_cards"],
        "custom_requirements": ctx.get("custom_requirements", []),
        "mappings": ctx.get("mappings", []),
        "results": rank(engine.db, games, claims, docs, ctx),
        "errors": ctx["errors"],
        "unknowns": list(dict.fromkeys(ctx["gaps"])),
        "budget": ctx["budget"],
        "assumptions": ctx["assumptions"],
        "reading_ledger": ctx["reading_ledger"],
        "investigations": ctx.get("investigations", {}),
        "stop_reason": ctx.get("stop_reason"),
        "model_protocol_errors": ctx.get("model_protocol_errors", []),
        "model_requests": ctx.get("model_requests", []),
        "model_usage": ctx.get("model_usage", []),
        "request_failures": ctx.get("request_failures", []),
        "catalog_scan": ctx.get("catalog_scan", {}),
        "strategy": ctx.get("strategy", {}),
        "shortlist": ctx.get("shortlist", []),
        "savings": ctx.get("savings", {}),
        "strategy_decisions": ctx.get("strategy_decisions", []),
        "reading_policy": {
            "batch_size": engine.s.tag_review_batch_size,
            "batch_chars": engine.s.tag_review_batch_chars,
            "fetch_limit": engine.s.tag_review_fetch_limit,
            "max_batches_per_game": ctx.get("strategy", {}).get("max_batches", engine.s.tag_max_batches_per_game),
        },
        "snapshot": {"games": games, "claims": claims, "documents": docs},
        "investigation_limit": ctx.get("investigation_limit", 0),
        "note": "逐款完成完整评价分析与核验后排序。未形成匹配证据的目录候选单独列出，不作为推荐。",
    }
    eligible = [r for r in report["results"] if r["group"] in {"recommended", "tradeoffs"} and r["card"]["complete"]]
    report["recommendation_ids"] = [r["game"]["id"] for r in eligible[: ctx.get("target_count", 5)]]
    report["target"] = {
        "requested": ctx.get("target_count", 5),
        "achieved": len(report["recommendation_ids"]),
        "met": len(report["recommendation_ids"]) >= ctx.get("target_count", 5),
        "stop_reason": ctx.get("stop_reason")
        or ctx.get("completion_reason")
        or "strategy_limit_or_evidence_insufficient",
    }
    report["unmet"] = []
    if not report["target"]["met"]:
        report["unmet"].append(
            f"期望 {report['target']['requested']} 款，当前有完整证据卡片 {report['target']['achieved']} 款；原因：{report['target']['stop_reason']}。"
        )
    if any(not x.get("recovered") for x in report["model_protocol_errors"]):
        report["unmet"].append("部分模型返回项未通过结构校验，已保留合法项并记录具体路径；未通过项不参与推荐。")
    report["recovery_notes"] = [
        "模型格式错误已通过有界重试恢复" for e in report["model_protocol_errors"] if e.get("recovered")
    ]
    for r in eligible[: ctx.get("target_count", 5)]:
        if r.get("critical_unknowns"):
            report["unmet"].append(r["game"]["name"] + " 关键条件待确认：" + "、".join(r["critical_unknowns"]))
        if r["group"] == "tradeoffs":
            report["unmet"].append(r["game"]["name"] + " 有与偏好相反的证据，列入有取舍的选项；不能理解为完全匹配。")
    report["unfinished_investigations"] = [
        {
            "game_id": r["game"]["id"],
            "name": r["game"]["name"],
            "diagnosis": r["diagnosis"],
            "missing_card_fields": r["card"]["missing"],
            "unknown_preferences": r["unknowns"],
        }
        for r in report["results"]
        if r["diagnosis"]["selected"] and r["game"]["id"] not in report["recommendation_ids"]
    ]
    report["diagnostics"] = {
        "recalled": len(games),
        "selected": len(ctx["selected"]),
        "with_reviews": sum(r["reading"]["fetched"] > 0 for r in report["results"]),
        "full_read": sum(r["reading"]["full_read"] > 0 for r in report["results"]),
        "with_findings": sum(r["diagnosis"]["extracted"] > 0 for r in report["results"]),
        "verified": sum(r["diagnosis"]["verified"] > 0 for r in report["results"]),
        "with_matches": sum(r["supporting_reviews"] > 0 for r in report["results"]),
    }
    markdown = render_markdown(report)
    with engine.db.Session.begin() as s:
        existing = s.scalar(select(Report).where(Report.task_id == tid))
        if not existing:
            s.add(Report(task_id=tid, version=1, data=report, markdown=markdown))
        task = s.get(Task, tid)
        task.context = copy.deepcopy(ctx)
        task.phase = "report"
        if task.status not in {"paused", "cancelled"}:
            task.status = "partial" if ctx["errors"] or not report["target"]["met"] else "completed"
        task.version += 1
    engine.db.event(tid, "system", "report_ready", "标签研究报告已生成；每个匹配都可展开完整评价。")
    return report


def render_markdown(report):
    lines = [
        "# GameReach · 玩家标签研究",
        "",
        report["note"],
        "",
        report["interpretation"]["summary"],
        "本次输入：" + report.get("query", ""),
        "数据 / 模型：" + report["data_mode"] + " / " + report["llm_mode"],
        "",
        *["- " + x for x in report.get("unmet", [])],
        "## 预算策略",
        f"本轮档位：{report.get('strategy', {}).get('label', '旧版')}；来源缓存命中 {report.get('savings', {}).get('source_cache_hits', 0)} 次；复用已核验批次 {report.get('savings', {}).get('verified_batches_reused', 0)} 次。",
        *[f"- {d.get('game_id', '')}：{d['reason']}" for d in report.get("strategy_decisions", [])],
        "## 需求标签",
    ]
    for i in report["interpretation"]["intents"]:
        lines.append(
            f"- {TAGS[i['tag_id']]['label']} · {i['polarity']} · 重要度 {i['priority']}；依据：{i['source_quote']}"
        )
    for r in report["results"]:
        lines += [
            "",
            "## " + r["game"]["name"],
            f"{r['group']} · 排序分 {r['rank_score']} · 已读完整评价 {r['reading']['full_read']} / 获取 {r['reading']['fetched']}",
            "调查诊断：" + r["diagnosis"]["label"] + "。" + r["diagnosis"]["next_step"],
            *[f"- {e['stage']}：{e['code']}" for e in r["diagnosis"]["errors"]],
        ]
        for t in r["tags"]:
            lines.append(f"- {t['label']}：支持 {t['support_count']} / 反对 {t['oppose_count']} / 匹配 {t['match']}")
        for c in r["claims"]:
            lines.append("- " + c["claim_text"] + " [" + ", ".join(c["evidence_ids"]) + "]")
    lines += [
        "",
        "## 局限",
        *["- " + x for x in report["assumptions"] + report["unknowns"] + report["errors"]],
        "",
        "## 完整评价快照",
    ]
    reviewed = {x["document_id"] for x in report["reading_ledger"]}
    for d in report["snapshot"]["documents"]:
        if d["kind"] == "reviews" and d["id"] in reviewed:
            lines += [
                "",
                "### " + d["title"],
                f"来源：{d.get('url') or '合成演示'}；抓取：{d['fetched_at']}；正文哈希：{d['content_hash']}",
                d["text"],
            ]
    return "\n".join(lines)
