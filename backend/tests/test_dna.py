"""DNA rules tested against explicit synthetic expectations, not real-game quality labels."""

import copy
import json
from pathlib import Path
import pytest
from pydantic import ValidationError
from sqlalchemy import select
from app.dna.query import DnaQuery, Predicate, Soft, compile_query, tri, interval, known
from app.dna.ontology import FEATURES, valid_value
from app.dna.ranking import rank_game, sort_rows
from app.dna.projection import make_snapshot, current_features, rerank, get_snapshot
from app.dna.routes import add_references
from app.db import FeatureAssessment, DnaSnapshot, Claim, Report
from app.schemas import ResearchRequest, Finding
from conftest import finish

CASES = json.loads((Path(__file__).parents[2] / "evaluations/dna_query_cases.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_query_corpus(case):
    if case.get("invalid"):
        with pytest.raises((ValueError, ValidationError)):
            DnaQuery.model_validate(case["query"])
        return
    q = compile_query(case["text"]) if "text" in case else DnaQuery.model_validate(case["query"])
    payload = q.model_dump()
    for bucket, expected in case.get("expected", {}).items():
        rows = payload[bucket]
        for e in expected:
            assert any(all((r.get("condition", r)).get(k) == v for k, v in e.items()) for r in rows), (
                case["id"],
                payload,
            )
    if case.get("unparsed"):
        assert q.unparsed_fragments
    if case.get("count") is not None:
        assert len(q.soft_preferences) == case["count"]


def p(key="parry", value=True, **kw):
    return Predicate(feature_key=key, target=value, **kw)


def fs(**values):
    return {
        k: {"value": v, "evidence_status": "supported", "freshness": "fresh", "evidence_ids": ["e-" + k]}
        for k, v in values.items()
    }


def game(gid="a"):
    return {
        "id": gid,
        "name": gid,
        "platforms": ["PC"],
        "released": True,
        "kind": "base",
        "dataset_id": "synthetic-v1",
        "tags": [],
    }


def test_authority_types_and_generated_copies():
    assert len(FEATURES) == 18
    assert valid_value("grind_requirement", 0) and not valid_value("grind_requirement", False)
    assert valid_value("dodge", False) and not valid_value("dodge", 0)
    assert valid_value("session_length_minutes", [0, 30]) and not valid_value("session_length_minutes", [30, 0])
    root = Path(__file__).parents[2]
    assert (
        json.loads((root / "frontend/src/dna-ontology.json").read_text())
        == json.loads((root / "backend/app/dna/ontology.json").read_text())
        == json.loads((root / "docs/game_dna_rubric.yaml").read_text())
    )


@pytest.mark.parametrize(
    "a,b,op,expected",
    [
        (True, None, "all", "unknown"),
        (False, None, "all", "fail"),
        (True, None, "any", "pass"),
        (False, None, "any", "unknown"),
        (False, False, "any", "fail"),
        (True, True, "all", "pass"),
    ],
)
def test_three_valued_logic(a, b, op, expected):
    c = Predicate(kind=op, children=[p("parry"), p("dodge")])
    f = fs(parry=a, dodge=b)
    assert tri(c, f) == expected
    n = Predicate(kind="not", children=[c])
    assert tri(n, f) == {"pass": "fail", "fail": "pass", "unknown": "unknown"}[expected]


def test_intervals_unknown_and_evidence_coverage():
    c = Predicate(kind="any", children=[p("parry"), p("dodge")])
    q = DnaQuery(soft_preferences=[Soft(condition=c)])
    row = rank_game(game(), fs(parry=True), q, {})
    assert (row["match_lower"], row["match_upper"], row["coverage"], row["evidence_coverage"]) == (100, 100, 1, 0.5)
    row = rank_game(game(), {}, DnaQuery(soft_preferences=[Soft(condition=p())]), {})
    assert (
        row["fit_known"] is None
        and (row["match_lower"], row["match_upper"]) == (0, 100)
        and row["group"] == "insufficient"
    )
    row = rank_game(game(), {}, DnaQuery(), {})
    assert row["match_lower"] is None and row["coverage"] is None and row["group"] == "discovery"


def test_negative_hard_unknown_never_passes_and_soft_not_does_not_reward_unknown():
    q = DnaQuery(must_not=[p("corpse_recovery")], soft_preferences=[Soft(condition=p("parry", False))])
    row = rank_game(game(), {}, q, {})
    assert row["group"] == "needs_confirmation" and row["fit_known"] is None
    assert rank_game(game(), fs(corpse_recovery=True), q, {})["group"] == "excluded"
    assert rank_game(game(), fs(corpse_recovery=False, parry=False), q, {})["group"] == "recommended"


def test_more_known_support_beats_one_perfect_fact_stable_ties():
    q = DnaQuery(soft_preferences=[Soft(condition=p(k)) for k in ["parry", "dodge", "deckbuilding"]])
    a = rank_game(game("a"), fs(parry=True), q, {})
    b = rank_game(game("b"), fs(parry=True, dodge=True), q, {})
    assert sort_rows([a, b])[0]["game"]["id"] == "b"
    assert sort_rows([rank_game(game("z"), fs(parry=True), q, {}), a])[0]["game"]["id"] == "a"


def test_query_hash_dedup_weight_and_scope():
    a = DnaQuery(soft_preferences=[Soft(condition=p()), Soft(condition=p(origin="text"))])
    assert len(a.soft_preferences) == 1
    assert a.query_hash == DnaQuery.model_validate(a.model_dump()).query_hash
    assert (
        a.query_hash != DnaQuery.model_validate(a.model_dump() | {"discovery_scope": {"stores": ["steam"]}}).query_hash
    )
    assert (
        compile_query("", [{"feature_key": "session_length_minutes", "target": [10, 30]}])
        .soft_preferences[0]
        .condition.operator
        == "overlap"
    )
    assert known(fs(parry=True), p(scope={"platform": "Switch"})) is None
    assert known({"parry": {"value": True, "evidence_status": "supported", "freshness": "stale"}}, p()) is None


async def seed(stack, gid="fx-02"):
    e = stack[3]
    tid = e.create(ResearchRequest(kind="scout", game_ids=[gid], query="策略和构筑"))
    t = await finish(e, tid)
    assert t["status"] == "completed"
    return tid


async def test_verifier_missing_decision_semantics_and_rejection_invalidate(stack):
    tid = await seed(stack)
    db = stack[1]
    e = stack[3]
    assert current_features(db, "synthetic-v1", "fx-02")["deckbuilding"]["value"] is True
    claims = e.knowledge.claims(tid)
    # Blanket live flag and an empty rejected list must never grant support.
    e.knowledge.verify(tid, live_verified=True, verdicts=[])
    assert all(c["evidence_status"] == "insufficient" for c in e.knowledge.claims(tid))
    assert current_features(db, "synthetic-v1", "fx-02")["deckbuilding"]["value"] is None
    c = claims[0]
    e.knowledge.verify(
        tid,
        verdicts=[
            {
                "claim_id": c["id"],
                "verdict": "supported",
                "reason": "unrelated",
                "evidence_ids": ["other-game-evidence"],
            }
        ],
    )
    assert all(c["evidence_status"] == "insufficient" for c in e.knowledge.claims(tid))


async def test_snapshot_immutable_after_new_revisions_and_dataset_boundaries(stack):
    tid = await seed(stack)
    db = stack[1]
    e = stack[3]
    q = DnaQuery(soft_preferences=[Soft(condition=p("deckbuilding"))])
    snap = make_snapshot(db, "synthetic-v1", [db.get("game", "fx-02")], q, {})
    before = get_snapshot(db, snap["id"], "synthetic-v1")
    e.knowledge.verify(tid, verdicts=[])
    assert get_snapshot(db, snap["id"], "synthetic-v1") == before
    with pytest.raises(ValueError, match="来源已更新或失效"):
        rerank(db, snap["id"], "synthetic-v1", q)
    assert current_features(db, "live", "fx-02")["deckbuilding"]["value"] is None
    with pytest.raises(ValueError, match="dataset_mismatch"):
        get_snapshot(db, snap["id"], "live")
    assert get_snapshot(db, snap["id"], "synthetic-v1")["assessment_revision_ids"]


async def test_reference_inherits_only_selected_known_no_override(stack):
    await seed(stack)
    db = stack[1]
    q = DnaQuery(
        reference_game_ids=["fx-02"],
        selected_features=["deckbuilding", "stamina_management"],
        must=[p("deckbuilding", False)],
    )
    out = add_references(db, "synthetic-v1", q)
    assert [s.condition.feature_key for s in out.soft_preferences] == ["stamina_management"]
    assert out.soft_preferences[0].condition.target is False
    out = add_references(db, "synthetic-v1", DnaQuery(reference_game_ids=["fx-23"], selected_features=["parry"]))
    assert not out.soft_preferences and out.unparsed_fragments
    with pytest.raises(ValueError):
        add_references(db, "live", q)


async def test_rerank_no_external_work_and_hard_relax_confirmation(stack, monkeypatch):
    await seed(stack)
    db = stack[1]
    reg = stack[2]
    engine = stack[3]

    async def forbidden(*a, **k):
        raise AssertionError("Local operation attempted external work")

    monkeypatch.setattr(reg, "call", forbidden)
    monkeypatch.setattr(engine.llm, "run", forbidden)
    q = DnaQuery(must=[p("deckbuilding")], soft_preferences=[Soft(condition=p("mid_run_save"))])
    snap = make_snapshot(db, "synthetic-v1", [db.get("game", "fx-02")], q, {})
    result = rerank(db, snap["id"], "synthetic-v1", q)
    assert result["external_requests"] == result["model_calls"] == 0
    assert result["candidate_snapshot_id"] == snap["id"]
    relaxed = DnaQuery()
    with pytest.raises(ValueError, match="confirmation"):
        rerank(db, snap["id"], "synthetic-v1", relaxed)
    assert rerank(db, snap["id"], "synthetic-v1", relaxed, True)["results"]


async def test_rerank_false_is_explicit_and_scope_cannot_expand(stack):
    await seed(stack)
    db = stack[1]
    q = DnaQuery(
        discovery_scope={"stores": ["steam"]},
        soft_preferences=[Soft(condition=p("stamina_management")), Soft(condition=p("dodge"))],
    )
    g = db.get("game", "fx-02")
    g["store_links"] = {"steam": "https://store.steampowered.com/"}
    snap = make_snapshot(db, "synthetic-v1", [g], q, {})
    result = rerank(db, snap["id"], "synthetic-v1", q)
    assert result["results"][0]["features"]["stamina_management"]["value"] is False
    expanded = DnaQuery(soft_preferences=q.soft_preferences)
    with pytest.raises(ValueError, match="scope_expansion"):
        rerank(db, snap["id"], "synthetic-v1", expanded)


async def test_wrong_scope_quote_and_metadata_cannot_assert_gameplay(stack):
    db, e = stack[1], stack[3]
    tid = e.create(ResearchRequest(kind="scout", game_ids=["fx-02"]))
    ids = e.knowledge.ingest(
        [
            {
                "dataset_id": "synthetic-v1",
                "game_id": "fx-02",
                "provider": "fixture",
                "external_id": "scope-test",
                "kind": "details",
                "title": "Scope",
                "text": "仅困难模式有死亡回收。",
                "scope": {"difficulty": "hard"},
                "provenance_type": "synthetic",
            }
        ]
    )
    f = Finding(
        game_id="fx-02",
        feature_key="corpse_recovery",
        value=True,
        claim_text="有死亡回收",
        document_id=ids[0],
        quote="仅困难模式有死亡回收。",
    )
    accepted, rejected = e.knowledge.extract(tid, [f], ids, "synthetic-v1")
    assert not accepted and rejected
    f.scope = {"difficulty": "hard"}
    accepted, _ = e.knowledge.extract(tid, [f], ids, "synthetic-v1")
    assert accepted
    claim = e.knowledge.claims(tid)[0]
    e.knowledge.verify(
        tid,
        verdicts=[
            {
                "claim_id": claim["id"],
                "verdict": "supported",
                "reason": "原文明确限定困难模式",
                "evidence_ids": claim["evidence_ids"],
            }
        ],
    )
    features = current_features(db, "synthetic-v1", "fx-02")
    assert known(features, p("corpse_recovery")) is None
    assert known(features, p("corpse_recovery", scope={"difficulty": "hard"})) is True
    assert known(features, p("corpse_recovery", scope={"difficulty": "easy"})) is None


async def test_cache_second_task_and_round_limits(stack):
    await seed(stack)
    e = stack[3]
    tid = e.create(ResearchRequest(kind="scout", game_ids=["fx-02"], query="策略和构筑"))
    task = await finish(e, tid)
    assert task["context"]["dna_followup_rounds"] <= 2
    assert task["context"]["budget"]["external_requests"] == 0
    assert task["context"]["budget"]["tool_calls"] == 0
    assert task["context"]["dna_snapshot_id"]


def test_complex_size_limits_range_conflict_and_op_types():
    for data in [
        {
            "must": [
                {"feature_key": "session_length_minutes", "operator": "eq", "target": [0, 20]},
                {"feature_key": "session_length_minutes", "operator": "eq", "target": [30, 50]},
            ]
        },
        {"must": [{"feature_key": "parry", "operator": "gte", "target": True}]},
        {
            "must": [
                {
                    "kind": "not",
                    "children": [{"kind": "not", "children": [{"kind": "not", "children": [p().model_dump()]}]}],
                }
            ]
        },
        {
            "soft_preferences": [
                {"condition": p("mechanical_difficulty", i % 5).model_dump(), "weight": 1} for i in range(5)
            ]
            + [{"condition": p("grind_requirement", i % 5).model_dump(), "weight": 1} for i in range(5)]
            + [{"condition": p("strategic_depth", i).model_dump(), "weight": 1} for i in range(3)]
        },
        {"soft_preferences": [{"condition": p().model_dump(), "weight": float("inf")}]},
    ]:
        with pytest.raises(ValueError):
            DnaQuery.model_validate(data)
    q = compile_query("必须弹反且不能有死亡回收")
    assert [x.feature_key for x in q.must] == ["parry"] and [x.feature_key for x in q.must_not] == ["corpse_recovery"]


async def test_semantic_mismatch_is_not_supported_even_when_quote_exists(stack):
    e = stack[3]
    tid = e.create(ResearchRequest(kind="scout", game_ids=["fx-02"]))
    ids = e.knowledge.ingest(
        [
            {
                "dataset_id": "synthetic-v1",
                "game_id": "fx-02",
                "provider": "fixture",
                "external_id": "tag",
                "kind": "details",
                "title": "Metadata",
                "text": "这是一款热门动作游戏。",
                "provenance_type": "synthetic",
            }
        ]
    )
    f = Finding(
        game_id="fx-02",
        feature_key="parry",
        value=True,
        claim_text="有弹反",
        document_id=ids[0],
        quote="这是一款热门动作游戏。",
    )
    e.knowledge.extract(tid, [f], ids, "synthetic-v1")
    from app.tools import Gate

    v = await e.llm.run(
        "verifier", {"claims": e.knowledge.claims(tid), "documents": e.knowledge.documents(ids)}, Gate(stack[0])
    )
    e.knowledge.verify(tid, verdicts=v.verdicts)
    assert e.knowledge.claims(tid)[0]["evidence_status"] != "supported"


async def test_assessment_repeated_write_concurrency_and_negative_evidence(stack):
    from concurrent.futures import ThreadPoolExecutor
    from app.dna.projection import persist_assessments

    tid = await seed(stack)
    db, e = stack[1], stack[3]
    claims = e.knowledge.claims(tid)
    documents = e.knowledge.documents([c["document_id"] for c in claims])
    with db.Session() as s:
        before = len(s.scalars(select(FeatureAssessment)).all())
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: persist_assessments(db, claims, documents), range(2)))
    with db.Session() as s:
        assert len(s.scalars(select(FeatureAssessment)).all()) == before
    f = current_features(db, "synthetic-v1", "fx-02")["stamina_management"]
    assert f["value"] is False and f["evidence_ids"]


def test_old_database_migration_preserves_rows_and_reports(tmp_path, monkeypatch):
    from app.db import DB, Base, Task, Record
    from alembic.config import Config
    from alembic import command

    url = "sqlite:///" + str(tmp_path / "legacy.db")
    db = DB(url)
    # Real v1 table set, with a historic supported claim and immutable report.
    legacy = [t for t in Base.metadata.sorted_tables if t.name not in ["feature_assessments", "dna_snapshots"]]
    Base.metadata.create_all(db.engine, tables=legacy)
    with db.Session.begin() as s:
        s.add(Task(id="legacy-task", request={}, context={}, status="completed"))
        s.flush()
        s.add(Report(task_id="legacy-task", version=1, data={"old": "unchanged"}, markdown="original report"))
        s.add(
            Claim(
                id="legacy-claim",
                task_id="legacy-task",
                game_id="fx-old",
                data={"feature_key": "parry", "value": True, "evidence_status": "supported", "schema_version": 1},
            )
        )
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).parents[1] / "migrations"))
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    with db.Session() as s:
        assert s.get(Report, ("legacy-task", 1)).markdown == "original report"
        assert s.get(Claim, "legacy-claim").data["schema_version"] == 1
        assert not list(s.scalars(select(FeatureAssessment)))
    assert current_features(db, "synthetic-v1", "fx-old")["parry"]["value"] is None
    command.downgrade(config, "0001")
    with db.Session() as s:
        assert s.get(Report, ("legacy-task", 1)).data == {"old": "unchanged"}


def test_api_single_exploration_entry_validation_and_local_endpoints(stack):
    from fastapi.testclient import TestClient
    from app.main import create_app

    with TestClient(create_app(stack[0], stack[1], False)) as c:
        q = c.post("/api/dna/parse", json={"text": "必须弹反且不能有死亡回收"}).json()
        assert q["must"] and q["must_not"]
        invalid = c.post("/api/dna/parse", json={"text": "必须弹反，必须不要弹反"})
        assert invalid.status_code == 422 and invalid.json()["error"]["code"] == "validation_error"
        refs = c.get("/api/dna/references?dataset_id=synthetic-v1&q=星图").json()
        assert refs["model_calls"] == refs["external_requests"] == 0
        assert c.post("/api/dna/preferences", json={"query": q}).status_code == 404
        assert c.post("/api/dna/graph", json={}).status_code == 404
        assert c.post("/api/games/compare", json={}).status_code in (404, 405)
        assert c.post("/api/research", json={"kind": "analyst", "game_ids": ["fx-02"]}).status_code == 422
        assert c.get("/api/games/fx-02/dna?dataset_id=live").status_code == 404


async def test_human_accept_only_explicitly_selected_claims(stack):
    from app.schemas import RoleModes, HumanInput

    e = stack[3]
    tid = e.create(ResearchRequest(kind="scout", game_ids=["fx-02"], role_modes=RoleModes(verifier="human")))
    task = await finish(e, tid)
    assert task["status"] == "paused"
    claims = e.knowledge.claims(tid)
    chosen = claims[0]["id"]
    e.human(
        tid,
        HumanInput(
            action_id="human-explicit-accept",
            state_version=task["version"],
            decision="accept",
            claim_ids=[chosen],
            note="已阅读这一条原文",
        ),
    )
    task = await finish(e, tid)
    checked = e.knowledge.claims(tid)
    assert next(c for c in checked if c["id"] == chosen)["evidence_status"] == "supported"
    assert all(c["evidence_status"] == "insufficient" for c in checked if c["id"] != chosen)


def test_backup_helper_copies_consistently_and_refuses_overwrite(tmp_path):
    import os, sqlite3, subprocess, sys

    source = tmp_path / "original.db"
    target = tmp_path / "backup.db"
    with sqlite3.connect(source) as conn:
        conn.execute("CREATE TABLE preserved (value INTEGER)")
        conn.execute("INSERT INTO preserved VALUES (7)")
    cmd = [sys.executable, str(Path(__file__).parents[2] / "scripts/backup_database.py"), "--output", str(target)]
    env = {**os.environ, "DATABASE_URL": "sqlite:///" + str(source)}
    result = subprocess.run(cmd, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(target) as conn:
        assert conn.execute("SELECT value FROM preserved").fetchone()[0] == 7
    assert subprocess.run(cmd, env=env, capture_output=True).returncode != 0


def test_text_overflow_rejected_before_any_hard_conditions_can_be_dropped():
    with pytest.raises(ValueError, match="12"):
        compile_query("，".join("必须" + key for key in FEATURES))
