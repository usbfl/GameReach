"""Anonymous bounded check of the Steam adapter; no model or private keys.
From backend: uv run python ../scripts/check_steam_reviews.py --appid 1245620
Prints only diagnostics/counts, never review bodies or user identities.
"""

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.config import Settings
from app.db import DB, now
from app.tools import HTTP, Gate, SteamSource, SourceFailure, BudgetExceeded


async def check(appid):
    with tempfile.TemporaryDirectory(prefix="gamereach-review-check-") as tmp:
        settings = Settings(
            _env_file=None,
            database_url="sqlite:///" + tmp + "/check.db",
            max_reviews_per_game=20,
            review_context_limit=20,
            max_retries_per_call=0,
            max_active_seconds=75,
            igdb_client_id="",
            igdb_client_secret="",
            steam_web_api_key="",
            llm_api_key="",
            data_mode="fixtures",
            llm_mode="mock",
        )
        db = DB(settings.resolved_db_url())
        db.create()
        db.put("game", "public-check", {"external_ids": {"steam": appid}})
        http, gate = HTTP(settings), Gate(settings)
        try:
            result = await SteamSource(settings, http, db, True).call(
                "get_reviews", {"game_id": "public-check"}, gate
            )
            docs = result["documents"]
            return {
                "checked_at": now(),
                "appid": appid,
                "status": "partial" if result["sampling"]["errors"] else "passed",
                "fetched": len(docs),
                "complete": sum(d.get("complete") is True for d in docs),
                "rating": result["rating"],
                "sampling": result["sampling"],
                "requests": gate.ctx["budget"]["external_requests"],
                "scope": "Anonymous source check only. No IGDB or LLM quality validation.",
            }
        except (SourceFailure, BudgetExceeded) as exc:
            return {
                "checked_at": now(),
                "appid": appid,
                "status": "failed",
                "error": str(exc),
            }
        finally:
            await http.client.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--appid", required=True)
    args = parser.parse_args()
    if not args.appid.isdigit():
        parser.error("appid must be digits")
    result = asyncio.run(check(args.appid))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["status"] == "passed" else 1)
