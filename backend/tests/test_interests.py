import json
import pytest
import respx
from fastapi.testclient import TestClient
from pydantic import SecretStr
from app.main import create_app
from app.schemas import ResearchRequest
from app.db import Watch
from app.interests import collect_sources


def import_games(client, db, gids=("fx-02", "fx-04", "fx-06")):
    return client.post(
        "/api/player/import/file",
        json={
            "format": "json",
            "content": json.dumps(
                {
                    "games": [
                        {
                            "platform": "fixture",
                            "game_id": gid,
                            "name": db.get("game", gid)["name"],
                            "playtime_minutes": 100,
                            "favorite": True,
                        }
                        for gid in gids
                    ]
                }
            ),
        },
    ).json()


def test_memory_uses_history_library_watch_and_latest_feedback(stack):
    settings, db, _, engine = stack
    tid = engine.create(ResearchRequest(query="喜欢弹反，重视构筑，不喜欢刷装备"))
    game = db.get("game", "fx-02")
    with db.Session.begin() as session:
        session.add(Watch(id="w-test", data={"id": "w-test", "game_id": "fx-02", "enabled": True, "game": game}))
    with TestClient(create_app(settings, db, False)) as c:
        imp = import_games(c, db)
        sources = collect_sources(db, "synthetic-v1")
        assert {s["kind"] for s in sources} == {"input", "library", "watch"}
        assert tid in {s["id"] for s in sources}
        response = c.post("/api/interests/generate", json={})
        assert response.status_code == 200
        cards = response.json()["cards"]
        assert cards and all(x["status"] == "draft" for x in cards)
        assert all(x["text"] != "喜欢弹反，重视构筑，不喜欢刷装备" for x in cards)
        assert c.post("/api/interests/generate", json={}).json()["cards"] == []
        card = cards[0]
        assert (
            c.patch(
                "/api/interests/" + card["id"],
                json={"version": 1, "title": card["title"], "text": card["text"], "confirm": True},
            ).json()["status"]
            == "confirmed"
        )
        assert (
            c.patch(
                "/api/interests/" + card["id"], json={"version": 1, "title": card["title"], "text": card["text"]}
            ).status_code
            == 409
        )
        assert c.get("/api/interests").json()["items"]
        assert c.delete("/api/interests/" + card["id"]).status_code == 200
        assert card["id"] not in {x["id"] for x in c.get("/api/interests").json()["items"]}
        c.post("/api/profile/feedback", json={"game_id": "fx-02", "reaction": "like", "reason": ""})
        c.post("/api/profile/feedback", json={"game_id": "fx-02", "reaction": "dislike", "reason": "不喜欢"})
        sources = collect_sources(db, "synthetic-v1")
        assert not any(s["kind"] in ["watch", "library"] and s.get("game_key") == "fx-02" for s in sources)
        assert not collect_sources(db, "live")


def test_text_library_draft_confirmation_and_selection_persist(stack):
    settings, db, _, _ = stack
    with TestClient(create_app(settings, db, False)) as c:
        imp = import_games(c, db, ("fx-02", "fx-01", "fx-04", "fx-06"))
        draft = c.post(
            "/api/player/imports/" + imp["id"] + "/analyze",
            json={"selected_ids": ["entry-0", "entry-1", "entry-2", "entry-3"], "reactions": {"entry-1": "exclude"}},
        ).json()
        cards = draft["interest_summary"]["cards"]
        assert cards
        assert not c.get("/api/interests").json()["items"]
        assert not any("烬刃回响" in s["label"] for s in collect_sources(db, "synthetic-v1"))
        wanted = [{k: card[k] for k in ["title", "text", "source_ids", "game_keys"]} for card in cards]
        result = c.post(
            "/api/player/drafts/" + draft["id"] + "/confirm", json={"interest_cards": wanted, "style": "vivid"}
        )
        assert result.status_code == 200
        assert result.json()["query_seed"] == wanted[0]["text"]
        stored = c.get("/api/interests").json()["items"]
        assert len(stored) == len(cards) and all(x["status"] == "confirmed" for x in stored)
        assert (
            c.post("/api/player/drafts/" + draft["id"] + "/confirm", json={"interest_cards": wanted}).status_code == 409
        )
        # Text confirmation does not silently turn inferred mechanics into hard constraints.
        assert result.json()["profile"]["preferences"] == []
    settings.data_mode = "live"
    with TestClient(create_app(settings, db, False)) as c:
        assert c.get("/api/interests").json()["items"] == []
        assert c.delete("/api/interests/" + stored[0]["id"]).status_code == 404


@pytest.mark.parametrize("bad", [False, True])
def test_live_summary_protocol_deepseek_and_source_validation(stack, bad):
    s, db, _, _ = stack
    s.llm_mode = "live"
    s.llm_base_url = "https://api.deepseek.com"
    s.llm_model = "test"
    s.llm_api_key = SecretStr("test-secret")
    arguments = {
        "cards": [
            {
                "game_keys": ["fx-02", "fx-04", "fx-06"],
                "title": "战斗节奏",
                "text": "你似乎享受观察与反应带来的挑战，可以继续寻找允许多样构筑的战斗体验。",
                "source_ids": ["invented" if bad else "current"],
            }
        ]
    }
    with respx.mock() as mock, TestClient(create_app(s, db, False)) as c:
        imp = import_games(c, db)
        arguments["cards"][0]["source_ids"] = (
            ["invented"] if bad else [imp["id"] + ":entry-" + str(i) for i in range(3)]
        )
        response = {
            "usage": None,
            "choices": [
                {
                    "message": {
                        "tool_calls": [{"function": {"name": "submit_interests", "arguments": json.dumps(arguments)}}]
                    }
                }
            ],
        }
        route = mock.post("https://api.deepseek.com/chat/completions").respond(200, json=response)
        result = c.post("/api/interests/generate", json={"text": "喜欢弹反和构筑"})
        assert result.status_code == (502 if bad else 200)
        body = json.loads(route.calls[0].request.content)
        assert body["thinking"] == {"type": "disabled"}
        assert len(route.calls) == 1 and body["tool_choice"]["function"]["name"] == "submit_interests"
        assert "test-secret" not in result.text
        if bad:
            assert c.get("/api/interests").json()["items"] == []
        else:
            assert result.json()["generator"] == "live_model"
            assert result.json()["budget"]["llm_calls"] == 1
            assert result.json()["budget"]["external_requests"] == 1


def test_live_failure_preserves_cards_and_never_falls_back(stack):
    s, db, _, _ = stack
    s.llm_mode = "live"
    s.llm_base_url = "https://api.deepseek.com"
    s.llm_model = "test"
    s.llm_api_key = SecretStr("test-secret")
    db.put(
        "interest_card",
        "saved",
        {
            "id": "saved",
            "title": "已有兴趣",
            "text": "这段已经确认的文字应当保留。",
            "status": "confirmed",
            "dataset_id": "synthetic-v1",
            "version": 1,
            "created_at": "2026-01-01",
        },
    )
    with respx.mock() as mock, TestClient(create_app(s, db, False)) as c:
        import_games(c, db)
        mock.post("https://api.deepseek.com/chat/completions").respond(500)
        assert c.post("/api/interests/generate", json={"text": "喜欢弹反"}).status_code == 502
        assert [x["id"] for x in c.get("/api/interests").json()["items"]] == ["saved"]


def test_less_than_three_games_does_not_call_model_or_count_duplicates(stack):
    s, db, _, _ = stack
    s.llm_mode = "live"
    with TestClient(create_app(s, db, False)) as c:
        import_games(c, db, ("fx-02", "fx-04"))
        c.post("/api/shortlist", json={"game_id": "fx-02"})
        r = c.post("/api/interests/generate", json={"text": "构筑"})
        assert r.status_code == 200 and not r.json()["cards"]
        assert r.json()["budget"]["llm_calls"] == 0 and "不足3款" in r.json()["message"]


def test_balanced_groups_and_unassigned_games_are_visible(stack):
    from app.interests import rule_summary, partition

    assert [len(x) for x in partition(list(range(8)))] == [4, 4]
    assert [len(x) for x in partition(list(range(15)))] == [5, 5, 5]
    sources = [
        {
            "id": str(i),
            "kind": "library",
            "game_key": str(i),
            "game_name": f"Synthetic {i}",
            "text": "authored",
            "tags": ["策略"],
        }
        for i in range(15)
    ]
    cards = rule_summary(sources).cards
    assert [len(c.game_keys) for c in cards] == [5, 5, 5]
    assert len({key for c in cards for key in c.game_keys}) == 15


def test_group_card_attachment_preserves_games_and_member_count_on_edit(stack):
    s, db, _, engine = stack
    with TestClient(create_app(s, db, False)) as c:
        import_games(c, db)
        card = c.post("/api/interests/generate", json={}).json()["cards"][0]
        body = {
            "version": 1,
            "title": card["title"],
            "text": card["text"],
            "confirm": True,
            "game_keys": card["game_keys"],
        }
        saved = c.patch("/api/interests/" + card["id"], json=body).json()
        assert len(saved["related_games"]) == 3
        bad = c.patch("/api/interests/" + card["id"], json=dict(body, version=2, game_keys=card["game_keys"][:2]))
        assert bad.status_code == 422
        tid = engine.create(
            ResearchRequest(
                recommendation_mode="player_tags", query="构筑", attachments=[{"id": card["id"], "version": 2}]
            )
        )
        assert len(db.task(tid)["context"]["attached_cards"][0]["related_games"]) == 3


def test_equal_summaries_with_different_game_groups_are_not_lost(stack):
    s, db, _, _ = stack
    with TestClient(create_app(s, db, False)) as c:
        for gid in [f"fx-{n:02}" for n in range(1, 9)]:
            game = db.get("game", gid)
            game["tags"] = ["同类体验"]
            db.put("game", gid, game)
        import_games(c, db, tuple(f"fx-{n:02}" for n in range(1, 9)))
        r = c.post("/api/interests/generate", json={}).json()
        assert len(r["cards"]) == 2
        assert [len(x["related_games"]) for x in r["cards"]] == [4, 4]
        assert not c.post("/api/interests/generate", json={}).json()["cards"]


@pytest.mark.parametrize("defect", ["invented_game", "duplicate_game", "too_few", "missing_member_source"])
def test_model_cannot_invent_or_pad_game_members(stack, defect):
    s, db, _, _ = stack
    s.llm_mode = "live"
    s.llm_base_url = "https://api.deepseek.com"
    s.llm_model = "test"
    s.llm_api_key = SecretStr("test-secret")
    with respx.mock() as mock, TestClient(create_app(s, db, False)) as c:
        imp = import_games(c, db)
        keys = ["fx-02", "fx-04", "fx-06"]
        refs = [imp["id"] + ":entry-" + str(i) for i in range(3)]
        if defect == "invented_game":
            keys[-1] = "not-in-library"
        if defect == "duplicate_game":
            keys[-1] = keys[0]
        if defect == "too_few":
            keys = keys[:2]
        if defect == "missing_member_source":
            refs = refs[:2]
        raw = {
            "cards": [
                {
                    "title": "策略构筑",
                    "text": "这类游戏围绕策略选择和构筑变化形成共同线索。",
                    "game_keys": keys,
                    "source_ids": refs,
                }
            ]
        }
        mock.post("https://api.deepseek.com/chat/completions").respond(
            200, json={"choices": [{"message": {"content": json.dumps(raw)}}]}
        )
        assert c.post("/api/interests/generate", json={}).status_code == 502
        assert not c.get("/api/interests").json()["items"]
