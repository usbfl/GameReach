"""Real end-to-end verification. Uses the supplied env unchanged; isolated test database.

python scripts/verify_recommendations_live.py --env-file .env --allow-model
Costs actual provider requests. No credentials, raw reviews or personal libraries exported.
"""

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.config import Settings
from app.db import DB, Report, now
from app.engine import Engine, TERMINAL
from app.schemas import ResearchRequest
from app.tools import Registry, Gate
from app.dna.query import DnaQuery, Predicate
from app.dna.projection import rerank
from app.quality import public_link


async def verify(args):
    work = Path(tempfile.mkdtemp(prefix="gamereach-live-08-"))
    settings = Settings(_env_file=args.env_file, database_url="sqlite:///" + str(work / "test.db"))
    if not args.allow_model or not settings.run_live_tests:
        raise SystemExit("需要 --allow-model 和 RUN_LIVE_TESTS=1；这会产生真实模型/API调用。")
    if settings.data_mode != "live" or settings.llm_mode != "live" or settings.diagnostics():
        raise SystemExit("需 DATA_MODE=live、LLM_MODE=live 及完整凭据；未切换为演示。")
    db = DB(settings.resolved_db_url())
    db.create()
    registry = Registry(settings, db)
    engine = Engine(settings, db, registry)
    output = {
        "checked_at": now(),
        "real_calls": True,
        "model": settings.llm_model,
        "config_overrides": ["DATABASE_URL（仅隔离验证数据）"],
        "database": str(work / "test.db"),
    }
    try:
        ids = []
        if args.exact_name:
            gate = Gate(settings)
            found = await registry.call({"tool_name": "search_games", "arguments": {"q": args.exact_name}}, gate)
            ids = [g["id"] for g in found.data.get("games", []) if g["name"].casefold() == args.exact_name.casefold()]
            output["setup_budget"] = gate.ctx["budget"]
            if not ids:
                raise SystemExit("真实目录未返回同名游戏；不会猜测 ID。")
        request = ResearchRequest(
            query=args.query, game_ids=ids[:1], discovery={"stores": args.stores, "platforms": []}
        )
        tid = engine.create(request)
        for _ in range(240):
            task = db.task(tid)
            if task["status"] in TERMINAL or task["status"] == "paused":
                break
            print(
                json.dumps({"phase": task["phase"], "model_calls": task["context"]["budget"]["llm_calls"]}), flush=True
            )
            await engine.step(tid)
        task = db.task(tid)
        with db.Session() as session:
            saved = session.get(Report, (tid, 1))
            if not saved:
                raise RuntimeError("没有产生报告；status=" + task["status"])
            report = saved.data
        rows = report["results"]
        output.update(
            task_id=tid,
            status=task["status"],
            budget=report["budget"],
            errors=report["errors"],
            query=args.query,
            stores=args.stores,
            candidates=report["candidate_count"],
            withheld=report["withheld_candidates"],
            policy=report["quality_policy"],
            model_usage=report.get("model_usage"),
            model_protocol_errors=report.get("model_protocol_errors", []),
            dna_query=report.get("dna_query"),
            discovery_info=report.get("discovery_info"),
            investigation_plan=report.get("investigation_plan"),
            outcome=report.get("outcome"),
            recommendations=[],
        )
        for row in rows:
            evidence_ids = set(row["reading"]["intro_evidence_ids"])
            for item in row["reading"]["strengths"] + row["reading"]["tradeoffs"] + row["reading"]["audience"]:
                evidence_ids.update(item["evidence_ids"])
            evidence_ids.update(e for p in row["portrait"] if p["known"] for e in p["evidence_ids"])
            citations = []
            for eid in evidence_ids:
                source = engine.knowledge.evidence(eid)
                valid = bool(
                    source
                    and source["document"]["text"][source["start"] : source["end"]] == source["quote"]
                    and public_link(source["document"].get("url"))
                )
                citations.append(
                    {
                        "id": eid,
                        "locatable": valid,
                        "provider": source["document"]["provider"] if source else None,
                        "url": source["document"].get("url") if source else None,
                    }
                )
            assert citations and all(c["locatable"] for c in citations)
            assert row["qualification"]["eligible"] and row["hard_status"] == "pass"
            output["recommendations"].append(
                {
                    "id": row["game"]["id"],
                    "name": row["game"]["name"],
                    "source_url": row["game"].get("source_url"),
                    "store_links": row["game"].get("store_links"),
                    "counts": row["reading"]["diagnosis"],
                    "intro": row["reading"]["intro"],
                    "strengths": [v["text"] for v in row["reading"]["strengths"]],
                    "tradeoffs": [v["text"] for v in row["reading"]["tradeoffs"]],
                    "portrait": [p["text"] for p in row["portrait"] if p["known"]],
                    "story": row["story"],
                    "citations": citations,
                }
            )
        query = DnaQuery.model_validate(report["dna_query"])
        same = rerank(db, report["candidate_snapshot_id"], "live", query)
        assert [r["game"]["id"] for r in same["results"]] == [r["game"]["id"] for r in rows]
        query.must += [
            Predicate(feature_key="deckbuilding", target=True),
            Predicate(feature_key="deckbuilding", target=False),
        ]
        strict = rerank(db, report["candidate_snapshot_id"], "live", query)
        assert strict["results"] == [] and strict["model_calls"] == strict["external_requests"] == 0
        output["checks"] = {
            "citation_locations": "passed" if rows else "not_applicable_no_recommendations",
            "only_complete_cards": "passed",
            "same_query_rerank": "passed",
            "contradictory_constraints_exclude_all": "passed",
            "real_recommendations_produced": bool(rows),
        }
    finally:
        await registry.close()
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(path),
                "status": output["status"],
                "recommended": len(output["recommendations"]),
                "checks": output["checks"],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--env-file", default=str(ROOT / ".env"))
    p.add_argument("--allow-model", action="store_true")
    p.add_argument("--exact-name", default="")
    p.add_argument("--query", default="喜欢回合制战斗和牌组构筑，想了解具体玩法和体验取舍")
    p.add_argument("--stores", nargs="*", choices=["steam", "gog", "epic", "itch"], default=[])
    p.add_argument("--output", default=str(ROOT / "artifacts/recommendations-live.json"))
    asyncio.run(verify(p.parse_args()))
