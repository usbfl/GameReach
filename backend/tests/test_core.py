import asyncio
import json
import pytest
from sqlalchemy import select
from app.schemas import Preference, Finding, ResearchRequest, HumanInput, RoleModes, valid_feature
from app.ranking import hard_filter, extract_preferences
from app.dna.query import compile_query
from app.dna.ranking import rank_game, sort_rows
from app.dna.projection import current_features
from app.db import Task, Report, Document
from app.tools import Gate, BudgetExceeded, Registry, SourceFailure, redact, clean_text, ToolResult
from app.engine import Engine
from app.shortlist import ShortlistService
from app.schemas import ShortlistInput
from conftest import finish


def test_dna_types():
    for k, v in [
        ("dodge", 1),
        ("build_diversity", True),
        ("build_diversity", 5),
        ("session_length_minutes", [30, 10]),
        ("combat_mode", "magic"),
    ]:
        assert not valid_feature(k, v)
    assert valid_feature("parry", None)
    with pytest.raises(ValueError):
        Preference(feature_key="parry", target="yes")


def test_three_state_and_price(stack):
    _, db, _, _ = stack
    g = db.get("game", "fx-24")
    assert hard_filter(g, {"language": "zh-CN"})[0] == "unknown"
    assert hard_filter(db.get("game", "fx-21"), {})[0] == "fail"
    assert hard_filter(db.get("game", "fx-01"), {"max_price": 100, "currency": "CNY", "region": "CN"})[0] == "unknown"
    assert hard_filter(db.get("game", "fx-01"), {"played": ["fx-01"]})[0] == "fail"


def test_ranking_unknown_and_monotonic(stack):
    game = stack[1].get("game", "fx-01")
    query = compile_query(preferences=[{"feature_key": "grind_requirement", "target": 0, "weight": 1}])

    def result(n):
        return rank_game(
            game, {"grind_requirement": {"value": n, "evidence_status": "supported", "evidence_ids": ["e"]}}, query, {}
        )

    assert result(0)["fit_score"] > result(1)["fit_score"] > result(4)["fit_score"]
    assert result(None)["fit_score"] is None and result(None)["group"] == "insufficient"
    assert rank_game(game, {}, compile_query(), {})["group"] == "discovery"
    assert sort_rows([result(2), result(0)])[0]["fit_score"] == 100
    assert extract_preferences("不喜欢弹反")[0]["target"] is False


async def test_offline_full_branch_and_citations(stack):
    s, db, registry, engine = stack
    tid = engine.create(ResearchRequest(query="战斗技巧和构筑，不需要大量刷装备"))
    t = await finish(engine, tid)
    assert t["status"] == "completed", t["context"]["errors"]
    with db.Session() as sess:
        r = sess.get(Report, (tid, 1)).data
    assert r["budget"]["external_requests"] == 0
    assert r["results"][0]["game"]["id"] == "fx-01"
    assert r["results"][0]["fit_score"] == 100
    actions = [json.loads(a) for a in t["context"]["completed_actions"]]
    assert any(a["tool_name"] == "get_updates" for a in actions)
    assert any(a["tool_name"] == "get_reviews" and a["arguments"].get("after") for a in actions)
    assert any("实际效果待验证" in risk for row in r["results"] for risk in row["risks"])
    for row in r["results"]:
        for f in row["features"].values():
            for eid in f["evidence_ids"]:
                e = engine.knowledge.evidence(eid)
                assert e["document"]["text"][e["start"] : e["end"]] == e["quote"]
                assert e["document"]["game_id"] == row["game"]["id"]
    assert len(actions) == len(set(t["context"]["completed_actions"]))


async def test_non_performance_task_skips_news(stack):
    tid = stack[3].create(ResearchRequest(kind="scout", game_ids=["fx-02"]))
    t = await finish(stack[3], tid)
    assert t["status"] == "completed"
    assert not any(json.loads(a)["tool_name"] == "get_updates" for a in t["context"]["completed_actions"])


async def test_conflicting_features_remain_unknown(stack):
    e = stack[3]
    tid = e.create(
        ResearchRequest(
            kind="scout", game_ids=["fx-03"], preferences=[Preference(feature_key="grind_requirement", target=0)]
        )
    )
    await finish(e, tid)
    f = current_features(e.db, "synthetic-v1", "fx-03")["grind_requirement"]
    assert f["value"] is None and f["evidence_status"] == "disputed"


async def test_human_reject_followup_resume_idempotency(stack):
    _, db, _, e = stack
    tid = e.create(ResearchRequest(kind="scout", game_ids=["fx-02"], role_modes=RoleModes(verifier="human")))
    t = await finish(e, tid)
    assert t["status"] == "paused" and t["context"]["pending_human"]["role"] == "verifier"
    old_active = t["context"]["budget"]["active_seconds"]
    # A new Engine reading the same DB sees the same pending node.
    other = Engine(stack[0], db, stack[2])
    other.recover()
    assert db.task(tid)["context"]["pending_human"]
    body = HumanInput(
        action_id="test-action-001", state_version=t["version"], decision="reject", note="请补充玩家和更新证据"
    )
    assert e.human(tid, body)["status"] == "resumed"
    assert e.human(tid, body)["status"] == "already_applied"
    with pytest.raises(ValueError):
        e.human(tid, body.model_copy(update={"action_id": "test-other-002"}))
    # Use AI after the demonstrated human rejection, avoiding repeated manual waits.
    with db.Session.begin() as sess:
        row = sess.get(Task, tid)
        row.request = row.request | {"role_modes": RoleModes().model_dump()}
    t = await finish(e, tid)
    assert t["status"] == "completed", t["context"]["errors"]
    assert any(json.loads(a)["tool_name"] == "get_updates" for a in t["context"]["completed_actions"])
    assert any(c["evidence_status"] == "rejected" for c in e.knowledge.claims(tid))
    assert t["context"]["budget"]["active_seconds"] - old_active < 20


@pytest.mark.parametrize("role", ["planner", "researcher"])
async def test_other_human_roles(stack, role):
    e = stack[3]
    tid = e.create(ResearchRequest(kind="scout", game_ids=["fx-02"], role_modes=RoleModes(**{role: "human"})))
    t = await finish(e, tid)
    assert t["status"] == "paused"
    e.human(
        tid,
        HumanInput(
            action_id="other-role-action",
            state_version=t["version"],
            decision="submit",
            note="继续",
            source_text="用户提供的待核验信息" if role == "researcher" else "",
        ),
    )
    await e.step(tid)
    assert stack[1].task(tid)["status"] == "running"
    if role == "researcher":
        assert any(c["provenance_type"] == "user" for c in e.knowledge.claims(tid))


async def test_fault_budget_and_cancel(stack):
    s, db, _, e = stack
    tid = e.create(ResearchRequest(query="构筑", demo_fault="rate_limited"))
    t = await finish(e, tid)
    assert t["status"] == "partial"
    assert t["context"]["errors"]
    assert not e.knowledge.claims(tid)
    s.max_tool_calls = 1
    tid = e.create(ResearchRequest(query="策略"))
    t = await finish(e, tid)
    assert t["status"] == "partial"
    assert t["context"]["budget"]["tool_calls"] == 1
    tid = e.create(ResearchRequest(query="策略"))
    with db.Session.begin() as ss:
        ss.get(Task, tid).status = "cancelled"
    await e.step(tid)
    assert db.task(tid)["context"]["budget"]["tool_calls"] == 0


async def test_quote_fabrication_wrong_object_rejected(stack):
    e = stack[3]
    tid = e.create(ResearchRequest(query="构筑"))
    ids = e.knowledge.ingest([stack[2].fixture.docs[0]])
    base = {
        "game_id": "fx-01",
        "feature_key": "parry",
        "value": True,
        "claim_text": "招架",
        "document_id": ids[0],
        "quote": "不存在的原文",
        "assessment_type": "fact",
    }
    accepted, rejected = e.knowledge.extract(tid, [base], ids, "synthetic-v1")
    assert not accepted and rejected
    base.update(game_id="fx-02", quote="招架")
    assert not e.knowledge.extract(tid, [base], ids, "synthetic-v1")[0]


async def test_cache_and_no_cross_mode_fallback(stack):
    s, db, reg, _ = stack
    gate = Gate(s)
    action = {"tool_name": "get_game_details", "arguments": {"game_id": "fx-01"}}
    assert not (await reg.call(action, gate)).from_cache
    assert (await reg.call(action, gate)).from_cache
    assert gate.ctx["budget"]["tool_calls"] == 2 and gate.ctx["budget"]["external_requests"] == 0
    reg.adapters = []
    result = await reg.call(action, gate)
    assert result.status == "unavailable" and result.error_code == "no_equivalent_capability"


async def test_same_capability_fallback(stack):
    s, _, reg, _ = stack

    class Down:
        name = "down"
        capabilities = ["get_reviews"]
        auth_requirements = []

        async def call(self, *args):
            raise SourceFailure("unavailable")

    reg.adapters = [Down(), reg.fixture]
    r = await reg.call({"tool_name": "get_reviews", "arguments": {"game_id": "fx-01"}}, Gate(s))
    assert r.provider == "fixture" and r.status == "available"


async def test_shortlist_deduplicates_and_never_fetches(stack, monkeypatch):
    settings, db, registry, _ = stack

    async def forbidden(*a, **kw):
        raise AssertionError("shortlist must be local")

    monkeypatch.setattr(registry, "call", forbidden)
    service = ShortlistService(settings, db)
    first = service.add(ShortlistInput(game_id="fx-01", reason="喜欢战斗节奏"))
    again = service.add(ShortlistInput(game_id="fx-01", reason="想看更多玩法"))
    assert first["id"] == again["id"] and len(service.list()) == 1
    assert service.list()[0]["reason"] == "想看更多玩法"
    service.remove(first["id"])
    assert service.list() == [] and db.list("notification") == []


def test_security_sanitization():
    assert "secret" not in redact("Authorization: Bearer_secret access_token=secret api_key=secret")
    assert "alert" not in clean_text("<script>alert(1)</script><b>safe</b>")


async def test_live_requires_configuration(stack):
    stack[0].llm_mode = "live"
    with pytest.raises(ValueError, match="LLM_BASE_URL"):
        stack[3].create(ResearchRequest(query="test"))


async def test_restart_uncertain_requires_resume(stack):
    e = stack[3]
    tid = e.create(ResearchRequest(query="策略"))
    with stack[1].Session.begin() as sess:
        t = sess.get(Task, tid)
        t.status = "running"
        t.context = t.context | {"inflight": {"kind": "tool"}}
    e.recover()
    assert stack[1].task(tid)["status"] == "paused"
    assert stack[1].task(tid)["context"]["recovery_note"]
