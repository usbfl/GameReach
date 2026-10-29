import json
import httpx
import pytest
import respx
from pydantic import SecretStr
from app.config import Settings
from app.tools import HTTP, Gate, SourceFailure, BudgetExceeded, IGDBSource, SteamSource
from app.agents import LiveLLM


@pytest.mark.parametrize(
    "status,code",
    [(401, "requires_auth"), (403, "requires_auth"), (429, "rate_limited"), (500, "http_500"), (302, "http_302")],
)
async def test_http_errors(status, code):
    s = Settings(_env_file=None, max_retries_per_call=0)
    h = HTTP(s)
    g = Gate(s)
    with respx.mock() as mock:
        mock.get("https://api.steampowered.com/test").respond(status, headers={"Retry-After": "10"})
        with pytest.raises(SourceFailure) as exc:
            await h.request(g, "GET", "https://api.steampowered.com/test")
        assert exc.value.code == code and g.ctx["budget"]["external_requests"] == 1
    await h.client.aclose()


async def test_timeout_invalid_json_size():
    s = Settings(_env_file=None, max_retries_per_call=0)
    h = HTTP(s)
    with respx.mock() as mock:
        route = mock.get("https://example.com/")
        route.mock(side_effect=httpx.ReadTimeout("timeout"))
        with pytest.raises(SourceFailure):
            await h.request(Gate(s), "GET", "https://example.com/")
        route.respond(200, text="not JSON")
        with pytest.raises(SourceFailure, match="invalid_json"):
            await h.request(Gate(s), "GET", "https://example.com/")
        route.respond(200, content=b"x" * 2_000_001)
        with pytest.raises(SourceFailure, match="response_too_large"):
            await h.request(Gate(s), "GET", "https://example.com/")
    await h.client.aclose()


async def test_retries_share_budget():
    s = Settings(_env_file=None, max_external_requests=1)
    h = HTTP(s)
    g = Gate(s)
    with respx.mock() as mock:
        route = mock.get("https://example.com/").respond(500)
        with pytest.raises(BudgetExceeded):
            await h.request(g, "GET", "https://example.com/")
        assert route.call_count == 1
    await h.client.aclose()


async def test_igdb_auth_mapping_token_cache(stack):
    s = stack[0]
    s.igdb_client_id = "test-id"
    s.igdb_client_secret = SecretStr("test-secret")
    h = HTTP(s)
    a = IGDBSource(s, h)
    g = Gate(s)
    with respx.mock() as mock:
        auth = mock.post("https://id.twitch.tv/oauth2/token").respond(
            200, json={"access_token": "test-token", "expires_in": 3600}
        )
        games = mock.post("https://api.igdb.com/v4/games").respond(
            200,
            json=[
                {
                    "id": 10,
                    "name": "Example",
                    "platforms": [{"name": "PC (Microsoft Windows)"}],
                    "game_type": {"type": "Main Game"},
                    "game_status": {"status": "Released"},
                    "websites": [{"url": "https://store.steampowered.com/app/123/"}],
                    "language_supports": [
                        {"language": {"locale": "zh-CN"}, "language_support_type": {"name": "Interface"}}
                    ],
                }
            ],
        )
        result = await a.call("search_games", {"q": 'test"; malicious'}, g)
        assert result["games"][0]["external_ids"]["steam"] == "123"
        assert result["games"][0]["languages"] == ["zh-CN"]
        assert result["games"][0]["kind"] == "base"
        assert 'test\\"; malicious' in games.calls[0].request.content.decode()
        await a.call("get_game_details", {"game_id": "igdb-10"}, g)
        assert auth.call_count == 1 and g.ctx["budget"]["external_requests"] == 3
    await h.client.aclose()


async def test_steam_review_new_contract_and_privacy(stack):
    s, db, _, _ = stack
    s.steam_min_interval = 0
    h = HTTP(s)
    a = SteamSource(s, h, db, True)
    db.put("game", "igdb-1", {"external_ids": {"steam": "123"}})
    with respx.mock() as mock:
        route = mock.get("https://api.steampowered.com/IUserReviewsService/GetAppReviews/v1/").respond(
            200,
            json={
                "response": {
                    "reviews": [
                        {
                            "recommendationid": "r1",
                            "review": "good",
                            "author": {"steamid": "PRIVATE"},
                            "timestamp_created": 1700000000,
                            "language": "english",
                        }
                    ],
                    "cursor": "abc+/=",
                }
            },
        )
        r = await a.call("get_reviews", {"game_id": "igdb-1", "cursor": "x+/"}, Gate(s))
        params = json.loads(route.calls[0].request.url.params["input_json"])
        assert params["languages"] == ["all"] and params["filter"] == 1 and params["purchase_type"] == 0
        assert params["use_review_quality"] is True and params["day_range"] == 365
        assert params["cursor"] == "x+/" and "PRIVATE" not in json.dumps(r)
        assert r["sampling"]["sample_size"] == 1
        route.respond(200, json={"reviews": []})
        assert (await a.call("get_reviews", {"game_id": "igdb-1"}, Gate(s)))["documents"] == []
        route.respond(200, json={"changed": []})
        with pytest.raises(SourceFailure, match="invalid_schema"):
            await a.call("get_reviews", {"game_id": "igdb-1"}, Gate(s))
    await h.client.aclose()


async def test_news_cleaning_and_developer(stack):
    s, db, _, _ = stack
    h = HTTP(s)
    a = SteamSource(s, h, db)
    db.put("game", "igdb-1", {"external_ids": {"steam": "123"}})
    with respx.mock() as mock:
        mock.get("https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/").respond(
            200,
            json={
                "appnews": {
                    "newsitems": [
                        {
                            "gid": "1",
                            "title": "Patch",
                            "contents": "<script>leak()</script>[b]Fix[/b]",
                            "date": 1700000000,
                            "feedname": "steam_community_announcements",
                            "url": "javascript:alert(1)",
                        }
                    ]
                }
            },
        )
        d = (await a.call("get_updates", {"game_id": "igdb-1"}, Gate(s)))["documents"][0]
        assert d["text"] == "Fix" and d["developer"] and d["url"] is None
    await h.client.aclose()


async def test_model_protocol_repairs_and_usage():
    s = Settings(
        _env_file=None,
        llm_mode="live",
        llm_base_url="https://model.example/v1",
        llm_api_key="test-key",
        llm_model="configured-model",
    )
    h = HTTP(s)
    m = LiveLLM(s, h)
    g = Gate(s)
    valid = {
        "role": "planner",
        "summary": "继续调查",
        "findings": [],
        "evidence_ids": [],
        "missing_information": [],
        "proposed_actions": [],
        "status": "done",
    }
    with respx.mock() as mock:
        route = mock.post("https://model.example/v1/chat/completions")
        route.side_effect = [
            httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]}),
            httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "tool_calls": [
                                    {"function": {"name": "submit_role_result", "arguments": json.dumps(valid)}}
                                ]
                            }
                        }
                    ],
                    "usage": {"total_tokens": 100},
                },
            ),
        ]
        result = await m.run("planner", {}, g)
        assert result.role == "planner" and g.ctx["budget"]["llm_calls"] == 2
        assert g.ctx["budget"]["external_requests"] == 2 and g.ctx["model_usage"][-1]["usage"]["total_tokens"] == 100
        assert json.loads(route.calls[0].request.content)["model"] == "configured-model"
    await h.client.aclose()


@pytest.mark.parametrize(
    "base,expected",
    [("https://api.deepseek.com", True), ("https://api.deepseek.com/v1/", True), ("https://model.example/v1", False)],
)
async def test_forced_function_provider_compatibility(base, expected):
    s = Settings(_env_file=None, llm_base_url=base, llm_model="test", llm_api_key="test")
    h = HTTP(s)
    valid = {"role": "planner", "summary": "done", "status": "done"}
    with respx.mock() as mock:
        route = mock.post(base.rstrip("/") + "/chat/completions").respond(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [{"function": {"name": "submit_role_result", "arguments": json.dumps(valid)}}]
                        }
                    }
                ]
            },
        )
        await LiveLLM(s, h).run("planner", {}, Gate(s))
        body = json.loads(route.calls[0].request.content)
        assert (body.get("thinking") == {"type": "disabled"}) == expected
        assert body["tool_choice"]["function"]["name"] == "submit_role_result"
    await h.client.aclose()


async def test_store_filter_platform_and_spoofed_domain():
    s = Settings(_env_file=None, igdb_client_id="test", igdb_client_secret="test")
    h = HTTP(s)
    a = IGDBSource(s, h)
    with respx.mock() as mock:
        mock.post("https://id.twitch.tv/oauth2/token").respond(200, json={"access_token": "test", "expires_in": 3600})
        route = mock.post("https://api.igdb.com/v4/games").respond(
            200,
            json=[
                {"id": 1, "name": "Real", "websites": [{"url": "https://store.steampowered.com/app/123/"}]},
                {"id": 2, "name": "Other", "websites": [{"url": "https://www.gog.com/en/game/example"}]},
                {
                    "id": 3,
                    "name": "Spoof",
                    "websites": [{"url": "https://store.steampowered.com.evil.example/app/123/"}],
                },
            ],
        )
        r = await a.call("search_games", {"q": "Hades", "store": "steam", "platform": "pc"}, Gate(s))
        assert [g["id"] for g in r["games"]] == ["igdb-1"]
        assert "where platforms = (6)" in route.calls[0].request.content.decode()
        assert r["search_info"]["candidates_scanned"] == 3
        assert (
            a.normalize({"id": 4, "name": "Itch", "websites": [{"url": "https://studio.itch.io/game"}]})["store_links"][
                "itch"
            ]
            == "https://studio.itch.io/game"
        )
    await h.client.aclose()
