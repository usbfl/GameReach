import json
import pytest
import respx
from fastapi.testclient import TestClient
from pydantic import SecretStr
from app.main import create_app
from app.player import FileImport, parse_file, signal, steam_id, infer
from app.tools import IGDBSource, HTTP, Gate
from app.schemas import ResearchRequest, AgentOutput
from app.agents import MockLLM, extract_synthetic_documents
from app.db import Report
from conftest import finish


def sample(gid="fx-02", **kwargs):
    return {
        "platform": "fixture",
        "game_id": gid,
        "name": "合成测试游戏",
        "playtime_minutes": 1800,
        "collections": ["周末"],
        **kwargs,
    }


def test_file_validation_identity_units_and_bias():
    body = FileImport(
        format="csv",
        content="platform,external_id,name,playtime_minutes,recent_minutes,favorite,collections,reaction\nsteam,1,A,,0,true,周末|喜欢,like\n",
    )
    game = parse_file(body)[0]
    assert game["playtime_minutes"] is None and game["collections"] == ["周末", "喜欢"] and game["favorite"]
    assert signal(dict(game, reaction="exclude")) == 0
    assert signal(dict(game, reaction="dislike", playtime_minutes=500000)) == 0
    assert signal(dict(game, playtime_minutes=3000)) == signal(dict(game, playtime_minutes=60000))
    for content in [
        "{}",
        "{",
        json.dumps([{"name": "A", "playtime_minutes": -1}]),
        json.dumps([{"name": "A", "external_id": "1"}, {"name": "A", "external_id": "1"}]),
    ]:
        with pytest.raises(ValueError):
            parse_file(FileImport(format="json", content=content))


@pytest.mark.parametrize(
    "value",
    [
        "https://evil.example/profiles/76561190000000000",
        "https://steamcommunity.com/id/name",
        "76561190000000000?x=1",
        "http://127.0.0.1/",
    ],
)
def test_steam_id_no_arbitrary_fetch(value):
    with pytest.raises(ValueError):
        steam_id(value)


def test_import_draft_confirm_delete_and_revert(stack):
    s, db, _, _ = stack
    with TestClient(create_app(s, db, False)) as c:
        p = c.get("/api/profile").json()
        r = c.post(
            "/api/player/import/file",
            json={"format": "json", "content": json.dumps({"games": [sample(), sample("fx-01", reaction="exclude")]})},
        )
        assert r.status_code == 200
        obj = r.json()
        assert obj["games"][0]["game_id"] == "fx-02"
        draft = c.post(
            "/api/player/imports/" + obj["id"] + "/analyze", json={"selected_ids": ["entry-0", "entry-1"]}
        ).json()
        assert draft["topics"] and not draft["preferences"]  # No cached verified mechanism yet.
        assert len(draft["evidence"]) == 1 and draft["budget"]["external_requests"] == 0
        assert c.get("/api/profile").json()["version"] == p["version"]
        assert (
            c.post("/api/player/drafts/" + draft["id"] + "/confirm", json={"topic_names": ["invented"]}).status_code
            == 422
        )
        applied = c.post(
            "/api/player/drafts/" + draft["id"] + "/confirm",
            json={"topic_names": [draft["topics"][0]["name"]], "style": "vivid"},
        )
        assert applied.status_code == 200
        rev = applied.json()["revision_id"]
        assert applied.json()["profile"]["reading_style"] == "vivid"
        assert c.post("/api/player/drafts/" + draft["id"] + "/confirm", json={}).status_code == 409
        assert c.delete("/api/player/imports/" + obj["id"]).status_code == 200
        assert c.get("/api/player/imports/" + obj["id"]).status_code == 404
        assert c.post("/api/profile/revisions/" + rev + "/revert").status_code == 200
        assert "reading_style" not in c.get("/api/profile").json() or c.get("/api/profile").json()[
            "reading_style"
        ] == p.get("reading_style")


def test_stale_draft_and_unmatched_no_name_merge(stack):
    s, db, _, _ = stack
    with TestClient(create_app(s, db, False)) as c:
        obj = c.post(
            "/api/player/import/file",
            json={
                "format": "json",
                "content": json.dumps({"games": [{"name": "星图议会", "game_id": "fx-02", "external_id": "wrong"}]}),
            },
        ).json()
        assert obj["games"][0]["game_id"] is None
        assert (
            c.post("/api/player/imports/" + obj["id"] + "/analyze", json={"selected_ids": ["bad"]}).status_code == 422
        )
        d = c.post("/api/player/imports/" + obj["id"] + "/analyze", json={"selected_ids": ["entry-0"]}).json()
        assert d["topics"] == [] and d["preferences"] == []
        c.patch("/api/profile", json={})
        assert c.post("/api/player/drafts/" + d["id"] + "/confirm", json={}).status_code == 409


@pytest.mark.parametrize(
    "response,expected",
    [
        ({"response": {}}, 409),
        ({"response": {"game_count": 0}}, 200),
        ({"response": {"game_count": 1, "games": [{"appid": 1, "name": "Game", "playtime_forever": 100}]}}, 200),
    ],
)
def test_steam_contract_private_empty_and_data_minimization(stack, response, expected):
    s, db, _, _ = stack
    s.data_mode = "live"
    s.steam_web_api_key = SecretStr("test-private-key")
    with respx.mock() as mock:
        route = mock.get("https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/").respond(200, json=response)
        with TestClient(create_app(s, db, False)) as c:
            result = c.post("/api/player/import/steam", json={"steam_id": "76561190000000000"})
            assert result.status_code == expected
            assert "test-private-key" not in result.text and "76561190000000000" not in result.text
            assert json.loads(route.calls[0].request.url.params["input_json"])["include_appinfo"] is True
            assert "76561190000000000" not in json.dumps(db.list("player_import"))


def test_import_auth_and_dataset_boundaries(stack):
    s, db, _, _ = stack
    with TestClient(create_app(s, db, False)) as c:
        assert c.post("/api/player/import/steam", json={"steam_id": "76561190000000000"}).status_code == 409
    s.data_mode = "live"
    with TestClient(create_app(s, db, False)) as c:
        assert c.post("/api/player/import/steam", json={"steam_id": "76561190000000000"}).status_code == 409
        assert (
            c.post("/api/player/import/file", json={"format": "json", "content": json.dumps([sample()])}).status_code
            == 422
        )


async def test_exact_id_resolution_ambiguity_and_budget(stack):
    s, db, _, _ = stack
    s.igdb_client_id = "test"
    s.igdb_client_secret = SecretStr("test-secret")
    h = HTTP(s)
    a = IGDBSource(s, h)
    gate = Gate(s)
    with respx.mock() as mock:
        mock.post("https://id.twitch.tv/oauth2/token").respond(
            200, json={"access_token": "test-token", "expires_in": 300}
        )
        route = mock.post("https://api.igdb.com/v4/external_games").respond(
            200,
            json=[
                {"uid": "1", "external_game_source": {"name": "Steam"}, "game": {"id": 10, "name": "A"}},
                {"uid": "2", "external_game_source": {"name": "Steam"}, "game": {"id": 20, "name": "B"}},
                {"uid": "2", "external_game_source": {"name": "Steam"}, "game": {"id": 21, "name": "B DLC"}},
                {"uid": "3", "external_game_source": {"name": "Other"}, "game": {"id": 30, "name": "C"}},
            ],
        )
        result = await a.resolve_steam(["1", "2", "3"], gate)
        assert list(result) == ["1"] and result["1"]["external_ids"]["steam"] == "1"
        assert 'external_game_source.name = "Steam"' in route.calls[0].request.content.decode()
        assert gate.ctx["budget"]["external_requests"] == 2
    await h.client.aclose()


async def test_batched_verification_preserves_prior_decisions_and_required_reviews(stack):
    s, db, registry, engine = stack
    s.llm_mode = "live"
    s.llm_base_url = "https://test.invalid"
    s.llm_model = "test"
    s.llm_api_key = SecretStr("test")
    s.dynamic_routing = False
    seen = []

    class Bounded:
        async def run(self, role, context, gate):
            if role == "planner":
                gate.use("llm_calls")
                return AgentOutput(role=role, summary="Premature stop", proposed_actions=[])
            if role == "researcher":
                gate.use("llm_calls")
                assert len({d["game_id"] for d in context["documents"]}) == 1
                assert len(context["documents"]) <= 12
                out = extract_synthetic_documents(context)
                out.findings = out.findings[:12]
                return out
            assert len(context["claims"]) <= 12
            seen.extend(c["id"] for c in context["claims"])
            return await MockLLM().run(role, context, gate)

    engine.llm = Bounded()
    tid = engine.create(ResearchRequest(kind="scout", game_ids=["fx-01", "fx-02"], query="构筑"))
    t = await finish(engine, tid)
    assert t["status"] == "completed"
    assert any("get_reviews" in a for a in t["context"]["completed_actions"])
    claims = engine.knowledge.claims(tid)
    assert len(claims) > 12 and len(seen) == len(set(seen)) == len(claims)
    assert all(c["evidence_status"] != "pending" for c in claims)
    assert len([c for c in claims if c["evidence_status"] == "supported"]) > 12
    assert t["context"]["dna_followup_rounds"] == 0
    with db.Session() as session:
        report = session.get(Report, (tid, 1)).data
    assert all(r["reading"]["diagnosis"]["reviews"] > 0 for r in report["results"])
    draft = infer(
        db, "synthetic-v1", [dict(sample(), id="entry-0", favorite=True, recent_minutes=None, reaction="like")]
    )
    assert draft["preferences"]


async def test_reading_snapshot_rerank_preserves_attribution(stack):
    from app.dna.projection import rerank
    from app.dna.query import DnaQuery

    s, db, _, engine = stack
    tid = engine.create(ResearchRequest(kind="scout", game_ids=["fx-02"], query="构筑"))
    await finish(engine, tid)
    with db.Session() as session:
        report = session.get(Report, (tid, 1)).data
    r = report["results"][0]
    assert r["reading"]["intro"] and r["reading"]["intro_evidence_ids"]
    assert r["reading"]["strengths"] and r["reading"]["tradeoffs"]
    out = rerank(db, report["candidate_snapshot_id"], "synthetic-v1", DnaQuery.model_validate(report["dna_query"]))
    assert out["results"][0]["reading"] == r["reading"]
    assert out["external_requests"] == out["model_calls"] == 0
    again = engine.create(ResearchRequest(kind="scout", game_ids=["fx-02"], query="构筑"))
    await finish(engine, again)
    with db.Session() as session:
        cached = session.get(Report, (again, 1)).data["results"][0]["reading"]
    assert cached["intro"] == r["reading"]["intro"]
    assert cached["strengths"] == r["reading"]["strengths"]
    assert cached["diagnosis"]["stage"] == "cached"


async def test_unrelated_insufficient_claim_does_not_erase_verified_dna(stack):
    from app.dna.projection import current_features
    from app.schemas import Finding

    _, db, _, engine = stack
    first = engine.create(ResearchRequest(kind="scout", game_ids=["fx-02"]))
    await finish(engine, first)
    assert current_features(db, "synthetic-v1", "fx-02")["deckbuilding"]["value"] is True
    old = next(c for c in engine.knowledge.claims(first) if c.get("feature_key") == "deckbuilding")
    second = engine.create(ResearchRequest(kind="scout", game_ids=["fx-02"]))
    engine.knowledge.extract(
        second,
        [
            Finding(
                game_id="fx-02",
                feature_key="deckbuilding",
                value=None,
                claim_text="本次没有确定新结论",
                document_id=old["document_id"],
                quote=old["quote"],
            )
        ],
        [old["document_id"]],
        "synthetic-v1",
    )
    engine.knowledge.verify(second, verdicts=[])
    assert current_features(db, "synthetic-v1", "fx-02")["deckbuilding"]["value"] is True


@pytest.mark.parametrize(
    "key", ["https://steamcommunity.com/profiles/76561190000000000/", "76561190000000000", "bad key"]
)
def test_steam_mistaken_key_is_rejected_without_network(stack, key):
    s, db, _, _ = stack
    s.data_mode = "live"
    s.steam_web_api_key = SecretStr(key)
    with respx.mock() as mock, TestClient(create_app(s, db, False)) as c:
        result = c.post("/api/player/import/steam", json={"steam_id": "76561190000000000"})
        assert result.status_code == 409
        assert "重启后端" in result.text
        assert key not in result.text
        assert len(mock.calls) == 0


@pytest.mark.parametrize("status,expected", [(403, "密钥"), (503, "稍后重试")])
def test_steam_errors_are_actionable(stack, status, expected):
    s, db, _, _ = stack
    s.data_mode = "live"
    s.steam_web_api_key = SecretStr("test-private-key")
    with respx.mock() as mock, TestClient(create_app(s, db, False)) as c:
        mock.get("https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/").respond(status)
        result = c.post("/api/player/import/steam", json={"steam_id": "76561190000000000"})
        assert result.status_code == 502
        assert expected in result.text
        assert "test-private-key" not in result.text


@pytest.mark.parametrize("error", ["timeout", "proxy"])
def test_steam_transport_errors_do_not_leak_credentials(stack, error):
    import httpx

    s, db, _, _ = stack
    s.data_mode = "live"
    s.steam_web_api_key = SecretStr("test-private-key")
    exc = httpx.ReadTimeout if error == "timeout" else httpx.ProxyError
    with respx.mock() as mock, TestClient(create_app(s, db, False)) as c:
        mock.get("https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/").mock(
            side_effect=exc("test-private-key")
        )
        result = c.post("/api/player/import/steam", json={"steam_id": "76561190000000000"})
        assert result.status_code in (502, 504)
        assert "网络" in result.text and "test-private-key" not in result.text
