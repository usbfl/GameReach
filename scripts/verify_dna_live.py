"""Opt-in bounded real DNA smoke. No fixed game facts and no secret output."""

import argparse, asyncio, json, sys, uuid, re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.config import Settings
from app.db import DB, now, Task
from app.tools import Registry, Gate, redact
from app.engine import Engine
from app.schemas import ResearchRequest
from app.dna.projection import current_features


async def main():
    p = argparse.ArgumentParser(
        description="【由你指定】真实 IGDB 数字 ID；--allow-model 明确允许最多 4 次模型调用。"
    )
    p.add_argument("--igdb-id", type=int)
    p.add_argument("--allow-model", action="store_true")
    args = p.parse_args()
    run_id = "dna-live-" + uuid.uuid4().hex[:10]
    output = {
        "run_id": run_id,
        "checked_at": now(),
        "results": {
            k: {"status": "skipped"} for k in ["model", "igdb", "steam", "dna"]
        },
    }
    s = Settings(
        data_mode="live",
        llm_mode="live",
        max_candidates=1,
        max_investigated_games=1,
        max_external_requests=6,
        max_llm_calls=4,
        max_dna_followup_rounds=0,
        max_retries_per_call=0,
        max_active_seconds=90,
    )
    reason = "需要 RUN_LIVE_TESTS=1、--allow-model 和由用户指定的 --igdb-id；本次没有执行真实调用。"
    reg = None
    if (
        not s.run_live_tests
        or not args.allow_model
        or not args.igdb_id
        or args.igdb_id <= 0
    ):
        for row in output["results"].values():
            row["reason"] = reason
    elif s.diagnostics():
        for row in output["results"].values():
            row["reason"] = "配置缺失：" + ", ".join(s.diagnostics())
    else:
        db = DB("sqlite:///" + str(ROOT / "artifacts" / f"{run_id}.db"))
        db.create()
        reg = Registry(s, db)
        engine = Engine(s, db, reg)
        gate = Gate(s)
        try:
            gid = f"igdb-{args.igdb_id}"
            result = await reg.call(
                {"tool_name": "get_game_details", "arguments": {"game_id": gid}}, gate
            )
            output["results"]["igdb"] = {
                "status": "passed"
                if result.status == "available" and result.data.get("games")
                else "failed",
                "source_status": result.status,
                "error": result.error_code,
            }
            if result.status == "available" and result.data.get("games"):
                tid = engine.create(
                    ResearchRequest(
                        kind="scout",
                        game_ids=[gid],
                        query="请只据来源核验有无闪避、弹反、局中保存、体力管理、死亡回收；无法判断保持未知。",
                    )
                )
                with db.Session.begin() as session:
                    task = session.get(Task, tid)
                    ctx = dict(task.context)
                    ctx["budget"] = dict(gate.ctx["budget"])
                    task.context = ctx
                for _ in range(100):
                    task = db.task(tid)
                    if task["status"] in ["completed", "partial", "failed", "paused"]:
                        break
                    await engine.step(tid)
                task = db.task(tid)
                output["budget"] = task["context"]["budget"]
                output["task_id"] = tid
                output["results"]["model"] = {
                    "status": "passed" if engine.knowledge.claims(tid) else "failed",
                    "model_calls": output["budget"]["llm_calls"],
                    "errors": task["context"]["errors"],
                }
                docs = engine.knowledge.documents(task["context"]["document_ids"])
                steam = [d for d in docs if "steam" in d["provider"]]
                output["results"]["steam"] = {
                    "status": "passed" if steam else "skipped",
                    "documents": len(steam),
                    "reason": None
                    if steam
                    else "没有可用 Steam 样本；可能没有核对 ID 或来源不可用。",
                }
                a = current_features(db, "live", gid)
                b = current_features(db, "live", gid)
                supported = [
                    {
                        "key": k,
                        "value": v["value"],
                        "revision_ids": v["assessment_revision_ids"],
                        "evidence_ids": v["evidence_ids"],
                    }
                    for k, v in a.items()
                    if v.get("value") is not None
                ]
                citations = []
                for f in supported:
                    for eid in f["evidence_ids"]:
                        evidence = engine.knowledge.evidence(eid)
                        citations.append(
                            {
                                "id": eid,
                                "locatable": bool(
                                    evidence
                                    and evidence["document"]["text"][
                                        evidence["start"] : evidence["end"]
                                    ]
                                    == evidence["quote"]
                                ),
                                "url": evidence["document"].get("url")
                                if evidence
                                else None,
                            }
                        )
                output["results"]["dna"] = {
                    "status": "passed"
                    if supported and a == b and all(c["locatable"] for c in citations)
                    else "partial",
                    "features": supported,
                    "citations": citations,
                    "persistent_reread_equal": a == b,
                    "source_page_reachability": "not independently checked; open source links manually",
                    "reason": None
                    if supported
                    else "真实来源未提供足以核验的机制，未预设任何游戏事实。",
                }
        except Exception as exc:
            output["results"]["dna"] = {
                "status": "failed",
                "reason": redact(str(exc))[:800],
            }
        finally:
            await reg.close()
    output.setdefault("budget", {"external_requests": 0, "llm_calls": 0})
    out = ROOT / "artifacts" / f"{run_id}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "path": str(out),
                "results": output["results"],
                "budget": output["budget"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
