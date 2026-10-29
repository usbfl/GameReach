from fastapi import HTTPException
from sqlalchemy import select
from .catalog import VOCABULARY
from .models import Reorder
from .ranking import rank
from ..schemas import ResearchRequest
from ..db import Report, uid
from ..tools import Gate, SourceFailure, BudgetExceeded
from ..reuse import put, digest
from ..ranking import constraints_from_text
from ..discovery import scope_constraints
from .workflow import resolve_cards
from .intent import demo_interpret, ground_interpretation
from .llm import TagLLM


def install_tag_routes(app, db, engine):
    @app.get("/api/tags")
    async def vocabulary():
        return VOCABULARY

    @app.post("/api/tag-intent-preview")
    async def preview(body: ResearchRequest):
        dataset = "synthetic-v1" if engine.s.data_mode == "fixtures" else "live"
        cards = resolve_cards(db, dataset, body.attachments)
        gate = Gate(engine.s)
        if not body.query.strip() and not cards:
            raise HTTPException(422, "请先填写需求或附加卡片")
        try:
            if engine.s.llm_mode == "mock":
                result = demo_interpret(body.query, cards)
            else:
                result = await TagLLM(engine.s, engine.registry.http).run(
                    "interpret", {"query": body.query, "attachments": cards}, gate
                )
                result = ground_interpretation(result, body.query, cards)
        except (SourceFailure, BudgetExceeded, ValueError) as exc:
            raise HTTPException(502, "需求解析失败；未改用模拟结果：" + str(exc)) from None
        pid = uid("intent-")
        put(
            db,
            "intent_preview",
            pid,
            {"signature": digest([body.query, cards, dataset]), "budget": gate.ctx["budget"]},
            1800,
        )
        parsed_constraints = constraints_from_text(body.query, body.constraints.model_dump())
        parsed_constraints["platform"] = scope_constraints(parsed_constraints, body.discovery.model_dump(), body.query)["platform"]
        return {
            "id": pid,
            "interpretation": result.model_dump(),
            "budget": gate.ctx["budget"],
            "constraints": parsed_constraints,
            "custom": [
                {"text": x, "status": "unmapped", "polarity": "want", "priority": 2, "explicit_redline": False}
                for x in result.unmapped
            ],
            "note": "自定义表达尚无目录映射，不保证召回或核验；设为硬约束时保持待确认。",
        }

    @app.post("/api/tag-research", status_code=202)
    async def create(body: ResearchRequest):
        body.recommendation_mode = "player_tags"
        return {"task_id": engine.create(body)}

    @app.post("/api/tag-research/{tid}/rerank")
    async def rerank(tid: str, body: Reorder):
        task = db.task(tid)
        with db.Session() as session:
            row = session.scalar(select(Report).where(Report.task_id == tid).order_by(Report.version.desc()))
            report = row.data if row else None
        if not task or not report or report.get("mode") != "player_tags":
            raise HTTPException(404, "标签研究报告不存在")
        if task["status"] not in {"completed", "partial"}:
            raise HTTPException(409, "请先完成研究")
        allowed = {i["tag_id"] for i in report["interpretation"]["intents"]}
        if not set(body.priorities) <= allowed:
            raise HTTPException(422, "只能调整本次已解释标签的重要度")
        snap = report["snapshot"]
        rows = rank(db, snap["games"], snap["claims"], snap["documents"], task["context"], body.priorities)
        return {
            "results": rows,
            "priorities": body.priorities,
            "note": "仅在本次来源快照上重排，并复查来源可用性；没有搜索或模型调用，原报告不变。",
        }
