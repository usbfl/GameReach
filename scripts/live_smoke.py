"""Opt-in real connection tests. No credentials in logs; never auto-falls back."""

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.config import Settings
from app.db import DB, now
from app.tools import Registry, IGDBSource, SteamSource, Gate, SourceFailure, BudgetExceeded, redact
from app.agents import LiveLLM


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--components", default="model,igdb,steam_news,steam_reviews")
    parser.add_argument("--steam-app-id", type=int, help="【由你指定】要测试的真实 Steam AppID")
    parser.add_argument("--query", default="Hades")
    parser.add_argument("--store", choices=["all", "steam", "gog", "epic", "itch"], default="all")
    parser.add_argument("--platform", choices=["all", "pc", "switch", "ps5", "xbox_series"], default="all")
    args = parser.parse_args()
    s = Settings(max_external_requests=8, max_llm_calls=2, max_active_seconds=60)
    db = DB("sqlite:///:memory:")
    db.create()
    reg = Registry(s, db)
    output = {"run_id": "live-smoke-"+uuid.uuid4().hex[:10],"checked_at": now(), "opt_in": s.run_live_tests, "results": {}}
    for component in args.components.split(","):
        row = {"status": "skipped", "reason": "RUN_LIVE_TESTS 未设为 1", "external_requests": 0}
        gate = Gate(s)
        if s.run_live_tests:
            try:
                if component == "model":
                    result = await LiveLLM(s, reg.http).run(
                        "planner",
                        {
                            "query": "比较虚构游戏的招架机制；当前无证据，请保留未知。",
                            "round": 1,
                            "suggested_actions": [],
                        },
                        gate,
                    )
                    row = {
                        "status": "passed",
                        "model": s.llm_model,
                        "role": result.role,
                        "usage": gate.ctx.get("model_usage"),
                    }
                elif component == "igdb":
                    if not s.igdb_client_id or not s.igdb_client_secret.get_secret_value():
                        row = {"status": "skipped", "reason": "缺少 IGDB_CLIENT_ID / IGDB_CLIENT_SECRET"}
                    else:
                        r = await IGDBSource(s, reg.http).call("search_games", {"q": args.query, "store": args.store, "platform": args.platform}, gate)
                        row = {"status": "passed", "games_returned": len(r["games"]),
                               "search_info": r.get("search_info"),
                               "games": [{k: game.get(k) for k in ["id", "name", "external_ids", "store_links", "source_url", "provenance_type"]} for game in r["games"]]}
                elif component in ["steam_news", "steam_reviews"]:
                    if not args.steam_app_id:
                        row = {"status": "skipped", "reason": "需由你使用 --steam-app-id 指定真实游戏 ID"}
                    else:
                        db.put("game", "live-smoke", {"external_ids": {"steam": str(args.steam_app_id)}})
                        source = SteamSource(s, reg.http, db, component == "steam_reviews")
                        r = await source.call(source.capabilities[0], {"game_id": "live-smoke"}, gate)
                        row = {"status": "passed", "documents_returned": len(r["documents"])}
                else:
                    row = {"status": "skipped", "reason": "未知 component"}
            except (SourceFailure, BudgetExceeded) as exc:
                row = {
                    "status": "skipped" if str(exc) == "llm_requires_config" else "failed",
                    "reason": redact(str(exc)),
                }
        row["external_requests"] = gate.ctx["budget"]["external_requests"]
        output["results"][component] = row
    await reg.close()
    (ROOT / "artifacts").mkdir(exist_ok=True)
    (ROOT / "artifacts" / (output["run_id"]+".json")).write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
