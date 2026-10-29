import json
import httpx
import respx
from fastapi.testclient import TestClient
from app.schemas import ResearchRequest
from app.discovery import discovery_action, scope_constraints
from app.ranking import hard_filter
from app.tools import HTTP, Gate, IGDBSource, Registry
from app.config import Settings
from app.main import create_app
from app.engine import Engine


def test_all_default_and_scope_union():
    request = ResearchRequest(query="策略")
    assert request.discovery.model_dump() == {"stores": [], "platforms": []}
    constraints = scope_constraints({"released_only": False}, request.discovery.model_dump())
    g = {"id": "x", "name": "X", "kind": "base", "platforms": ["PlayStation 5"]}
    assert hard_filter(g, constraints)[0] == "pass"
    c = scope_constraints({"released_only": False}, {"stores": ["steam", "gog"], "platforms": ["pc", "ps5"]})
    assert hard_filter(g | {"store_links": {"gog": "https://www.gog.com/"}}, c)[0] == "pass"
    assert hard_filter(g | {"platforms": ["Nintendo Switch"], "store_links": {"steam": "x"}}, c)[0] == "fail"
    assert hard_filter(g, c)[0] == "unknown"


def test_preferences_change_recall_and_reference_is_not_title_search():
    action = discovery_action("喜欢《Hades》这样的肉鸽", [], {"stores": ["steam", "gog"]})
    assert action["arguments"]["q"] == ""
    assert "roguelike" in action["arguments"]["keyword_hints"]
    assert 9 in discovery_action("解谜", [], {})["arguments"]["genre_ids"]
    assert (
        16
        in discovery_action("", [{"feature_key": "combat_mode", "target": "turn_based"}], {})["arguments"]["genre_ids"]
    )
    assert not discovery_action("不要魂类，喜欢解谜", [], {})["arguments"]["keyword_hints"]
    assert "deckbuilding" in discovery_action("牌组构筑与回合制战斗", [], {})["arguments"]["keyword_hints"]
    assert "deckbuilding" in discovery_action("", [{"feature_key": "deckbuilding", "target": True}], {})["arguments"]["keyword_hints"]
    assert "deckbuilding" not in discovery_action("不要牌组构筑", [], {})["arguments"]["keyword_hints"]


def test_preview_no_model_and_validates_scope(stack):
    s, db, _, _ = stack
    with TestClient(create_app(s, db, False)) as c:
        r = c.post("/api/discovery", json={"query": "构筑", "discovery": {"stores": ["steam", "gog"]}})
        assert r.status_code == 200 and r.json()["items"]
        assert r.json()["scope"]["stores"] == ["steam", "gog"]
        assert "演示" in r.json()["note"]
        assert c.post("/api/discovery", json={"discovery": {"stores": ["arbitrary"]}}).status_code == 422


async def test_source_multiselect_query_and_deduplication():
    s = Settings(_env_file=None, igdb_client_id="test", igdb_client_secret="test")
    h = HTTP(s)
    source = IGDBSource(s, h)
    with respx.mock() as mock:
        mock.post("https://id.twitch.tv/oauth2/token").respond(200, json={"access_token": "test"})
        route = mock.post("https://api.igdb.com/v4/games").respond(
            200,
            json=[
                {
                    "id": 1,
                    "name": "A",
                    "platforms": [{"name": "PC (Microsoft Windows)"}],
                    "websites": [
                        {"url": "https://store.steampowered.com/app/1/"},
                        {"url": "https://www.gog.com/game/a"},
                    ],
                },
                {
                    "id": 2,
                    "name": "B",
                    "platforms": [{"name": "Nintendo Switch"}],
                    "websites": [{"url": "https://www.gog.com/game/b"}],
                },
                {"id": 3, "name": "C", "websites": [{"url": "https://store.epicgames.com/p/c"}]},
            ],
        )
        r = await source.call(
            "search_games",
            discovery_action("肉鸽", [], {"stores": ["steam", "gog"], "platforms": ["pc", "switch"]})["arguments"],
            Gate(s),
        )
        query = route.calls[0].request.content.decode()
        assert "platforms = (6,130)" in query and "genres = (" in query and 'keywords.name ~ *"roguelike"*' in query
        assert [g["id"] for g in r["games"]] == ["igdb-1", "igdb-2"]
    await h.client.aclose()


async def test_execution_enforces_user_scope_over_model(stack):
    s, db, _, _ = stack
    s.data_mode = "live"
    s.igdb_client_id = "test"
    s.igdb_client_secret = __import__("pydantic").SecretStr("test")
    registry = Registry(s, db)
    engine = Engine(s, db, registry)
    request = ResearchRequest(query="解谜", discovery={"stores": ["steam"], "platforms": ["pc"]})
    tid = engine.create(request)
    await engine.step(tid)
    ctx = db.task(tid)["context"]
    ctx["queue"] = [{"tool_name": "search_games", "arguments": {"q": "other", "stores": ["gog"], "platforms": ["ps5"]}}]
    engine.commit(tid, ctx, "retrieve")
    with respx.mock() as mock:
        mock.post("https://id.twitch.tv/oauth2/token").respond(200, json={"access_token": "test"})
        route = mock.post("https://api.igdb.com/v4/games").respond(200, json=[])
        await engine.step(tid)
        assert "platforms = (6)" in route.calls[0].request.content.decode()
        ctx = db.task(tid)["context"]
        assert ctx["discovery_info"]["stores"] == ["steam"]
        assert ctx["constraints"]["platform"] == ""
        assert ctx["discovered"]
    await registry.close()


def test_nonsteam_capabilities_and_source_order(stack):
    s, db, registry, engine = stack
    s.data_mode = "live"
    base = {"dataset_id": "live", "platforms": ["PC"], "released": True, "kind": "base", "tags": []}
    db.put("game", "igdb-999", base | {"id": "igdb-999", "name": "First", "external_ids": {"steam": "1"}})
    db.put("game", "igdb-1", base | {"id": "igdb-1", "name": "GOG", "external_ids": {}})
    ctx = {
        "data_mode": "live",
        "dataset_id": "live",
        "candidates": ["igdb-999", "igdb-1"],
        "selected": [],
        "constraints": {"platform": "", "released_only": True},
        "preferences": [],
    }
    assert engine.candidates(ctx, {"kind": "scout", "query": "策略"}) == ["igdb-999", "igdb-1"]
    assert not engine.supports_action({"tool_name": "get_reviews", "arguments": {"game_id": "igdb-1"}})
    assert engine.supports_action({"tool_name": "get_game_details", "arguments": {"game_id": "igdb-1"}})
