import copy
import json
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.db import Report, DocumentIndex
from app.main import create_app
from app.schemas import ResearchRequest
from app.tags.catalog import TAGS
from app.tags.intent import demo_interpret, ground_interpretation
from app.tags.models import Interpretation, Intent
from app.tags.workflow import batches
from app.tags.ranking import independent_ids, rating_value, rank
from app.tools import SteamSource, HTTP, Gate
from conftest import finish


def test_dictionary_has_auditable_mappings():
    assert len(TAGS) == 64
    for tag in TAGS.values():
        assert tag["definition"] and tag["research_sources"] and tag["confusions"]
        assert set(tag["platform_mappings"]) == {"steam", "gog", "epic", "itch"}
    assert TAGS["resource_grind"]["platform_mappings"]["steam"]["relation"] == "no_direct_mapping"


def test_negation_titles_and_attachment_precedence():
    cards = [{"id": "card", "text": "我喜欢刷装备，也喜欢探索和构筑。"}]
    result = demo_interpret("想找《刷装备传奇》以外的游戏，不需要刷装备，喜欢构筑", cards)
    grinds = [i for i in result.intents if i.tag_id == "resource_grind"]
    assert len(grinds) == 1 and grinds[0].polarity == "avoid" and grinds[0].source_id == "current"
    assert all(i.priority == 1 for i in result.intents if i.source_id == "card")
    assert not any(i.explicit_redline for i in result.intents)
    assert not demo_interpret("想玩《刷装备传奇》", []).intents
    assert demo_interpret("你希望减少装备资源刷取带来的负担。", []).intents[0].polarity == "avoid"


def test_ungrounded_quotes_and_attachment_redlines_rejected():
    r = Interpretation(summary="x", intents=[Intent(tag_id="build_variety", source_quote="幻觉")])
    with pytest.raises(ValueError):
        ground_interpretation(r, "构筑", [])
    r.intents[0] = Intent(tag_id="build_variety", source_id="card", source_quote="必须构筑", explicit_redline=True)
    with pytest.raises(ValueError):
        ground_interpretation(r, "构筑", [{"id": "card", "text": "必须构筑"}])


def test_whole_reviews_are_deferred_not_truncated():
    docs = [
        {
            "id": str(n),
            "game_id": "g",
            "kind": "reviews",
            "complete": True,
            "provenance_type": "live",
            "text": text,
            "sampling_stratum": str(n),
        }
        for n, text in enumerate(["a" * 20000, "b" * 20000, "c" * 33000])
    ]
    assert batches(docs, 60) == [["0"]]
    docs[0]["complete"] = False
    assert batches(docs, 60) == [["1"]]


def test_independent_counts_and_rating():
    docs = [
        dict(id="a", provider="steam", author_key="a", text="Same review!"),
        dict(id="b", provider="gog", author_key="b", text="Same review"),
        dict(id="c", provider="gog", author_key="b", text="Revised review"),
    ]
    assert len(set(independent_ids(docs).values())) == 1
    assert rating_value(None) is None
    assert rating_value({"positive": 0, "negative": 0}) is None
    assert rating_value({"positive": 90, "negative": 10})["wilson_lower"] < 0.9


@pytest.mark.asyncio
async def test_adapter_preserves_long_tail_and_population_rating(stack):
    s, db, registry, engine = stack
    db.put("game", "igdb-1", {"id": "igdb-1", "external_ids": {"steam": "1"}})
    body = "x" * 31000 + "尾部明确说：只在毕业阶段需要刷取。"

    async def handler(req):
        p = json.loads(req.url.params["input_json"])
        r = {
            "reviews": [
                {"recommendationid": str(p["review_type"]), "review": body, "author": {"steamid": "76561190000000001"}}
            ]
        }
        if p["review_type"] == 0:
            r["query_summary"] = {"total_positive": 900, "total_negative": 100}
        return httpx.Response(200, json={"response": r})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await SteamSource(s, HTTP(s, client), db, reviews=True).call(
            "get_reviews", {"game_id": "igdb-1"}, Gate(s)
        )
    assert result["documents"][0]["text"].endswith("只在毕业阶段需要刷取。")
    assert result["documents"][0]["complete"] is True and result["rating"]["positive"] == 900
    assert result["sampling"]["representative"] is False


@pytest.mark.asyncio
async def test_workflow_interprets_before_search_retains_unknown_and_full_text(stack):
    s, db, r, e = stack
    tid = e.create(ResearchRequest(recommendation_mode="player_tags", query="重视战斗技巧和构筑，不需要大量刷装备"))
    await e.step(tid)
    assert db.task(tid)["context"]["budget"]["tool_calls"] == 0
    assert db.task(tid)["context"]["interpretation"]["intents"]
    await finish(e, tid)
    with db.Session() as session:
        report = session.scalar(select(Report).where(Report.task_id == tid)).data
    first = report["results"][0]
    assert first["game"]["id"] == "fx-01" and first["group"] == "recommended"
    assert first["reading"]["full_read"] == 3 and first["rating"] is None
    assert any(row["group"] == "unreviewed" and row["match"] is None for row in report["results"])
    docs = {d["id"]: d for d in report["snapshot"]["documents"]}
    assert all(x["end"] == len(docs[x["document_id"]]["text"]) for x in report["reading_ledger"])
    assert not report.get("dna_query")
    assert all(c.get("feature_key") is None for c in report["snapshot"]["claims"])


@pytest.mark.asyncio
async def test_scope_opposition_pending_and_revoked_sources(stack):
    s, db, r, e = stack
    tid = e.create(
        ResearchRequest(recommendation_mode="player_tags", query="不需要刷装备", game_ids=["fx-01", "fx-09"])
    )
    await finish(e, tid)
    ctx = db.task(tid)["context"]
    docs = e.knowledge.documents(ctx["document_ids"])
    claims = e.knowledge.claims(tid)
    games = [db.get("game", "fx-01"), db.get("game", "fx-09")]
    rows = rank(db, games, claims, docs, ctx)
    assert rows[0]["game"]["id"] == "fx-01"
    assert next(x for x in rows if x["game"]["id"] == "fx-09")["tags"][0]["oppose_count"] > 0
    only = copy.deepcopy(claims)
    for c in only:
        c["scope"] = {"stage": "endgame"}
    assert all(x["match"] is None and x["rank_score"] is None for x in rank(db, games, only, docs, ctx))
    for c in only:
        c["scope"] = {}
        c["evidence_status"] = "pending"
    assert all(x["match"] is None for x in rank(db, games, only, docs, ctx))
    with db.Session.begin() as session:
        for d in docs:
            session.get(DocumentIndex, d["id"]).revoked = 1
    assert all(x["match"] is None for x in rank(db, games, claims, docs, ctx))


@pytest.mark.asyncio
async def test_card_snapshot_validation_and_local_rerank(stack):
    s, db, r, e = stack
    c = {
        "id": "card1",
        "version": 1,
        "title": "构筑与成长",
        "text": "我享受有意义的构筑变化，也愿意尝试不同流派。",
        "status": "draft",
        "dataset_id": "synthetic-v1",
        "sources": [],
        "created_at": "2026-10-01",
    }
    db.put("interest_card", "card1", c)
    req = ResearchRequest(
        recommendation_mode="player_tags", query="不需要刷装备", attachments=[{"id": "card1", "version": 1}]
    )
    with pytest.raises(ValueError):
        e.create(req)
    c["status"] = "confirmed"
    db.put("interest_card", "card1", c)
    tid = e.create(req)
    c["version"] = 2
    c["text"] = "现在完全不同了。"
    db.put("interest_card", "card1", c)
    assert db.task(tid)["context"]["attached_cards"][0]["version"] == 1
    assert "构筑" in db.task(tid)["context"]["attached_cards"][0]["text"]
    with pytest.raises(ValueError):
        e.create(req)
    await finish(e, tid)
    before = copy.deepcopy(db.task(tid)["context"]["budget"])
    with TestClient(create_app(s, db, run_worker=False)) as client:
        assert (
            client.post("/api/tag-research/" + tid + "/rerank", json={"priorities": {"build_variety": 3}}).status_code
            == 200
        )
        assert (
            client.post("/api/tag-research/" + tid + "/rerank", json={"priorities": {"parry_counter": 3}}).status_code
            == 422
        )
    assert db.task(tid)["context"]["budget"] == before


@pytest.mark.asyncio
async def test_live_model_receives_entire_review_and_invalid_protocol_never_falls_back(stack):
    from pydantic import SecretStr
    from app.tags.llm import TagLLM
    from app.tools import SourceFailure

    s, db, r, e = stack
    s.llm_mode = "live"
    s.llm_base_url = "https://model.example.test/v1"
    s.llm_model = "test"
    s.llm_api_key = SecretStr("synthetic-test-credential")
    full = "前面的赞美。" * 1000 + "但以上说的是前作，本作完全不是这样。"
    payload = {"intents": [{"tag_id": "build_variety"}], "documents": [{"id": "d", "text": full, "kind": "reviews"}]}
    seen = []

    async def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        sent = json.loads(body["messages"][1]["content"])
        assert sent["documents"][0]["text"] == full
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SourceFailure, match="invalid_tag_model_protocol"):
            await TagLLM(s, HTTP(s, client)).run("extract", payload, Gate(s))
    assert len(seen) == 2
    assert "完整正文" in seen[0]["messages"][0]["content"]


def test_live_data_requires_live_model(stack):
    s, db, r, e = stack
    from pydantic import SecretStr

    s.data_mode = "live"
    s.igdb_client_id = "synthetic"
    s.igdb_client_secret = SecretStr("synthetic")
    with pytest.raises(ValueError, match="真实 LLM"):
        e.create(ResearchRequest(recommendation_mode="player_tags", query="构筑"))


@pytest.mark.asyncio
async def test_explicit_pc_enters_recall_before_source_filter(stack, monkeypatch):
    s, db, registry, engine = stack
    actions = []
    original = registry.call

    async def capture(action, *args, **kwargs):
        actions.append(action)
        return await original(action, *args, **kwargs)

    monkeypatch.setattr(registry, "call", capture)
    tid = engine.create(ResearchRequest(recommendation_mode="player_tags", query="想要构筑丰富的 PC 游戏"))
    await engine.step(tid)
    await engine.step(tid)
    assert actions[0]["arguments"]["platforms"] == ["pc"]
