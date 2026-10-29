import asyncio
import copy
import json
import time
from sqlalchemy import select
from .db import Task, Report, HumanAction, now, uid
from .schemas import AgentOutput, Action, FEATURE_LABELS
from .ranking import extract_preferences, constraints_from_text, hard_filter
from .knowledge import Knowledge
from .retrieval import Retrieval
from .agents import MockLLM, LiveLLM, PROMPT_VERSION, extract_synthetic_documents
from .discovery import discovery_action, scope_constraints
from .dna.query import DnaQuery, compile_query, as_legacy, leaves, known
from .dna.projection import current_features, make_snapshot, local_recall
from .dna.routes import add_references
from .tools import Gate, BudgetExceeded, Stopped, SourceFailure, redact
from .quality import quality_policy, publishable, assess_card, exploration_outcome

TERMINAL = {"completed", "partial", "failed", "cancelled"}
ROLE_PHASE = {"plan": "planner", "extract": "researcher", "verify": "verifier"}


def signature(action):
    return json.dumps(Action.model_validate(action).model_dump(), sort_keys=True)


class Engine:
    def __init__(self, settings, db, registry):
        self.s, self.db, self.registry = settings, db, registry
        self.knowledge = Knowledge(db)
        self.retrieval = Retrieval(db)
        self.llm = MockLLM() if settings.llm_mode == "mock" else LiveLLM(settings, registry.http)

    def create(self, request):
        if request.recommendation_mode == "player_tags":
            from .tags.workflow import create
            return create(self, request)
        missing = self.s.diagnostics()
        if missing:
            raise ValueError("需要由你配置：" + ", ".join(missing))
        if request.demo_fault != "none" and (self.s.data_mode != "fixtures" or not self.s.enable_demo_controls):
            raise ValueError("故障注入仅限演示模式")
        dataset = "synthetic-v1" if self.s.data_mode == "fixtures" else "live"
        for gid in request.game_ids:
            game = self.db.get("game", gid)
            if not game or game["dataset_id"] != dataset:
                raise ValueError("游戏未收录或不属于当前数据集：" + gid)
        if request.dna_query and request.dna_query.discovery_scope.model_dump() != request.discovery.model_dump():
            raise ValueError("DNA 查询与平台范围不一致")
        # Validate before creating or scheduling any external work.
        validated = request.dna_query or compile_query(
            request.query,
            request.preferences,
            request.discovery.model_dump(),
            self.db.get("profile", "default", {}).get("preferences", []),
        )
        request.dna_query = add_references(self.db, dataset, validated)
        tid = uid("task-")
        profile = self.db.get("profile", "default", {"preferences": [], "constraints": {}})
        if "constraints" not in request.model_fields_set and profile.get("constraints"):
            from .schemas import Constraints

            request.constraints = Constraints.model_validate(profile["constraints"])
        ctx = {
            "task_id": tid,
            "dna_query": request.dna_query.model_dump(),
            "dna_followup_rounds": 0,
            "dataset_id": dataset,
            "data_mode": self.s.data_mode,
            "llm_mode": self.s.llm_mode,
            "prompt_version": PROMPT_VERSION,
            "profile_snapshot": self.db.get("profile", "default", {"preferences": [], "constraints": {}}),
            "budget": {
                "tool_calls": 0,
                "external_requests": 0,
                "llm_calls": 0,
                "planning_rounds": 0,
                "active_seconds": 0,
            },
            "candidates": [],
            "selected": request.game_ids,
            "document_ids": [],
            "completed_actions": [],
            "queue": [],
            "gaps": [],
            "errors": [],
            "human_rejected_ids": [],
            "pending_human": None,
            "coverage_threshold": self.s.min_coverage,
            "quality_policy": quality_policy(self.s, dataset),
            "verifier_enabled": self.s.enable_verifier,
            "dynamic_routing": self.s.dynamic_routing,
        }
        import re

        ctx["quality_policy"]["unresolved_requirements"] = [
            part
            for part in request.dna_query.unparsed_fragments
            if re.search(r"必须|不能|不要|不接受|仅限", part) and not re.search(r"中文|预算|不超过.*(?:元|块)", part)
        ]
        with self.db.Session.begin() as s:
            s.add(Task(id=tid, request=request.model_dump(), context=ctx))
        self.db.event(
            tid,
            "system",
            "task_started",
            "任务已进入持久化队列。数据与模型模式已锁定。",
            {"data_mode": self.s.data_mode, "llm_mode": self.s.llm_mode},
        )
        return tid

    def recover(self):
        with self.db.Session.begin() as s:
            for t in s.scalars(select(Task).where(Task.status == "running")):
                ctx = copy.deepcopy(t.context)
                if ctx.get("inflight"):
                    t.status = "paused"
                    ctx["recovery_note"] = (
                        "上次只读调用是否完成不确定；请点击恢复，允许重新调查。已持久化结果不会重复写入。"
                    )
                else:
                    t.status = "queued"
                t.context = ctx
        with self.db.Session() as s:
            ids = [
                t.id for t in s.scalars(select(Task).where(Task.status == "paused")) if t.context.get("recovery_note")
            ]
        for tid in ids:
            self.db.event(tid, "system", "recovery_uncertain", "进程中断：已保留检查点，等待明确恢复。")

    def checkpoint(self, tid, ctx):
        with self.db.Session.begin() as s:
            t = s.get(Task, tid)
            t.context = copy.deepcopy(ctx)
            t.updated_at = now()

    def guard(self, tid):
        t = self.db.task(tid)
        if t["context"]["data_mode"] != self.s.data_mode or t["context"]["llm_mode"] != self.s.llm_mode:
            raise SourceFailure("task_mode_changed_restart_with_original_modes")
        if t["status"] in ["paused", "cancelled"]:
            raise Stopped()

    def commit(self, tid, ctx, phase, status=None):
        with self.db.Session.begin() as s:
            t = s.get(Task, tid)
            t.context = copy.deepcopy(ctx)
            t.phase = phase
            t.version += 1
            t.updated_at = now()
            if t.status not in ["cancelled", "paused"]:
                t.status = status or "running"

    def candidates(self, ctx, request):
        ids = ctx["selected"] if request["kind"] != "scout" else ctx["candidates"]
        games = [self.db.get("game", g) for g in ids]
        games = [g for g in games if g and g["dataset_id"] == ctx["dataset_id"]]
        if request["kind"] == "scout":
            groups = {g["id"]: hard_filter(g, ctx["constraints"])[0] for g in games}
            eligible = [g for g in games if groups[g["id"]] != "fail"]
            ctx["excluded"] = [
                {"game": g, "checks": hard_filter(g, ctx["constraints"])[1]} for g in games if groups[g["id"]] == "fail"
            ]
            text = request["query"].lower()
            recently_unresolved = set()
            if ctx["data_mode"] == "live":
                from datetime import datetime, timedelta, timezone

                cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
                with self.db.Session() as session:
                    history = session.scalars(select(Task).where(Task.created_at >= cutoff).order_by(Task.created_at.desc()).limit(30))
                    for previous in history:
                        if (previous.id == ctx.get("task_id")
                            or previous.context.get("dataset_id") != ctx["dataset_id"]
                            or previous.context.get("prompt_version") != PROMPT_VERSION
                            or previous.context.get("dna_query", {}).get("query_hash") != ctx.get("dna_query", {}).get("query_hash")
                            or previous.request.get("query") != request["query"]):
                            continue
                        report = session.get(Report, (previous.id, 1))
                        for row in report.data.get("withheld_candidates", []) if report else []:
                            if (row.get("diagnosis", {}).get("reviews_read", 0) >= self.s.min_reviews_for_recommendation
                                and {r["code"] for r in row["reasons"]} <= {"constraints", "preference_unknown", "low_fit"}):
                                recently_unresolved.add(row["game_id"])
                if recently_unresolved:
                    ctx.setdefault("assumptions", []).append(
                        "相同条件下近24小时已读足评论却未查清关键条件的游戏，本轮先让出调查名额；不把它们判成不适合。"
                    )

            def priority(g):
                if ctx["data_mode"] == "live":
                    # The source already used query preferences and scope balancing.
                    # Preserve that order; internal IDs are not a relevance signal.
                    return (
                        0 if g.get("external_ids", {}).get("steam") else 1,
                        1 if g["id"] in recently_unresolved else 0,
                        0 if groups[g["id"]] == "pass" else 1,
                        ctx["candidates"].index(g["id"]),
                    )
                # Tags are for a bounded investigation shortlist only, never the final fit score.
                words = {
                    "mechanical_difficulty": ["动作", "技巧", "Hack and slash/Beat 'em up", "Role-playing (RPG)"],
                    "strategic_depth": ["策略", "解谜", "Strategy", "Tactical", "Puzzle"],
                    "build_diversity": ["构筑", "Role-playing (RPG)", "Strategy"],
                    "combat_mode": ["回合制", "Turn-based strategy (TBS)"]
                    if any(p["target"] == "turn_based" for p in ctx["preferences"])
                    else [],
                }
                tags = set(g.get("tags", []))
                score = sum(
                    p["weight"] * len(tags.intersection(words.get(p["feature_key"], []))) for p in ctx["preferences"]
                )
                score += sum(1 for tag in tags if tag.lower() in text)
                return (0 if groups[g["id"]] == "pass" else 1, -score, g["id"])

            limit = self.s.max_investigated_games
            if ctx["data_mode"] == "live" and self.s.llm_mode == "live":
                import math

                cap = self.s.max_reviews_per_game
                review_pages = sum(math.ceil(n / 100) for n in [cap * 3 // 8, cap * 3 // 8, cap - 2 * (cap * 3 // 8)])
                # Catalog + Store + review strata + extraction/verification, with
                # room for the one allowed repair. Do not inevitably over-plan
                # five games when the configured request cap only covers two.
                per_game = 2 + review_pages + 4
                budget = ctx.get("budget", {})
                limit = min(
                    limit,
                    max(0, self.s.max_external_requests - budget.get("external_requests", 0)) // per_game,
                    max(0, self.s.max_llm_calls - budget.get("llm_calls", 0)) // 2,
                )
                ctx["investigation_plan"] = {
                    "game_limit": limit, "external_reserve_per_game": per_game,
                    "note": f"按当前预算优先完整核验 {limit} 款候选，保留一次局部修订余量；其余目录候选不冒充推荐。",
                }
                ctx.setdefault("assumptions", []).append(ctx["investigation_plan"]["note"])
            return [g["id"] for g in sorted(eligible, key=priority)[:limit]]
        return [g["id"] for g in games]

    def supports_action(self, action):
        if self.s.data_mode == "live" and action["tool_name"] in ["get_reviews", "get_updates"]:
            game = self.db.get("game", action["arguments"].get("game_id"), {})
            return bool(game.get("external_ids", {}).get("steam"))
        return True

    def suggested(self, ctx, request):
        done = set(ctx["completed_actions"])

        def action(tool, gid=None, **extra):
            return {"tool_name": tool, "arguments": {**({"game_id": gid} if gid else {}), **extra}}

        def fresh(actions):
            return [a for a in actions if signature(a) not in done and self.supports_action(a)]

        requested = fresh(ctx.get("requested_actions", []))
        if ctx.get("quality_policy") and ctx["data_mode"] == "live":
            sampled = {d["game_id"] for d in self.knowledge.documents(ctx["document_ids"]) if d["kind"] == "reviews"}
            requested = [
                a for a in requested if a["tool_name"] != "get_reviews" or a["arguments"].get("game_id") not in sampled
            ]
        if requested and ctx["dynamic_routing"]:
            return requested

        if not ctx.get("discovered") and request["kind"] == "scout" and not ctx["selected"]:
            if ctx["data_mode"] == "live":
                return fresh(
                    [discovery_action(request["query"], ctx.get("preferences", []), request.get("discovery", {}))]
                )
            return fresh([action("search_games", q="")])
        dna = DnaQuery.model_validate(ctx.get("dna_query", {}))
        targets = [
            leaf
            for root in dna.must + dna.must_not + [s.condition for s in dna.soft_preferences]
            for leaf in leaves(root)
        ]

        def needs_evidence(gid):
            fs = current_features(self.db, ctx["dataset_id"], gid)
            missing = not targets or any(known(fs, p) is None for p in targets) or ctx.get("human_followup_game") == gid
            if not missing and ctx["data_mode"] == "live" and ctx.get("task_id"):
                from .dna.ranking import rank_game
                from .reading import reader_card

                row = rank_game(self.db.get("game", gid), fs, dna, ctx.get("constraints", {}))
                row["reading"] = reader_card(self.db, ctx["task_id"], row)
                missing = any(
                    r["code"] not in {"constraints", "low_fit"}
                    for r in assess_card(row, ctx["quality_policy"])["reasons"]
                )
            return missing

        # Details and the initial review sample belong to the initial investigation.
        # Only later missing-information checks count against the two follow-up rounds.
        pending = [gid for gid in ctx["selected"] if needs_evidence(gid)]
        initial = fresh([action(tool, gid) for gid in pending for tool in ["get_game_details", "get_reviews"]])
        if initial:
            return initial
        if not ctx["dynamic_routing"]:
            return []
        docs = self.knowledge.documents(ctx["document_ids"])
        actions = []
        for gid in ctx["selected"]:
            gd = [d for d in docs if d["game_id"] == gid]
            reviews = [d for d in gd if d["kind"] == "reviews"]
            cursor = ctx.get("review_cursors", {}).get(gid)
            if (
                cursor
                and (ctx["data_mode"] == "fixtures" or not ctx.get("quality_policy"))
                and any(g["game_id"] == gid for g in self.dna_gaps(ctx))
            ):
                actions += fresh([action("get_reviews", gid, cursor=cursor)])
            performance = any(
                any(w in d["text"].lower() for w in ["掉帧", "性能", "stutter", "performance", "crash"])
                for d in reviews
            )
            if (performance and ctx["data_mode"] == "fixtures") or ctx.get("human_followup_game") == gid:
                updates = [d for d in gd if d["kind"] == "updates"]
                a = action("get_updates", gid)
                if signature(a) not in done:
                    actions.append(a)
                elif performance and updates and (ctx["data_mode"] == "fixtures" or not ctx.get("quality_policy")):
                    patches = [
                        d
                        for d in updates
                        if d.get("developer")
                        and d.get("published_at")
                        and any(w in d["text"].lower() for w in ["修复", "fix"])
                    ]
                    if patches:
                        patch = max(patches, key=lambda d: d["published_at"])
                        actions += fresh([action("get_reviews", gid, after=patch["published_at"])])
        return fresh(actions)

    def dna_gaps(self, ctx):
        query = DnaQuery.model_validate(ctx.get("dna_query", {}))
        targets = [(3, p) for root in query.must + query.must_not for p in leaves(root)]
        targets += [(s.weight, p) for s in query.soft_preferences for p in leaves(s.condition)]
        gaps = []
        for gid in ctx["selected"]:
            fs = current_features(self.db, ctx["dataset_id"], gid)
            for priority, p in targets:
                if known(fs, p) is None:
                    gaps.append(
                        {
                            "game_id": gid,
                            "feature_key": p.feature_key,
                            "scope": p.scope,
                            "priority": priority,
                            "reason": "关键条件仍未知或已过期；不能按满足处理。",
                        }
                    )
        ordered = sorted(gaps, key=lambda x: (-x["priority"], x["game_id"], x["feature_key"]))
        unique = {}
        for gap in ordered:
            unique.setdefault((gap["game_id"], gap["feature_key"], json.dumps(gap["scope"], sort_keys=True)), gap)
        return list(unique.values())[:40]

    def retrieval_context(self, ctx, request, gid, allowed_ids=None):
        gaps = [g for g in self.dna_gaps(ctx) if g["game_id"] == gid]
        query = " ".join(g["feature_key"] for g in gaps) + " " + request["query"]
        query += " 核心玩法 优点 缺点 core gameplay loop strengths tradeoffs"
        scopes = [g["scope"] for g in gaps] + [{}]
        contexts, seen = [], set()
        for scope in scopes:
            key = json.dumps(scope, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            contexts.append(self.retrieval.context(query, ctx["dataset_id"], gid, allowed_ids, scope))
        # Keep the catalog explanation and a diverse set of review authors. A long,
        # high-scoring review must not consume every snippet slot.
        ranked = []
        focus_terms = {
            "mechanical_difficulty": "timing reflex reaction precision dodge dash combo combat 操作 读招 时机 闪避 反应",
            "build_diversity": "build weapon boon synergy loadout playstyle variety 构筑 武器 流派 搭配",
            "grind_requirement": "grind grinding farming repetition upgrade resource 刷取 刷装备 重复 肝",
            "death_penalty": "death dying lose loss currency recover progress 死亡 损失 回收 进度",
            "navigation_complexity": "navigation map maze linear direction lost path route 地图 迷路 线性 路线 引导",
            "runback_penalty": "runback checkpoint retry boss walking 重走 跑图 检查点 重试",
        }
        for scope in [json.loads(k) for k in sorted(seen)]:
            focused = [
                self.retrieval.search(focus_terms[g["feature_key"]], ctx["dataset_id"], gid, allowed_ids, scope, limit=12)
                for g in gaps if g["feature_key"] in focus_terms
            ]
            for i in range(12):
                ranked += [bucket[i] for bucket in focused if i < len(bucket)]
            ranked += self.retrieval.search(query, ctx["dataset_id"], gid, allowed_ids, scope, limit=200)
            ranked += self.retrieval.search("", ctx["dataset_id"], gid, allowed_ids, scope, limit=200)
        unique, hashes = {}, set()
        for d in ranked:
            if d["id"] in unique or d["content_hash"] in hashes:
                continue
            if d["kind"] == "reviews" and len(d["text"].strip()) < 40:
                continue
            unique[d["id"]] = d
            hashes.add(d["content_hash"])
        detail_ids = [d["id"] for d in unique.values() if d["kind"] != "reviews"]
        openings = self.retrieval.search("", ctx["dataset_id"], gid, detail_ids, {}, limit=8) if detail_ids else []
        openings = [d for d in openings if d.get("start") == 0]
        openings.sort(key=lambda d: d.get("provider") != "steam_store")
        details = list({(d["id"], d.get("start")): d for d in
                        openings + [d for d in unique.values() if d["kind"] != "reviews"]}.values())[:3]
        buckets = {}
        for d in unique.values():
            if d["kind"] == "reviews":
                key = d.get("sampling_stratum", "positive" if d.get("voted_up") else "negative")
                buckets.setdefault(key, []).append(d)
        reviews = []
        while any(buckets.values()) and len(reviews) < self.s.review_context_limit:
            for bucket in buckets.values():
                if bucket and len(reviews) < self.s.review_context_limit:
                    reviews.append(bucket.pop(0))
        documents = details + reviews
        return {
            "documents": documents,
            "verified_assessments": [c["assessments"] for c in contexts if c["assessments"]],
            "metrics": {
                "method": "sqlite_fts5",
                "chunks": len(documents),
                "characters": sum(len(d["text"]) for d in documents),
                "latency_ms": round(sum(c["metrics"]["latency_ms"] for c in contexts), 2),
                "external_requests": 0,
                "model_calls": 0,
            },
        }

    def prime_local(self, ctx, request):
        # Reusing raw sources still requires extraction + verification. No planner/tool request here.
        if any(request["role_modes"][r] == "human" for r in ["planner", "researcher"]) or ctx.get("human_input"):
            return False
        added = []
        for gid in ctx["selected"]:
            if gid in ctx.get("local_attempted", []):
                continue
            ctx.setdefault("local_attempted", []).append(gid)
            context = self.retrieval_context(ctx, request, gid)
            ids = [d["id"] for d in context["documents"] if d["id"] not in ctx["document_ids"]]
            # Already checked mechanisms can go directly to ranking; don't re-extract them.
            if ids and (
                ctx["data_mode"] == "live"
                or not ctx.get("dna_query")
                or any(g["game_id"] == gid for g in self.dna_gaps(ctx))
            ):
                added += ids
        ctx["document_ids"] = list(dict.fromkeys(ctx["document_ids"] + added))
        return bool(added)

    async def role(self, tid, role, ctx, gate, extra=None):
        t = self.db.task(tid)
        human = ctx.pop("human_input", None)
        if t["request"]["role_modes"][role] == "human" and not human:
            ctx["pending_human"] = {
                "role": role,
                "phase": t["phase"],
                "instructions": {
                    "planner": "确认调查计划，可勾选最多 5 个候选并填写优先调查的问题。",
                    "researcher": "阅读已获取来源，可补充原文资料与标题。用户补充资料不会直接标为外部已证实。",
                    "verifier": "逐项阅读主张。可选中要驳回的主张，并填写补查问题；不选择时驳回当前首条主张。",
                }[role],
            }
            self.commit(tid, ctx, t["phase"])
            with self.db.Session.begin() as s:
                row = s.get(Task, tid)
                if row.status not in TERMINAL:
                    row.status = "paused"
            self.db.event(
                tid, role, "human_required", f"等待人工 {role}：上下文已保存，可以刷新页面。", ctx["pending_human"]
            )
            return None
        ctx["pending_human"] = None
        if human:
            if role == "planner":
                if human.get("game_ids"):
                    ctx["selected"] = human["game_ids"][: self.s.max_investigated_games]
                    ctx["discovered"] = True
                return AgentOutput(
                    role=role,
                    summary="人工计划：" + (human["note"] or "按候选范围继续调查"),
                    proposed_actions=self.suggested(ctx, t["request"]),
                    status="continue",
                )
            if role == "researcher":
                if human.get("source_text"):
                    gid = (human.get("game_ids") or ctx["selected"])[0]
                    ids = self.knowledge.ingest(
                        [
                            {
                                "game_id": gid,
                                "provider": "human",
                                "external_id": human["action_id"],
                                "title": human.get("source_title") or "用户提供资料",
                                "text": human["source_text"],
                                "url": None,
                                "submitted_url": human.get("source_url"),
                                "published_at": None,
                                "language": "unknown",
                                "provenance_type": "user",
                                "dataset_id": ctx["dataset_id"],
                                "kind": "user",
                            }
                        ]
                    )
                    ctx["document_ids"] += ids
                    self.knowledge.extract(
                        tid,
                        [
                            {
                                "game_id": gid,
                                "feature_key": None,
                                "value": None,
                                "claim_text": "用户提供，待外部核验：" + human["source_text"][:100],
                                "document_id": ids[0],
                                "quote": human["source_text"][:500],
                                "assessment_type": "fact",
                            }
                        ],
                        ctx["document_ids"],
                        ctx["dataset_id"],
                    )
                # Human approves source processing; rule parser is a local deterministic utility.
                result = extract_synthetic_documents({"documents": self.knowledge.documents(ctx["document_ids"])})
                result.summary = "人工提交资料；规则提取可定位字段，未知项保留。" + human["note"]
                return result
            claims = self.knowledge.claims(tid)
            rejected = []
            if human["decision"] == "reject":
                rejected = human.get("claim_ids") or ([claims[0]["id"]] if claims else [])
                ctx["human_rejected_ids"] = list(set(ctx["human_rejected_ids"] + rejected))
                target = next(
                    (c["game_id"] for c in claims if c["id"] in rejected),
                    ctx["selected"][0] if ctx["selected"] else None,
                )
                ctx["human_followup_game"] = target
                ctx["gaps"].append("人工要求补查：" + (human["note"] or "该主张支持不足"))
            return AgentOutput(
                role=role,
                summary="人工核验："
                + ("驳回所选主张并要求补查。" if rejected else "接受勾选的主张，未勾选保持未知。")
                + human["note"],
                rejected_claim_ids=rejected,
                verdicts=[
                    {
                        "claim_id": c["id"],
                        "verdict": "rejected"
                        if c["id"] in rejected
                        else "supported"
                        if human.get("decision") == "accept" and c["id"] in human.get("claim_ids", [])
                        else "insufficient",
                        "reason": "用户所勾选主张的逐项核验；未勾选不自动通过：" + human["note"],
                        "evidence_ids": c["evidence_ids"],
                    }
                    for c in claims
                ],
            )
        payload = {
            "query": t["request"]["query"],
            "preferences": ctx.get("preferences", []),
            "dna_query": ctx.get("dna_query"),
            "mechanism_gaps": self.dna_gaps(ctx),
            "constraints": ctx.get("constraints", {}),
            "round": ctx["budget"]["planning_rounds"],
            "selected_games": [self.db.get("game", g) for g in ctx["selected"]],
            "game_capabilities": {
                gid: [
                    cap
                    for cap in ["get_game_details", "get_reviews", "get_updates"]
                    if self.supports_action({"tool_name": cap, "arguments": {"game_id": gid}})
                ]
                for gid in ctx["selected"]
            },
            "gaps": ctx["gaps"],
            **(extra or {}),
        }
        if role in {"researcher", "verifier"}:
            batch_ids = {d["game_id"] for d in payload.get("documents", [])}
            batch_ids.update(c["game_id"] for c in payload.get("claims", []))
            payload["selected_games"] = [g for g in payload["selected_games"] if g["id"] in batch_ids]
            payload["mechanism_gaps"] = [g for g in payload["mechanism_gaps"] if g["game_id"] in batch_ids]
            payload["game_capabilities"] = {
                g: caps for g, caps in payload["game_capabilities"].items() if g in batch_ids
            }
            payload["selected_games"] = [{"id": g["id"], "name": g["name"]} for g in payload["selected_games"]]
            payload["documents"] = [
                {k: d[k] for k in [
                    "id", "game_id", "title", "text", "start", "end", "kind", "provider", "scope",
                    "published_at", "updated_at", "provenance_type",
                ] if k in d}
                for d in payload.get("documents", [])
            ]
        ctx["inflight"] = {
            "kind": "llm",
            "role": role,
            "prompt_version": PROMPT_VERSION,
            "document_ids": ctx["document_ids"],
        }
        self.checkpoint(tid, ctx)
        result = await self.llm.run(role, payload, gate)
        ctx.pop("inflight", None)
        return result

    def record_role(self, tid, result):
        self.db.event(tid, result.role, "agent_message", result.summary, result.model_dump())

    async def step(self, tid):
        t = self.db.task(tid)
        if not t or t["status"] in TERMINAL or t["status"] == "paused":
            return
        if t["request"].get("recommendation_mode") == "player_tags":
            from .tags.workflow import step
            return await step(self, t)
        ctx = copy.deepcopy(t["context"])
        request = t["request"]
        phase = t["phase"]
        started = time.monotonic()
        gate = Gate(self.s, ctx, lambda: self.guard(tid), lambda: self.checkpoint(tid, ctx))
        with self.db.Session.begin() as s:
            s.get(Task, tid).status = "running"
        next_phase = phase
        try:
            gate.guard()
            if phase == "interpret":
                historical = ctx["profile_snapshot"]
                prefs = {p["feature_key"]: p for p in historical.get("preferences", [])}
                prefs.update({p["feature_key"]: p for p in extract_preferences(request["query"])})
                prefs.update({p["feature_key"]: p for p in request["preferences"]})
                ctx["preferences"] = list(prefs.values())
                ctx["constraints"] = constraints_from_text(request["query"], request["constraints"])
                ctx["assumptions"] = [
                    "文本偏好由规则识别（可用表单明确覆盖）。"
                    if ctx["llm_mode"] == "mock"
                    else "结构化偏好优先于文本推断。",
                    "等级 0–4 为解释性尺度；未知项不计为满足。",
                    "候选最多 20 款、详细核验最多 5 款，不代表完整游戏市场。",
                ]
                if "跑图简单" in request["query"]:
                    ctx["assumptions"].append(
                        "‘跑图简单’按寻路负担低理解；若指死亡后短距离重试，可明确填写‘低跑图惩罚’。"
                    )
                if ctx["data_mode"] == "fixtures":
                    import re

                    names = re.findall(r'[《“"]([^》”"]+)[》”"]', request["query"])
                    catalog = [g["name"] for g in self.registry.fixture.games.values()]
                    ctx["gaps"] += [
                        f"参考游戏「{n}」未收录于虚构目录，不从模型记忆补齐。" for n in names if n not in catalog
                    ]
                if request["kind"] == "scout":
                    ctx["constraints"] = scope_constraints(ctx["constraints"], request.get("discovery", {}), request["query"])
                    ctx["assumptions"].append(
                        "探索范围：所选商店内任一、所选运行平台内任一；两组同时满足。空选表示全部。"
                    )
                self.db.event(
                    tid,
                    "planner",
                    "preferences_interpreted",
                    "已识别本次偏好与硬约束，保留解释性假设。",
                    {
                        "preferences": ctx["preferences"],
                        "constraints": ctx["constraints"],
                        "assumptions": ctx["assumptions"],
                    },
                )
                ctx["dna_query"] = (
                    request.get("dna_query")
                    or compile_query(
                        request["query"], request["preferences"], request.get("discovery", {})
                    ).model_dump()
                )
                ctx["preferences"] = as_legacy(DnaQuery.model_validate(ctx["dna_query"]))
                ctx["gaps"] += ctx["dna_query"].get("unparsed_fragments", [])
                next_phase = "report" if ctx["quality_policy"].get("unresolved_requirements") else "plan"
            elif phase == "plan":
                if self.prime_local(ctx, request):
                    self.db.event(
                        tid, "researcher", "local_sources_reused", "先核验本地已有原文；缺口仍存在时再按预算补查。"
                    )
                    next_phase = "extract"
                else:
                    # Count one planning round only after a human wait has been satisfied.
                    pending = ctx.get("pending_human")
                    if not pending:
                        gate.use("planning_rounds")
                    suggested = self.suggested(ctx, request)
                    query = DnaQuery.model_validate(ctx["dna_query"])
                    routine = (
                        ctx["data_mode"] == "live"
                        and request["role_modes"]["planner"] == "ai"
                        and not pending
                        and not ctx.get("human_input")
                        and bool(query.must or query.must_not or query.soft_preferences)
                        and bool(suggested)
                        and all(
                            a["tool_name"] == "search_games"
                            or (a["tool_name"] in {"get_game_details", "get_reviews"}
                                and not a["arguments"].get("after"))
                            for a in suggested
                        )
                    )
                    if routine:
                        # These actions are mandatory and already validated. Asking a
                        # model to copy them wastes budget and can stop before any review.
                        result = AgentOutput(
                            role="planner", proposed_actions=suggested, status="continue",
                            summary="按已识别条件执行必要取数计划；本步由程序安排，不调用规划模型。",
                        )
                    else:
                        result = await self.role(tid, "planner", ctx, gate, {"suggested_actions": suggested})
                    if result is None:
                        return
                    self.record_role(tid, result)
                    if result.preferences and ctx["llm_mode"] == "live":
                        # Model interpretation may fill unrecognized soft interests only.
                        # Today's parsed conditions and saved explicit choices cannot be overwritten.
                        from .dna.query import Predicate, Soft
                        from .dna.ontology import FEATURES

                        query = DnaQuery.model_validate(ctx["dna_query"])
                        roots = query.must + query.must_not + [s.condition for s in query.soft_preferences]
                        used = {p.feature_key for root in roots for p in leaves(root)}
                        for preference in result.preferences:
                            from .discovery import intent_text
                            # A genre/reference is not consent to require all its usual
                            # mechanics. Only explicitly mentioned features fill gaps.
                            mentioned = any(
                                alias.lower() in intent_text(request["query"]).lower()
                                for alias in FEATURES[preference.feature_key]["aliases"]
                            )
                            if preference.feature_key == "build_diversity":
                                import re

                                mentioned |= bool(re.search(r"换.*打法|技能组合|不同.*玩法", intent_text(request["query"])))
                            if not mentioned:
                                continue
                            if preference.feature_key in used or len(roots) >= 12:
                                continue
                            condition = Predicate(
                                feature_key=preference.feature_key,
                                operator="overlap" if FEATURES[preference.feature_key]["type"] == "range" else "eq",
                                target=preference.target,
                                origin="model",
                            )
                            query.soft_preferences.append(Soft(condition=condition, weight=preference.weight))
                            used.add(preference.feature_key)
                            roots.append(condition)
                        ctx["dna_query"] = DnaQuery.model_validate(query.model_dump()).model_dump()
                        ctx["preferences"] = as_legacy(DnaQuery.model_validate(ctx["dna_query"]))
                    actions = []
                    for a in result.proposed_actions:
                        raw = a.model_dump()
                        if a.tool_name == "search_games" and request["kind"] == "scout" and ctx["data_mode"] == "live":
                            raw = discovery_action(
                                request["query"], ctx.get("preferences", []), request.get("discovery", {})
                            )
                        if not self.supports_action(raw):
                            gap = "该游戏没有已核对的 Steam ID，当前无法调查玩家评论或公告；其他商店评论尚未接入。"
                            if gap not in ctx["gaps"]:
                                ctx["gaps"].append(gap)
                            continue
                        if a.tool_name != "search_games" and a.arguments.game_id not in ctx["selected"]:
                            ctx["gaps"].append("规划动作的游戏不在已核对候选范围，已拒绝。")
                            continue
                        if signature(raw) not in ctx["completed_actions"] and signature(raw) not in {
                            signature(x) for x in actions
                        }:
                            actions.append(raw)
                    if request["kind"] == "scout" and not ctx.get("discovered") and not ctx["selected"]:
                        # Candidate discovery is necessary even if a model stops prematurely.
                        actions = self.suggested(ctx, request)
                    # The first review/details pass cannot disappear because an AI planner stops early.
                    # Human planners retain their explicit selection; all calls still obey Gate budgets.
                    if request["role_modes"]["planner"] == "ai":
                        for required in suggested:
                            if (
                                required["tool_name"] in ["get_game_details", "get_reviews"]
                                and not required["arguments"].get("after")
                                and signature(required) not in {signature(x) for x in actions}
                            ):
                                actions.append(required)
                    if ctx["llm_mode"] == "live":
                        order = {gid: i for i, gid in enumerate(ctx["selected"])}
                        actions.sort(key=lambda a: order.get(a["arguments"].get("game_id"), -1))
                    ctx["queue"] = actions
                    ctx["gaps"] += result.missing_information
                    self.db.event(
                        tid,
                        "planner",
                        "plan_updated",
                        f"计划包含 {len(actions)} 项尚未执行的调查。",
                        {"actions": actions},
                    )
                    next_phase = "retrieve" if actions else "rank"
            elif phase == "retrieve":
                if not ctx["queue"]:
                    next_phase = "extract"
                else:
                    a = ctx["queue"][0]
                    if a["tool_name"] == "search_games" and request["kind"] == "scout" and ctx["data_mode"] == "live":
                        a = discovery_action(request["query"], ctx.get("preferences", []), request.get("discovery", {}))
                    ctx["inflight"] = {"kind": "tool", "action": a}
                    self.checkpoint(tid, ctx)
                    result = await self.registry.call(
                        a,
                        gate,
                        lambda kind, summary, payload: self.db.event(tid, "researcher", kind, summary, payload),
                        fault=request["demo_fault"],
                    )
                    ctx.pop("inflight", None)
                    ctx["completed_actions"].append(signature(a))
                    ctx["queue"].pop(0)
                    if result.status != "available":
                        ctx["errors"].append(f"{a['tool_name']}：{result.error_code}")
                        ctx["gaps"].append(f"来源 {result.provider} 不可用，相关事实仍未知。")
                    ids = self.knowledge.ingest(result.data.get("documents", []))
                    ctx["gaps"] += result.data.get("source_warnings", [])
                    if result.data.get("sampling"):
                        ctx.setdefault("review_sampling", []).append(
                            {"game_id": a["arguments"].get("game_id"), **result.data["sampling"]}
                        )
                        ctx["gaps"] += ["评论采样未完成：" + e for e in result.data["sampling"].get("errors", [])]
                    if a["tool_name"] == "get_reviews" and result.status == "available":
                        cursor = result.data.get("cursor")
                        if isinstance(cursor, str) and cursor and cursor != a["arguments"].get("cursor", "*") and ids:
                            ctx.setdefault("review_cursors", {})[a["arguments"]["game_id"]] = cursor
                        else:
                            ctx.setdefault("review_cursors", {}).pop(a["arguments"]["game_id"], None)
                    ctx["document_ids"] = list(dict.fromkeys(ctx["document_ids"] + ids))
                    if ids:
                        self.db.event(
                            tid,
                            "researcher",
                            "evidence_added",
                            f"保存 {len(ids)} 份可追溯来源。",
                            {"document_ids": ids, "sampling": result.data.get("sampling")},
                        )
                    if a["tool_name"] == "search_games":
                        local = local_recall(
                            self.db,
                            ctx["dataset_id"],
                            DnaQuery.model_validate(ctx["dna_query"]),
                            request.get("discovery", {}),
                            self.s.max_candidates,
                        )
                        combined = list({g["id"]: g for g in local + result.data.get("games", [])}.values())[
                            : self.s.max_candidates
                        ]
                        ctx["candidates"] = [g["id"] for g in combined]
                        ctx["cached_dna_count"] = len(local)
                        ctx["selected"] = self.candidates(ctx, request)
                        ctx["discovered"] = True
                        for gid in ctx["selected"]:
                            if not self.supports_action({"tool_name": "get_reviews", "arguments": {"game_id": gid}}):
                                ctx["gaps"].append(
                                    f"{self.db.get('game', gid)['name']}：没有已核对的 Steam ID，当前仅能研究 IGDB 元数据，玩家评论与公告待补充。"
                                )
                        ctx["discovery_info"] = result.data.get("search_info", {})
                        if not ctx["candidates"]:
                            ctx["gaps"].append("本次有限候选检索没有找到结果；可尝试减少平台限制或调整玩法偏好。")
                        next_phase = "plan"
                    elif (
                        ctx["data_mode"] == "live"
                        and a["tool_name"] == "get_game_details"
                        and hard_filter(self.db.get("game", a["arguments"]["game_id"], {}), ctx["constraints"])[0]
                        == "fail"
                    ):
                        # Stop spending reviews/model calls once fresh store data proves a hard exclusion.
                        gid = a["arguments"]["game_id"]
                        ctx["queue"] = [q for q in ctx["queue"] if q["arguments"].get("game_id") != gid]
                        ctx["extracted_documents"] = list(dict.fromkeys(ctx.get("extracted_documents", []) + ids))
                        next_phase = "retrieve" if ctx["queue"] else "rank"
                    elif not ctx["queue"] or (
                        ctx["llm_mode"] == "live"
                        and ctx["queue"][0]["arguments"].get("game_id") != a["arguments"].get("game_id")
                    ):
                        next_phase = "extract"
            elif phase == "extract":
                documents = self.knowledge.documents(ctx["document_ids"])
                batched = ctx["llm_mode"] == "live" and request["role_modes"]["researcher"] == "ai"
                pending = [d for d in documents if d["id"] not in ctx.get("extracted_documents", [])]
                batch = documents
                repair_gid = ctx.pop("repair_game", None)
                if batched and (pending or repair_gid):
                    gid = repair_gid or pending[0]["game_id"]
                    ctx["active_research_game"] = gid
                    retrieved = self.retrieval_context(
                        ctx,
                        request,
                        gid,
                        [d["id"] for d in (documents if repair_gid else pending) if d["game_id"] == gid],
                    )
                    batch = retrieved["documents"]
                    ctx.setdefault("retrieval_runs", []).append(retrieved["metrics"] | {"game_id": gid})
                    self.db.event(
                        tid,
                        "researcher",
                        "context_retrieved",
                        f"选取 {len(batch)} 个与当前问题相关的原文片段。",
                        retrieved["metrics"],
                    )
                result = await self.role(
                    tid,
                    "researcher",
                    ctx,
                    gate,
                    {
                        "documents": batch,
                        "verified_assessments": retrieved["verified_assessments"]
                        if batched and (pending or repair_gid)
                        else [],
                        "repair": ctx.get("repair_context") if repair_gid else None,
                        "reading_style": ctx.get("profile_snapshot", {}).get("reading_style", "plain"),
                    },
                )
                if result is None:
                    return
                self.record_role(tid, result)
                accepted, rejected = self.knowledge.extract(
                    tid, result.findings, ctx["document_ids"], ctx["dataset_id"], windows=batch if batched else None
                )
                ctx["gaps"] += rejected + result.missing_information
                self.db.event(
                    tid,
                    "researcher",
                    "claims_extracted",
                    f"{len(accepted)} 条候选主张有有效引用；{len(rejected)} 条未通过定位校验。",
                )
                next_phase = "verify"
                if batched and (pending or repair_gid):
                    # Considered != read: retain exact input windows and count only documents
                    # actually supplied to a successful extractor call.
                    ctx["reviewed_document_ids"] = list(
                        dict.fromkeys(
                            ctx.get("reviewed_document_ids", []) + [d["id"] for d in batch if d["kind"] == "reviews"]
                        )
                    )
                    ctx.setdefault("reading_windows", []).extend(
                        {k: d[k] for k in ["id", "start", "end"]} for d in batch
                    )
                    ctx["extracted_documents"] = list(
                        dict.fromkeys(
                            ctx.get("extracted_documents", []) + [d["id"] for d in pending if d["game_id"] == gid]
                        )
                    )
                elif not batched:
                    ctx["reviewed_document_ids"] = list(
                        dict.fromkeys(
                            ctx.get("reviewed_document_ids", []) + [d["id"] for d in batch if d["kind"] == "reviews"]
                        )
                    )
            elif phase == "verify":
                claims = self.knowledge.claims(tid)
                batched = ctx["llm_mode"] == "live" and request["role_modes"]["verifier"] == "ai"
                previous_verdicts = ctx.get("verification_verdicts", {}) if batched else {}
                pending_claims = [c for c in claims if c["id"] not in previous_verdicts] if batched else claims
                batch_claims = pending_claims[:12] if batched else claims
                if ctx["verifier_enabled"]:
                    result = await self.role(
                        tid,
                        "verifier",
                        ctx,
                        gate,
                        {
                            "documents": self.retrieval.verification_context(batch_claims)
                            if batched
                            else self.knowledge.documents(list(dict.fromkeys(c["document_id"] for c in batch_claims))),
                            "claims": batch_claims,
                        },
                    )
                    if result is None:
                        return
                    self.record_role(tid, result)
                    ctx["human_rejected_ids"] = list(set(ctx["human_rejected_ids"] + result.rejected_claim_ids))
                    ctx["gaps"] += result.missing_information
                    ctx["requested_actions"] = [
                        a.model_dump()
                        for a in result.proposed_actions
                        if a.tool_name != "search_games" and a.arguments.game_id in ctx["selected"]
                    ]
                    if batched:
                        returned = {
                            v.claim_id: v.model_dump()
                            for v in result.verdicts
                            if v.claim_id in {c["id"] for c in batch_claims}
                        }
                        for c in batch_claims:
                            previous_verdicts[c["id"]] = returned.get(
                                c["id"],
                                {
                                    "claim_id": c["id"],
                                    "verdict": "insufficient",
                                    "reason": "模型未返回这条主张的逐项判决",
                                    "evidence_ids": c["evidence_ids"],
                                },
                            )
                        ctx["verification_verdicts"] = previous_verdicts
                checked = self.knowledge.verify(
                    tid,
                    ctx["human_rejected_ids"],
                    ctx["verifier_enabled"],
                    ctx["llm_mode"] == "live",
                    verdicts=list(previous_verdicts.values())
                    if batched and ctx["verifier_enabled"]
                    else result.verdicts
                    if ctx["verifier_enabled"]
                    else [],
                )
                self.db.event(
                    tid,
                    "verifier",
                    "verification_result",
                    f"完成 {len(checked)} 条主张的证据状态检查。",
                    {
                        "disputed": sum(c["evidence_status"] == "disputed" for c in checked),
                        "rejected": len(ctx["human_rejected_ids"]),
                    },
                )
                if (
                    (not batched or len(pending_claims) <= 12)
                    and not ctx["queue"]
                    and self.suggested(ctx, request)
                    and ctx.get("dna_followup_rounds", 0) < self.s.max_dna_followup_rounds
                ):
                    ctx["dna_followup_rounds"] = ctx.get("dna_followup_rounds", 0) + 1
                    next_phase = "plan"
                else:
                    next_phase = "rank"
                if batched:
                    if ctx["queue"]:
                        next_phase = "retrieve"
                    elif any(did not in ctx.get("extracted_documents", []) for did in ctx["document_ids"]):
                        next_phase = "extract"
                    if ctx["verifier_enabled"] and len(pending_claims) > 12:
                        next_phase = "verify"
                    elif ctx["verifier_enabled"] and ctx.get("active_research_game"):
                        gid = ctx["active_research_game"]
                        from .dna.ranking import rank_game
                        from .reading import reader_card

                        row = rank_game(
                            self.db.get("game", gid),
                            current_features(self.db, ctx["dataset_id"], gid),
                            DnaQuery.model_validate(ctx["dna_query"]),
                            ctx.get("constraints", {}),
                        )
                        row["reading"] = reader_card(self.db, tid, row)
                        quality = assess_card(row, ctx["quality_policy"])
                        repair_codes = {
                            "intro",
                            "strengths",
                            "tradeoffs",
                            "mechanics",
                            "preference_unknown",
                            "citations",
                            "source_diversity",
                        }
                        gaps = [r for r in quality["reasons"] if r["code"] in repair_codes]
                        if (
                            gaps
                            and row["hard_status"] != "fail"
                            and ctx["data_mode"] == "live"
                            and ctx.get("card_repairs", {}).get(gid, 0) < 1
                            and ctx["budget"]["llm_calls"] + 2 <= self.s.max_llm_calls
                            and ctx["budget"]["external_requests"] + 2 <= self.s.max_external_requests
                        ):
                            ctx.setdefault("card_repairs", {})[gid] = 1
                            ctx["repair_game"] = gid
                            ctx["repair_context"] = {
                                "missing_card_fields": gaps,
                                "instruction": "仅修复下面缺失字段。保留已经通过的结论，不重复提取。缩短主张或扩大逐字引用，使quote包含claim的全部依据。没有依据就不输出，不为填卡片猜测。",
                                "failed_claims": [
                                    c for c in checked if c["game_id"] == gid and c["evidence_status"] == "insufficient"
                                ],
                            }
                            next_phase = "extract"
            elif phase == "rank":
                ctx["ranking"] = self.rank(tid, ctx)
                self.db.event(
                    tid, "rules", "ranking_computed", "已执行三态硬过滤和确定性排序；覆盖率与适配度分开显示。"
                )
                next_phase = "report"
            elif phase == "report":
                await self.make_report(tid, ctx)
                return
            ctx["gaps"] = list(dict.fromkeys(ctx["gaps"]))[-60:]
            ctx["budget"]["active_seconds"] += time.monotonic() - started
            self.commit(tid, ctx, next_phase)
        except Stopped:
            ctx["budget"]["active_seconds"] += time.monotonic() - started
            self.checkpoint(tid, ctx)
        except BudgetExceeded as e:
            ctx["budget"]["active_seconds"] += time.monotonic() - started
            ctx["errors"].append(str(e))
            ctx["gaps"].append("预算已耗尽；尚未完成的调查不会被当作成功。")
            ctx.pop("inflight", None)
            self.commit(tid, ctx, "report")
            self.db.event(tid, "system", "budget_exhausted", str(e))
            # Report generation is local and must be allowed even after the active-time budget.
            await self.make_report(tid, ctx)
        except SourceFailure as e:
            ctx["budget"]["active_seconds"] += time.monotonic() - started
            ctx["errors"].append(e.code)
            ctx.pop("inflight", None)
            self.commit(tid, ctx, "report")
            self.db.event(tid, "system", "model_unavailable", "模型调用未成功：" + e.code + "。未切换到模拟模型。")
            await self.make_report(tid, ctx)
        except Exception as e:
            ctx["errors"].append(type(e).__name__)
            self.commit(tid, ctx, phase, "failed")
            self.db.event(
                tid, "system", "task_failed", "执行失败：" + redact(type(e).__name__) + "。请查看服务端测试/运行记录。"
            )
            # Keep traceback local for debugging, never serialize request headers or keys.
            import traceback

            message = redact(traceback.format_exc())
            for secret in [self.s.llm_api_key, self.s.igdb_client_secret, self.s.steam_web_api_key]:
                if secret.get_secret_value():
                    message = message.replace(secret.get_secret_value(), "[REDACTED]")
            print(message)

    def rank(self, tid, ctx):
        query = DnaQuery.model_validate(
            ctx.get("dna_query") or compile_query(preferences=ctx.get("preferences", [])).model_dump()
        )
        ids = list(dict.fromkeys(ctx.get("selected", []) + ctx.get("candidates", [])))[: self.s.max_candidates]
        games = [self.db.get("game", gid) for gid in ids]
        games = [g for g in games if g and g.get("dataset_id") == ctx["dataset_id"]]
        snapshot = make_snapshot(
            self.db, ctx["dataset_id"], games, query, ctx.get("constraints", {}), tid, ctx["coverage_threshold"]
        )
        ctx["dna_snapshot_id"] = snapshot["id"]
        ctx["assessment_revision_ids"] = snapshot["assessment_revision_ids"]
        return snapshot["rows"]

    def risks(self, gid, ctx):
        docs = [d for d in self.knowledge.documents(ctx["document_ids"]) if d["game_id"] == gid]
        risks = []
        reviews = [d for d in docs if d["kind"] == "reviews"]
        if reviews:
            risks.append(f"评论为 {len(reviews)} 个文档样本，不代表全部玩家。")
        perf = [
            d
            for d in reviews
            if any(w in d["text"].lower() for w in ["掉帧", "性能", "stutter", "performance", "crash"])
        ]
        patches = [
            d
            for d in docs
            if d["kind"] == "updates" and d.get("developer") and any(w in d["text"].lower() for w in ["修复", "fix"])
        ]
        if perf:
            risks.append("旧版本样本提及性能问题，不能直接代表当前版本。")
        if patches:
            latest = max((d.get("published_at") or "" for d in patches), default="")
            after = [d for d in reviews if (d.get("published_at") or "") > latest]
            risks.append(
                "开发者宣布修复；"
                + (
                    "存在补丁后样本，但是否针对同一问题及实际效果待验证。"
                    if after
                    else "未取得补丁后样本，实际效果待验证。"
                )
            )
        return risks

    async def make_report(self, tid, ctx):
        t = self.db.task(tid)
        if t["status"] in ["cancelled", "paused"]:
            return
        rows = ctx.get("ranking") or self.rank(tid, ctx)
        claims = self.knowledge.claims(tid)
        for row in rows:
            row["risks"] = self.risks(row["game"]["id"], ctx)
            row["claims"] = [c for c in claims if c["game_id"] == row["game"]["id"]]
            row["unknowns"] = [FEATURE_LABELS[k] for k, f in row["features"].items() if f["value"] is None]
        rows, withheld = publishable(rows, ctx.get("quality_policy"))
        status = "partial" if ctx["errors"] else "completed"
        report = {
            "task_id": tid,
            "dna_snapshot_id": ctx.get("dna_snapshot_id"),
            "candidate_snapshot_id": ctx.get("dna_snapshot_id"),
            "dna_query": ctx.get("dna_query"),
            "query_hash": ctx.get("dna_query", {}).get("query_hash"),
            "assessment_revision_ids": ctx.get("assessment_revision_ids", []),
            "ontology_version": "dna-2",
            "rubric_version": ctx.get("quality_policy", {}).get("rubric_version"),
            "ranking_version": "dna-interval-2",
            "version": 1,
            "created_at": now(),
            "data_mode": ctx["data_mode"],
            "llm_mode": ctx["llm_mode"],
            "dataset_id": ctx["dataset_id"],
            "discovery_scope": t["request"].get("discovery", {}),
            "discovery_info": ctx.get("discovery_info", {}),
            "investigation_plan": ctx.get("investigation_plan"),
            "query": t["request"]["query"],
            "kind": t["request"]["kind"],
            "status": status,
            "assumptions": ctx.get("assumptions", []),
            "preferences": ctx.get("preferences", []),
            "constraints": ctx.get("constraints", {}),
            "results": rows,
            "withheld_candidates": withheld,
            "outcome": exploration_outcome(rows, withheld, ctx["errors"], ctx.get("quality_policy", {})),
            "candidate_count": len(rows) + len(withheld),
            "quality_policy": ctx.get("quality_policy"),
            "excluded_candidates": ctx.get("excluded", []),
            "unknowns": list(dict.fromkeys(ctx["gaps"])),
            "errors": ctx["errors"],
            "budget": ctx["budget"],
            "model_usage": ctx.get("model_usage"),
            "model_protocol_errors": ctx.get("model_protocol_errors", []),
            "cost": "unknown",
            "source_documents": self.knowledge.documents(ctx["document_ids"]),
            "retrieval_runs": ctx.get("retrieval_runs", []),
            "review_sampling": ctx.get("review_sampling", []),
            "note": "全部游戏、评价与公告均为虚构演示数据。"
            if ctx["data_mode"] == "fixtures"
            else "真实来源；引用存在不代表绝对真实。",
            "coverage_threshold": ctx["coverage_threshold"],
        }
        markdown = self.markdown(report)
        with self.db.Session.begin() as s:
            s.merge(Report(task_id=tid, version=1, data=report, markdown=markdown))
            row = s.get(Task, tid)
            row.status = status
            row.phase = "report"
            row.context = ctx
            row.version += 1
            row.updated_at = now()
        self.db.event(
            tid, "system", "report_ready", "报告已保存，可查看证据或下载 Markdown / JSON。", {"status": status}
        )

    def markdown(self, r):
        groups = {
            "recommended": "证据相对充分",
            "discovery": "候选展示（没有机制偏好）",
            "needs_confirmation": "硬约束待确认",
            "insufficient": "证据不足",
            "excluded": "已排除",
        }
        lines = [
            "# GameReach 研究报告",
            "",
            f"- 调查时间：{r['created_at']}",
            f"- 模式：{r['data_mode']} + {r['llm_mode']}；数据集：{r['dataset_id']}",
            f"- 状态：{r['status']}；任务：{r['task_id']}",
            "",
            r["note"],
            "",
            f"需求：{r['query']}",
            "",
            "## 偏好与假设",
        ]
        if r.get("kind") == "scout":
            scope = r.get("discovery_scope", {})
            lines += [
                "- 商店范围：" + (" / ".join(scope.get("stores", [])) or "全部"),
                "- 运行平台范围：" + (" / ".join(scope.get("platforms", [])) or "全部"),
            ]
        lines += ["- " + a for a in r["assumptions"]]
        for p in r["preferences"]:
            lines.append(f"- {FEATURE_LABELS[p['feature_key']]}：目标 {p['target']}，权重 {p['weight']}")
        evidence_ids = []
        for row in r["results"]:
            lines += [
                "",
                f"## {row['game']['name']} · {row['game']['id']}",
                f"分组：{groups[row['group']]}；适配度：{row['fit_score'] if row['fit_score'] is not None else '未知'}；证据覆盖：{row['coverage'] if row['coverage'] is not None else '未请求偏好'}",
                "",
            ]
            reading = row.get("reading", {})
            if reading.get("intro"):
                refs = " ".join(f"[{eid}](#{eid})" for eid in reading.get("intro_evidence_ids", []))
                lines += ["怎么玩：" + reading["intro"] + " " + refs]
                evidence_ids += reading.get("intro_evidence_ids", [])
            for field, title in [("strengths", "玩家提到的优点"), ("tradeoffs", "可能不合口味的地方")]:
                for item in reading.get(field, []):
                    refs = " ".join(f"[{eid}](#{eid})" for eid in item["evidence_ids"])
                    lines.append(f"- {title}：{item['text']} {refs}")
                    evidence_ids += item["evidence_ids"]
            if reading.get("diagnosis"):
                lines += ["资料说明：" + reading["diagnosis"]["message"]]
            for check in row["checks"]:
                lines.append(f"- 硬约束 {check['constraint']}：{check['status']}")
            for k, f in row["features"].items():
                refs = " ".join(f"[{eid}](#" + eid + ")" for eid in f["evidence_ids"])
                evidence_ids += f["evidence_ids"]
                lines.append(
                    f"- {FEATURE_LABELS[k]}：{f['value'] if f['value'] is not None else '未知'}；{f['evidence_status']} {refs}"
                )
            lines += ["- 风险：" + risk for risk in row["risks"]]
            for c in row["claims"]:
                if c["feature_key"] is None:
                    evidence_ids += c["evidence_ids"]
                    lines.append(
                        f"- 归因主张（{c['evidence_status']}）：{c['claim_text']} "
                        + " ".join(f"[{e}](#{e})" for e in c["evidence_ids"])
                    )
            if row["unknowns"]:
                lines.append("- 未知项：" + "、".join(row["unknowns"]))
        lines += ["", "## 调查缺口与错误"] + ["- " + v for v in r["unknowns"] + r["errors"]]
        lines += ["", "## 来源与短片段（完整证据请在工作台查看）"]
        for eid in dict.fromkeys(evidence_ids):
            e = self.knowledge.evidence(eid)
            if e:
                d = e["document"]
                lines += [
                    "",
                    f"### {eid}",
                    f"{d['title']} · {d['provider']} · {d['provenance_type']}",
                    f"发布时间：{d.get('published_at') or '未知'}；抓取：{d['fetched_at']}",
                    f"来源：{d.get('url') or '本地合成证据'}",
                    f"短片段：{e['quote'][:100]}",
                ]
        lines += [
            "",
            "## 预算",
            json.dumps(r["budget"], ensure_ascii=False),
            "费用：unknown；可用模型 usage 见 JSON 导出。",
        ]
        return "\n".join(lines)

    async def worker(self):
        while True:
            with self.db.Session() as s:
                ids = [
                    t.id
                    for t in s.scalars(
                        select(Task).where(Task.status.in_(["queued", "running"])).order_by(Task.created_at)
                    )
                ]
            for tid in ids:
                await self.step(tid)
                await asyncio.sleep(0.02)
            await asyncio.sleep(0.08)

    def human(self, tid, body):
        with self.db.Session.begin() as s:
            prior = s.get(HumanAction, body.action_id)
            if prior:
                if prior.task_id != tid or prior.data != body.model_dump():
                    raise ValueError("action_id 已被其他操作使用")
                return {"status": "already_applied"}
            t = s.get(Task, tid)
            if not t or t.status != "paused" or not t.context.get("pending_human") or t.version != body.state_version:
                raise ValueError("待办状态已变化，请刷新后提交")
            ctx = copy.deepcopy(t.context)
            for gid in body.game_ids:
                g = self.db.get("game", gid)
                if not g or g["dataset_id"] != ctx["dataset_id"]:
                    raise ValueError("候选不属于本次数据集")
                if hard_filter(g, ctx.get("constraints", {}))[0] == "fail":
                    raise ValueError("人工候选也不能绕过硬约束")
            known = {c["id"] for c in self.knowledge.claims(tid)}
            if not set(body.claim_ids) <= known:
                raise ValueError("主张 ID 不属于本任务")
            ctx["human_input"] = body.model_dump()
            t.context = ctx
            t.status = "queued"
            t.version += 1
            s.add(HumanAction(id=body.action_id, task_id=tid, state_version=body.state_version, data=body.model_dump()))
        self.db.event(
            tid,
            "human",
            "task_resumed",
            "人工输入已通过校验，任务恢复。",
            {"decision": body.decision, "note": body.note},
        )
        return {"status": "resumed"}
