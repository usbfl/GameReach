"""Retrieval contracts use authored sources, never live credentials or fabricated live results."""

from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import select, text, delete
from app.db import Document, DocumentIndex, Chunk, MemoryIndex, Task, now
from app.retrieval import Retrieval, CHUNK_VERSION
from app.dna.projection import current_features
from app.schemas import ResearchRequest, AgentOutput
from app.interests import collect_sources
from conftest import finish


def doc(text="精准弹反能打破敌人防御。", **kwargs):
    return {
        "dataset_id": "live",
        "game_id": "test-game",
        "provider": "test",
        "external_id": "review-1",
        "kind": "reviews",
        "provenance_type": "source",
        "published_at": now(),
        "text": text,
        "title": "Authored test review",
        "url": None,
        **kwargs,
    }


def test_tail_recall_aliases_offsets_and_allowed_documents(stack):
    _, db, _, engine = stack
    text_value = "环境与画面的描述。" * 400 + "A perfectly timed parry breaks the enemy guard."
    ids = engine.knowledge.ingest([doc(text_value)])
    r = Retrieval(db)
    hits = r.search("我想练习精准弹反", "live", "test-game", ids)
    assert hits and hits[0]["start"] > 1800 and "parry" in hits[0]["text"]
    assert all(text_value[h["start"] : h["end"]] == h["text"] for h in hits)
    assert all(h["chunk_version"] == CHUNK_VERSION and h["embedding_version"] == "none" for h in hits)
    assert r.search("弹反", "live", "test-game", []) == []
    assert r.search("弹反", "live", "other-game", ids) == []
    assert r.search("弹反", "synthetic-v1", "test-game", ids) == []
    assert r.search('" OR * : NEAR(弹反)', "live", "test-game", ids)  # User FTS grammar is never executed.


@pytest.mark.parametrize(
    "extra,scope,principal,visible",
    [
        ({"scope": {"platform": "pc"}}, {}, "local", False),
        ({"scope": {"platform": "pc"}}, {"platform": "pc"}, "local", True),
        ({"scope": {"platform": "pc", "patch": "2.0"}}, {"platform": "pc"}, "local", False),
        ({"visibility": "owner", "owner_id": "alice"}, {}, "local", False),
        ({"visibility": "owner", "owner_id": "alice"}, {}, "alice", True),
        ({"visibility": "revoked"}, {}, "local", False),
        ({"published_at": (datetime.now(timezone.utc) - timedelta(days=731)).isoformat()}, {}, "local", False),
        ({"published_at": None}, {}, "local", True),
        ({"provenance_type": "report"}, {}, "local", False),
        ({"kind": "report"}, {}, "local", False),
    ],
)
def test_scope_privacy_freshness_and_derived_exclusion(stack, extra, scope, principal, visible):
    _, db, _, engine = stack
    ids = engine.knowledge.ingest([doc(**extra)])
    hits = Retrieval(db).search("弹反", "live", "test-game", ids, scope, principal)
    assert bool(hits) is visible
    if visible and extra.get("published_at", "not-null") is None:
        assert hits[0]["freshness"] == "unknown"


def finding(did, quote, value=True):
    return {
        "game_id": "fx-01",
        "document_id": did,
        "feature_key": "parry",
        "value": value,
        "claim_text": "该评论描述弹反。" if value else "该评论描述不支持弹反。",
        "quote": quote,
        "scope": {},
        "assessment_type": "fact",
    }


def approve(engine, tid):
    claims = engine.knowledge.claims(tid)
    engine.knowledge.verify(
        tid,
        verdicts=[
            {
                "claim_id": c["id"],
                "verdict": "supported",
                "reason": "测试判决：逐字引文直接支持这一机制。",
                "evidence_ids": c["evidence_ids"],
            }
            for c in claims
        ],
    )


def test_quote_is_located_in_allowed_window_and_verifier_sees_tail(stack):
    _, db, _, engine = stack
    tid = engine.create(ResearchRequest(game_ids=["fx-01"], query="弹反"))
    quote = "精准弹反能打破防御。"
    original = quote + "前文。" * 800 + quote + "这是结尾的上下文。"
    ids = engine.knowledge.ingest([doc(original, dataset_id="synthetic-v1", game_id="fx-01")])
    start = original.rfind(quote)
    windows = [{"id": ids[0], "start": start, "end": len(original)}]
    accepted, rejected = engine.knowledge.extract(tid, [finding(ids[0], quote)], ids, "synthetic-v1", windows)
    assert accepted and not rejected
    claim = engine.knowledge.claims(tid)[0]
    evidence = engine.knowledge.evidence(claim["evidence_ids"][0])
    assert evidence["start"] == start
    context = Retrieval(db).verification_context([claim])
    assert quote in context[0]["text"] and "这是结尾" in context[0]["text"] and context[0]["start"] > 1800
    _, rejected = engine.knowledge.extract(tid, [finding(ids[0], "前文。")], ids, "synthetic-v1", windows)
    assert rejected


def test_source_update_revocation_rebuild_and_cache_invalidation(stack):
    _, db, _, engine = stack
    tid = engine.create(ResearchRequest(game_ids=["fx-01"], query="弹反"))
    original = doc("此版本支持弹反。", dataset_id="synthetic-v1", game_id="fx-01")
    first = engine.knowledge.ingest([original])[0]
    engine.knowledge.extract(tid, [finding(first, original["text"])], [first], "synthetic-v1")
    approve(engine, tid)
    assert current_features(db, "synthetic-v1", "fx-01")["parry"]["value"] is True
    revised = original | {"text": "此版本不支持弹反。"}
    second = engine.knowledge.ingest([revised])[0]
    assert first != second and current_features(db, "synthetic-v1", "fx-01")["parry"]["value"] is None
    engine.knowledge.extract(tid, [finding(second, revised["text"], False)], [second], "synthetic-v1")
    approve(engine, tid)
    assert current_features(db, "synthetic-v1", "fx-01")["parry"]["value"] is False
    r = Retrieval(db)
    r.rebuild()
    assert {h["id"] for h in r.search("弹反", "synthetic-v1", "fx-01")} == {second}
    engine.knowledge.ingest([original])  # Replaying an old adapter cache must not reactivate its source.
    assert {h["id"] for h in r.search("弹反", "synthetic-v1", "fx-01")} == {second}
    db.put("cache", "cached-review", {"result": {"data": {"documents": [revised]}}})
    r.revoke(second)
    r.rebuild()
    assert not r.search("弹反", "synthetic-v1", "fx-01") and db.get("cache", "cached-review") is None
    assert current_features(db, "synthetic-v1", "fx-01")["parry"]["value"] is None
    assert engine.knowledge.documents([first])[0]["text"] == original["text"]  # History remains exact.


def test_removed_rows_and_tampered_chunks_never_retrieve(stack):
    _, db, _, engine = stack
    did = engine.knowledge.ingest([doc()])[0]
    with db.Session.begin() as s:
        chunk = s.scalars(select(Chunk)).first()
        chunk.content_hash = "incorrect"
    assert not Retrieval(db).search("弹反", "live", "test-game")
    with db.Session.begin() as s:
        s.execute(delete(Document).where(Document.id == did))
    with db.Session() as s:
        assert not list(s.scalars(select(Chunk)))
        assert s.execute(text("SELECT count(*) FROM chunk_fts")).scalar() == 0


def test_metadata_identity_changes_and_permissions_survive_rebuild(stack):
    _, db, _, engine = stack
    original = doc()
    a = engine.knowledge.ingest([original])[0]
    b = engine.knowledge.ingest([original | {"scope": {"platform": "pc"}}])[0]
    assert a != b
    r = Retrieval(db)
    r.rebuild()
    assert not r.search("弹反", "live", "test-game")
    assert r.search("弹反", "live", "test-game", scope={"platform": "pc"})[0]["id"] == b
    with db.Session.begin() as s:
        state = s.get(DocumentIndex, b)
        state.visibility = "owner"
        state.owner_id = "private"
    r.rebuild()
    assert not r.search("弹反", "live", "test-game", scope={"platform": "pc"})


def test_memory_retrieves_older_relevance_keeps_current_feedback_and_invalidates(stack):
    _, db, _, _ = stack
    with db.Session.begin() as s:
        for i in range(145):
            s.add(
                Task(
                    id=f"history-{i}",
                    created_at=f"2026-01-{i + 1:03}",
                    request={"kind": "scout", "query": "喜欢弹反与读招" if i == 0 else "想了解轻松农场"},
                    context={"dataset_id": "synthetic-v1"},
                )
            )
        s.add(
            Task(id="private-dataset", request={"kind": "scout", "query": "弹反秘密"}, context={"dataset_id": "live"})
        )
    profile = db.get("profile", "default")
    profile["feedback"] = [
        {"game_id": "fx-01", "reaction": "dislike", "reason": "这次不要高强度战斗", "created_at": now()}
    ]
    db.put("profile", "default", profile)
    db.put(
        "interest_card",
        "confirmed",
        {
            "id": "confirmed",
            "dataset_id": "synthetic-v1",
            "created_at": now(),
            "status": "confirmed",
            "title": "读招",
            "text": "过去喜欢观察敌人与弹反，但本次要求优先。",
        },
    )
    sources = collect_sources(db, "synthetic-v1", "想试试弹反，不过今天想休息一下")
    assert sources[0]["id"] == "current" and any(s["id"] == "history-0" for s in sources)
    assert any(s["id"] == "confirmed" for s in sources) and any(
        s["kind"] == "feedback" and s["text"].startswith("dislike") for s in sources
    )
    assert not any(s["id"] == "private-dataset" for s in sources)
    with db.Session.begin() as s:
        row = s.get(Task, "history-0")
        row.request = {"kind": "scout", "query": "改为农场"}
    assert not any(s["id"] == "history-0" for s in Retrieval(db).history("synthetic-v1", "弹反"))
    with db.Session.begin() as s:
        s.delete(s.get(Task, "history-144"))
    Retrieval(db).sync_memory()
    with db.Session() as s:
        assert s.get(MemoryIndex, "history-144") is None
        assert s.execute(text("SELECT count(*) FROM memory_fts WHERE item_id='history-144'")).scalar() == 0
    # Private interests are not game evidence.
    assert Retrieval(db).search("弹反", "synthetic-v1", "fx-01") == []


async def test_engine_uses_relevant_local_tail_and_citation_windows_without_external_calls(stack, monkeypatch):
    settings, db, registry, engine = stack
    settings.llm_mode = "live"
    settings.llm_base_url = "https://model.invalid"
    settings.llm_api_key = __import__("pydantic").SecretStr("not-used")
    settings.llm_model = "contract-only"
    quote = "精准弹反能打破敌人的防御。"
    engine.knowledge.ingest([doc("美术与音乐。" * 600 + quote, dataset_id="synthetic-v1", game_id="fx-01")])

    class Model:
        async def run(self, role, context, gate):
            gate.use("llm_calls")
            if role == "researcher":
                chosen = next(d for d in context["documents"] if quote in d["text"])
                assert chosen["start"] > 1800 and len(chosen["text"]) <= 800
                return AgentOutput(
                    role=role, summary="按当前问题找到原文结尾。", findings=[finding(chosen["id"], quote)]
                )
            if role == "verifier":
                assert any(quote in d["text"] for d in context["documents"])
                return AgentOutput(
                    role=role,
                    summary="检查原文与主张。",
                    verdicts=[
                        {
                            "claim_id": c["id"],
                            "verdict": "supported",
                            "reason": "原文明示存在弹反。",
                            "evidence_ids": c["evidence_ids"],
                        }
                        for c in context["claims"]
                    ],
                )
            raise AssertionError("Local known evidence should not require a new discovery plan")

    async def forbidden(*a, **kw):
        raise AssertionError("Unnecessary external source call")

    monkeypatch.setattr(registry, "call", forbidden)
    engine.llm = Model()
    tid = engine.create(ResearchRequest(query="必须有弹反", game_ids=["fx-01"]))
    result = await finish(engine, tid)
    assert result["status"] == "completed", result["context"]["errors"]
    assert result["context"]["budget"]["external_requests"] == result["context"]["budget"]["tool_calls"] == 0
    assert result["context"]["budget"]["llm_calls"] == 2
    assert result["context"]["retrieval_runs"][0]["characters"] <= 9600
    assert current_features(db, "synthetic-v1", "fx-01")["parry"]["value"] is True


def test_full_index_rebuild_repairs_lost_chunks_and_preserves_source_permissions(stack):
    _, db, _, engine = stack
    did = engine.knowledge.ingest([doc()])[0]
    with db.Session.begin() as session:
        session.execute(delete(Chunk))
    assert Retrieval(db).search("弹反", "live", "test-game") == []
    Retrieval(db).rebuild()
    assert Retrieval(db).search("弹反", "live", "test-game")[0]["id"] == did
    with db.Session.begin() as session:
        session.get(DocumentIndex, did).revoked = 1
    Retrieval(db).rebuild()
    assert not Retrieval(db).search("弹反", "live", "test-game")


def test_source_update_date_not_fetch_date_controls_freshness(stack):
    _, db, _, engine = stack
    old = (datetime.now(timezone.utc) - timedelta(days=900)).isoformat()
    ids = engine.knowledge.ingest([doc(published_at=old, updated_at=now())])
    assert Retrieval(db).search("弹反", "live", "test-game", ids)
    ids = engine.knowledge.ingest([doc(published_at=old, external_id="old-fetch", fetched_at=now())])
    assert not Retrieval(db).search("弹反", "live", "test-game", ids)


def test_current_interest_beats_profile_and_explicit_constraints_beat_text(stack):
    from app.ranking import constraints_from_text
    from app.dna.query import compile_query

    historical = [
        {"feature_key": "parry", "target": True, "weight": 3},
        {"feature_key": "deckbuilding", "target": True, "weight": 1},
    ]
    query = compile_query("今天不想弹反", historical_preferences=historical)
    prefs = {s.condition.feature_key: s.condition.target for s in query.soft_preferences}
    assert prefs["parry"] is False and prefs["deckbuilding"] is True and not query.must
    assert constraints_from_text("预算100元，要求中文", {"max_price": 60, "language": "en"}) == {
        "max_price": 60,
        "language": "en",
    }
    settings, db, _, engine = stack
    profile = db.get("profile", "default")
    profile["preferences"] = historical
    db.put("profile", "default", profile)
    tid = engine.create(ResearchRequest(query="今天不想弹反"))
    assert (
        next(
            x
            for x in db.task(tid)["context"]["dna_query"]["soft_preferences"]
            if x["condition"]["feature_key"] == "parry"
        )["condition"]["target"]
        is False
    )


async def test_missing_mechanic_fetches_next_review_page_within_existing_budget(stack, monkeypatch):
    from app.tools import ToolResult

    settings, db, registry, engine = stack
    calls = []

    async def source(action, gate, *args, **kwargs):
        gate.use("tool_calls")
        calls.append(action)
        tool = action["tool_name"]
        cursor = action["arguments"].get("cursor", "*")
        quote = "弹反（parry）=true。" if cursor == "page-2" else "这篇只讨论环境。"
        result = {
            "documents": [
                doc(
                    quote,
                    dataset_id="synthetic-v1",
                    game_id="fx-02",
                    provenance_type="synthetic",
                    kind="details" if tool == "get_game_details" else "reviews",
                    external_id=tool + cursor,
                )
            ]
        }
        if tool == "get_reviews":
            result["cursor"] = "page-2"
        return ToolResult(status="available", provider="fixture", capability=tool, data=result)

    monkeypatch.setattr(registry, "call", source)
    tid = engine.create(ResearchRequest(query="必须有弹反", game_ids=["fx-02"]))
    task = await finish(engine, tid)
    assert task["status"] == "completed", task["context"]["errors"]
    assert [a["arguments"].get("cursor", "*") for a in calls if a["tool_name"] == "get_reviews"] == ["*", "page-2"]
    assert task["context"]["dna_followup_rounds"] <= settings.max_dna_followup_rounds
    assert current_features(db, "synthetic-v1", "fx-02")["parry"]["value"] is True


async def test_live_interpretation_fills_soft_gaps_without_overriding_current_request(stack):
    from pydantic import SecretStr

    settings, db, _, engine = stack
    settings.llm_mode = "live"
    settings.llm_base_url = "https://model.invalid"
    settings.llm_api_key = SecretStr("not-used")
    settings.llm_model = "contract-only"

    class Model:
        async def run(self, role, context, gate):
            return AgentOutput(
                role=role,
                summary="从自由描述补充一个软偏好。",
                preferences=[
                    {"feature_key": "parry", "target": True, "weight": 1},
                    {"feature_key": "build_diversity", "target": 3, "weight": 1},
                ],
            )

    engine.llm = Model()
    tid = engine.create(ResearchRequest(query="不喜欢弹反，希望每次换个打法", game_ids=["fx-02"]))
    await engine.step(tid)
    await engine.step(tid)
    query = db.task(tid)["context"]["dna_query"]
    prefs = {p["condition"]["feature_key"]: p["condition"] for p in query["soft_preferences"]}
    assert prefs["parry"]["target"] is False
    assert prefs["build_diversity"]["target"] == 3 and prefs["build_diversity"]["origin"] == "model"
    assert query["must"] == query["must_not"] == []
