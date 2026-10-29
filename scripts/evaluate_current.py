"""Repeatable authored retrieval stress cases + current ranking contracts, no live services."""

import asyncio
import json
import sys
import time
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.config import Settings
from app.db import DB, now
from app.tools import Registry
from app.engine import Engine, TERMINAL
from app.schemas import ResearchRequest
from app.retrieval import Retrieval
from app.dna.query import compile_query, tri
from app.dna.projection import make_snapshot, rerank
from app.discovery import scope_constraints


async def main():
    db = DB("sqlite:///:memory:")
    db.create()
    settings = Settings(_env_file=None)
    registry = Registry(settings, db)
    engine = Engine(settings, db, registry)
    retrieval = Retrieval(db)
    authored = [
        ("弹反", "Timed parry can stagger enemies."),
        ("牌组构筑", "Deckbuilding is central to each run."),
        ("中途存档", "Mid-run save lets you continue another day."),
        ("策略深度", "Strategic depth comes from choosing between competing objectives."),
        ("半小时", "The session length is about thirty minutes."),
        ("刷装备", "The endgame requires repeated grind for equipment."),
        ("闪避", "A timed dodge avoids the incoming attack."),
        ("捡尸", "Corpse recovery requires returning to where you died."),
        ("parry", "精准弹反会让敌人的防御露出破绽。"),
        ("deckbuilding", "每一局都围绕牌组构筑与选牌展开。"),
        ("dodge", "闪避时机决定能否躲开攻击。"),
        ("mid-run save", "支持局中保存，退出后可以继续这一局。"),
    ]
    cases = []
    for n, (query, gold) in enumerate(authored):
        prefix = "环境与画面的描述，配乐营造气氛。" * 220 if n < 8 else ""
        original = prefix + gold + "\n测试资料到此结束。"
        game = f"retrieval-{n}"
        did = engine.knowledge.ingest(
            [
                {
                    "dataset_id": "retrieval-evaluation",
                    "game_id": game,
                    "provider": "authored",
                    "external_id": str(n),
                    "kind": "reviews",
                    "provenance_type": "synthetic",
                    "title": "Authored bilingual retrieval case",
                    "text": original,
                    "published_at": now(),
                    "url": None,
                }
            ]
        )[0]
        started = time.perf_counter()
        hits = retrieval.search(query, "retrieval-evaluation", game, [did], limit=3)
        cases.append(
            {
                "id": n + 1,
                "query": query,
                "gold_quote": gold,
                "gold_start": len(prefix),
                "prefix_1800_recalled": gold in original[:1800],
                "fts_recalled_at_3": any(gold in h["text"] for h in hits),
                "exact_original_windows": all(original[h["start"] : h["end"]] == h["text"] for h in hits),
                "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                "characters_sent": sum(len(h["text"]) for h in hits),
                "new_model_calls": 0,
                "new_external_requests": 0,
            }
        )
    for game in registry.fixture.games.values():
        db.put("game", game["id"], game)
    tid = engine.create(ResearchRequest(query="战斗技巧和构筑，不需要大量刷装备"))
    for _ in range(180):
        if db.task(tid)["status"] in TERMINAL:
            break
        await engine.step(tid)
    task = db.task(tid)
    assert task["status"] == "completed", task["context"]["errors"]
    games = [db.get("game", gid) for gid in task["context"]["candidates"]]
    snapshot = make_snapshot(db, "synthetic-v1", games, compile_query(), scope_constraints({}, {}))
    ranking = []
    for case in json.loads((ROOT / "evaluations/dna_query_cases.json").read_text()):
        if "text" not in case or case.get("unparsed"):
            continue
        query = compile_query(case["text"])
        result = rerank(db, snapshot["id"], "synthetic-v1", query)
        recommendations = [row for row in result["results"] if row["group"] in ("recommended", "discovery")]
        violations = sum(
            any(tri(p, row["features"]) != "pass" for p in query.must)
            or any(tri(p, row["features"]) != "fail" for p in query.must_not)
            or row["hard_status"] != "pass"
            for row in recommendations
        )
        refs = {eid for row in result["results"] for f in row["features"].values() for eid in f.get("evidence_ids", [])}
        located = 0
        for eid in refs:
            evidence = engine.knowledge.evidence(eid)
            located += bool(
                evidence and evidence["document"]["text"][evidence["start"] : evidence["end"]] == evidence["quote"]
            )
        ranking.append(
            {
                "id": case["id"],
                "hard_violations": violations,
                "citations": len(refs),
                "located_citations": located,
                "model_calls": result["model_calls"],
                "external_requests": result["external_requests"],
            }
        )
    output = {
        "version": "0.7.0",
        "evaluated_at": now(),
        "data": "authored stress cases + synthetic-v1",
        "retrieval": {
            "cases": len(cases),
            "prefix_1800_recall": sum(c["prefix_1800_recalled"] for c in cases) / len(cases),
            "fts_recall_at_3": sum(c["fts_recalled_at_3"] for c in cases) / len(cases),
            "exact_original_windows": all(c["exact_original_windows"] for c in cases),
            "mean_local_latency_ms": round(mean(c["latency_ms"] for c in cases), 3),
            "mean_context_characters": round(mean(c["characters_sent"] for c in cases)),
            "new_external_requests": 0,
            "new_model_calls": 0,
        },
        "ranking": {
            "cases": len(ranking),
            "hard_violations": sum(c["hard_violations"] for c in ranking),
            "citation_locatable_rate": sum(c["located_citations"] for c in ranking)
            / sum(c["citations"] for c in ranking),
            "new_model_calls": 0,
            "new_external_requests": 0,
        },
        "common_fixture_investigation": task["context"]["budget"],
        "human_semantic_support_rate": None,
        "real_game_relevance": None,
        "limitations": [
            "手工构造的双语与长文位置压力样本，并非真实评论随机样本。",
            "逐字可定位不等于语义正确；语义流程由契约测试验证，真实人工支持率尚未测量。",
            "排序检查只证明当前合成案例遵守规则，不估计真实市场召回率或个人喜好准确率。",
            "本地延迟是当前环境单次测量，不代表大规模数据或真实模型延迟。",
        ],
        "retrieval_cases": cases,
        "ranking_cases": ranking,
    }
    out = ROOT / "evaluations/current_results.json"
    out.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    await registry.close()
    print(
        json.dumps(
            {k: output[k] for k in ["retrieval", "ranking", "common_fixture_investigation"]},
            ensure_ascii=False,
            indent=2,
        )
    )
    if (
        output["retrieval"]["fts_recall_at_3"] != 1
        or output["ranking"]["hard_violations"]
        or output["ranking"]["citation_locatable_rate"] != 1
    ):
        raise SystemExit("Current regression failed")


if __name__ == "__main__":
    asyncio.run(main())
