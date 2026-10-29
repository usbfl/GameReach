"""The user's failed queries, plus protocol/network failure contracts. No live calls."""

import asyncio
import json
import httpx
import pytest
import respx
from pydantic import SecretStr

from app.agents import LiveLLM
from app.config import Settings
from app.discovery import discovery_action, scope_constraints
from app.dna.query import compile_query, as_legacy
from app.engine import Engine
from app.quality import exploration_outcome
from app.ranking import hard_filter
from app.schemas import ResearchRequest
from app.tools import HTTP, Gate, Registry, BudgetExceeded
from conftest import finish


QUERIES = [
    "找几款重视战斗技巧和构筑变化、不需要大量刷装备的 PC 游戏。",
    "动作之外还要有构筑层",
    "魂、多构筑、死亡惩罚低、跑图简单",
    "从《DARK SOULS™ III》《Nioh》系列到《Monster Hunter: World》《Hades》，"
    "你的库里反复出现需要读招、抓时机、反复挑战才能过关的动作游戏。"
    "这类体验的乐趣往往在于把一套战斗系统练到熟练，用失败换手感，再靠自己的操作把强敌拆解掉。"
    "可以继续往「战斗系统有深度、Boss 设计讲究」的方向探索，也可以留意难度曲线是否给足练习空间。",
]


def conditions(text):
    return {s.condition.feature_key: s.condition for s in compile_query(text).soft_preferences}


def test_original_interest_queries_keep_their_meaning():
    first = conditions(QUERIES[0])
    assert first["mechanical_difficulty"].operator == "gte"
    assert first["build_diversity"].operator == "gte"
    assert first["grind_requirement"].operator == "lte"
    assert any(p.feature_key == "grind_requirement" and p.operator == "lte" and p.target == 2
               for p in compile_query(QUERIES[0]).must)
    second = conditions(QUERIES[1])
    assert second["combat_mode"].target == "realtime" and second["build_diversity"].target == 3
    third = conditions(QUERIES[2])
    assert third["death_penalty"].operator == third["navigation_complexity"].operator == "lte"
    assert "runback_penalty" not in third and "corpse_recovery" not in third
    # A gentle difficulty curve does not request lower/higher execution difficulty.
    last = conditions(QUERIES[3])
    assert last["mechanical_difficulty"].target == 3
    assert last["mechanical_difficulty"].source_text_span[0] < QUERIES[3].index("难度曲线")


def test_polarity_does_not_leak_between_features():
    prefs = conditions("高技巧战斗和少刷装备，构筑丰富但死亡惩罚低")
    assert prefs["mechanical_difficulty"].operator == prefs["build_diversity"].operator == "gte"
    assert prefs["grind_requirement"].operator == prefs["death_penalty"].operator == "lte"
    assert conditions("低跑图惩罚")["runback_penalty"].operator == "lte"


def test_recall_uses_intent_not_reference_title_and_preserves_pc_on_rerank():
    action = discovery_action(QUERIES[3], as_legacy(compile_query(QUERIES[3])), {})
    assert "soulslike" not in action["arguments"]["keyword_hints"]
    assert "soulslike" in discovery_action(QUERIES[2], [], {})["arguments"]["keyword_hints"]
    action = discovery_action(QUERIES[0], as_legacy(compile_query(QUERIES[0])), {})
    assert action["arguments"]["platforms"] == ["pc"]
    assert "action-rpg" in action["arguments"]["keyword_hints"]
    constraints = scope_constraints({"released_only": False}, {}, QUERIES[0])
    again = scope_constraints(constraints, {})
    assert again["platform"] == "PC"
    assert hard_filter({"id": "g", "name": "G", "kind": "base", "platforms": ["PlayStation 5"]}, again)[0] == "fail"


async def test_routine_live_plan_does_not_need_model_to_copy_actions(stack):
    settings, db, _, _ = stack
    settings.data_mode = settings.llm_mode = "live"
    settings.igdb_client_id = "contract"
    settings.igdb_client_secret = SecretStr("contract")
    settings.llm_base_url, settings.llm_model = "https://contract.invalid", "contract"
    settings.llm_api_key = SecretStr("contract")
    registry = Registry(settings, db)
    engine = Engine(settings, db, registry)

    class NeverPlanner:
        async def run(self, *args):
            raise AssertionError("Routine source actions do not need model planning")

    engine.llm = NeverPlanner()
    tid = engine.create(ResearchRequest(query=QUERIES[0]))
    await engine.step(tid)
    await engine.step(tid)
    with respx.mock() as mock:
        mock.post("https://id.twitch.tv/oauth2/token").respond(200, json={"access_token": "contract"})
        mock.post("https://api.igdb.com/v4/games").respond(200, json=[{
            "id": 1, "name": "Authored", "game_type": {"type": "main_game"},
            "platforms": [{"name": "PC (Microsoft Windows)"}],
            "websites": [{"url": "https://store.steampowered.com/app/1/"}],
        }])
        await engine.step(tid)
        await engine.step(tid)
    task = db.task(tid)
    assert task["phase"] == "retrieve" and task["context"]["budget"]["llm_calls"] == 0
    assert {a["tool_name"] for a in task["context"]["queue"]} == {"get_game_details", "get_reviews"}
    await registry.close()


async def test_research_schema_types_values_and_discards_only_malformed_claims():
    settings = Settings(_env_file=None, llm_base_url="https://contract.invalid", llm_model="contract", llm_api_key="contract")
    http = HTTP(settings)
    gate = Gate(settings)
    payload = {"role": "researcher", "summary": "Contract", "findings": [
        {"game_id": "g", "document_id": "d", "aspect": "core_loop", "quote": "Safe quoted loop", "claim_text": "Safe loop"},
        {"game_id": "g", "document_id": "d", "feature_key": "mechanical_difficulty",
         "value": {"label": "secret-canary"}, "quote": "Safe quoted loop", "claim_text": "Unsupported"},
    ]}
    with respx.mock() as mock:
        route = mock.post("https://contract.invalid/chat/completions").respond(200, json={
            "choices": [{"message": {"tool_calls": [{"function": {
                "name": "submit_role_result", "arguments": json.dumps(payload),
            }}]}}],
        })
        result = await LiveLLM(settings, http).run("researcher", {}, gate)
        body = json.loads(route.calls[0].request.content)
        schema = body["tools"][0]["function"]["parameters"]
        assert "proposed_actions" not in schema["properties"] and "Action" not in schema["$defs"]
        feature = next(x for x in schema["$defs"]["Finding"]["anyOf"]
                       if x["properties"]["feature_key"].get("const") == "mechanical_difficulty")
        assert feature["properties"]["value"]["anyOf"][0] == {"type": "integer", "minimum": 0, "maximum": 4}
        assert len(result.findings) == 1 and result.findings[0].feature_key is None
        assert len(route.calls) == 2 and gate.ctx["model_protocol_errors"][-1]["discarded_findings"] == 1
        assert "secret-canary" not in json.dumps(gate.ctx)
    await http.client.aclose()


async def test_proxy_block_preserves_honest_report_and_is_not_retried(stack):
    settings, db, registry, engine = stack
    settings.llm_mode = "live"
    settings.llm_base_url, settings.llm_model = "https://contract.invalid", "contract"
    settings.llm_api_key = SecretStr("contract")
    engine.llm = LiveLLM(settings, registry.http)
    with respx.mock() as mock:
        route = mock.post("https://contract.invalid/chat/completions").mock(side_effect=httpx.ProxyError("blocked"))
        tid = engine.create(ResearchRequest(query="构筑", game_ids=["fx-01"]))
        task = await finish(engine, tid)
        assert task["status"] == "partial" and task["context"]["errors"] == ["proxy_blocked"]
        assert len(route.calls) == 1
    from app.db import Report
    with db.Session() as session:
        report = session.get(Report, (tid, 1)).data
    assert report["outcome"]["code"] == "execution_error" and report["results"] == []
    assert "网络代理" in report["outcome"]["message"]


async def test_total_active_deadline_cancels_a_slow_request():
    settings = Settings(_env_file=None, max_active_seconds=0.02)

    async def slow(request):
        await asyncio.sleep(0.1)
        return httpx.Response(200, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(slow))
    http = HTTP(settings, client)
    with pytest.raises(BudgetExceeded):
        await http.request(Gate(settings), "GET", "https://contract.invalid")
    await client.aclose()


def test_empty_report_explains_incomplete_research_instead_of_no_matching_games():
    assert exploration_outcome([], [], ["external_requests 预算耗尽"], {})["code"] == "budget_exhausted"
    assert exploration_outcome([], [], ["invalid_model_protocol"], {})["code"] == "execution_error"
    assert exploration_outcome([], [], [], {})["code"] == "no_candidates"


def test_live_shortlist_reserves_enough_budget_for_complete_cards(stack):
    settings, db, _, engine = stack
    settings.llm_mode = "live"
    ids = []
    for i in range(5):
        gid = f"igdb-{i}"
        ids.append(gid)
        db.put("game", gid, {"id": gid, "name": gid, "dataset_id": "live", "released": True,
                             "kind": "base", "platforms": ["PC"], "external_ids": {"steam": str(i + 1)}})
    ctx = {"data_mode": "live", "dataset_id": "live", "candidates": ids, "selected": [],
           "constraints": {}, "preferences": [], "budget": {"external_requests": 2, "llm_calls": 0}}
    assert len(engine.candidates(ctx, {"kind": "scout", "query": QUERIES[0]})) == 2
    assert ctx["investigation_plan"]["external_reserve_per_game"] == 9


def test_same_query_continuation_prioritizes_new_evidence_without_changing_conditions(stack):
    from app.db import Task, Report
    from app.agents import PROMPT_VERSION

    settings, db, _, engine = stack
    settings.llm_mode = "live"
    ids = [f"igdb-{i}" for i in range(4)]
    for gid in ids:
        db.put("game", gid, {"id": gid, "name": gid, "dataset_id": "live", "released": True,
                             "kind": "base", "platforms": ["PC"], "external_ids": {"steam": gid[5:]}})
    query = compile_query(QUERIES[0]).model_dump()
    with db.Session.begin() as session:
        session.add(Task(id="previous", phase="report", status="completed", request={"query": QUERIES[0]},
                         context={"dataset_id": "live", "prompt_version": PROMPT_VERSION, "dna_query": query}))
        session.flush()
        session.add(Report(task_id="previous", version=1, markdown="Contract", data={
            "withheld_candidates": [{"game_id": gid, "diagnosis": {"reviews_read": 60},
                                     "reasons": [{"code": "preference_unknown"}]} for gid in ids[:2]],
        }))
    ctx = {"task_id": "new", "data_mode": "live", "dataset_id": "live", "dna_query": query,
           "candidates": ids, "selected": [], "constraints": {}, "preferences": [],
           "budget": {"external_requests": 2, "llm_calls": 0}}
    assert engine.candidates(ctx, {"kind": "scout", "query": QUERIES[0]}) == ids[2:]
    assert ctx["dna_query"] == query and ctx["budget"] == {"external_requests": 2, "llm_calls": 0}


async def test_changed_rubric_does_not_reuse_old_ordinal_assessments(stack):
    from sqlalchemy import select
    from app.db import FeatureAssessment, DnaSnapshot, Report
    from app.dna.projection import current_features, rerank
    from app.dna.query import DnaQuery

    _, db, _, engine = stack
    tid = engine.create(ResearchRequest(query="弹反与构筑", game_ids=["fx-01"]))
    task = await finish(engine, tid)
    assert task["status"] == "completed"
    before = current_features(db, "synthetic-v1", "fx-01")
    assert before["build_diversity"]["value"] is not None and before["parry"]["value"] is True
    with db.Session.begin() as session:
        for row in session.scalars(select(FeatureAssessment).where(FeatureAssessment.feature_key == "build_diversity")):
            row.data = {**row.data, "rubric_version": "2.0"}
        report = session.get(Report, (tid, 1)).data
        snapshot = session.get(DnaSnapshot, report["candidate_snapshot_id"])
        snapshot.data = {**snapshot.data, "rubric_version": "2.0"}
    after = current_features(db, "synthetic-v1", "fx-01")
    assert after["build_diversity"]["value"] is None and after["parry"]["value"] is True
    with pytest.raises(ValueError, match="量表已更新"):
        rerank(db, report["candidate_snapshot_id"], "synthetic-v1", DnaQuery.model_validate(report["dna_query"]))
