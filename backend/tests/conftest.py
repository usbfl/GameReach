import pytest
from app.config import Settings
from app.db import DB
from app.tools import Registry
from app.engine import Engine
from app.schemas import ProfileInput


@pytest.fixture
def stack(tmp_path):
    settings = Settings(
        _env_file=None, database_url="sqlite:///" + str(tmp_path / "test.db"), igdb_min_interval=0, steam_min_interval=0
    )
    db = DB(settings.resolved_db_url())
    db.create()
    registry = Registry(settings, db)
    for g in registry.fixture.games.values():
        db.put("game", g["id"], g)
    db.put("profile", "default", ProfileInput().model_dump() | {"version": 1, "feedback": []})
    return settings, db, registry, Engine(settings, db, registry)


async def finish(engine, tid, stop_paused=True):
    for _ in range(180):
        t = engine.db.task(tid)
        if t["status"] in ["completed", "partial", "failed", "cancelled"] or (stop_paused and t["status"] == "paused"):
            return t
        await engine.step(tid)
    raise AssertionError("workflow did not finish")
