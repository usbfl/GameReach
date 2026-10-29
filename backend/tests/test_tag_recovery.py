"""Non-happy-path regression cases. All responses in this file are authored mocks."""

import json
import copy
import httpx
import pytest
from sqlalchemy import select
from app.db import Report
from app.schemas import ResearchRequest
from app.tags import workflow
from app.tags.ranking import rank
from app.tools import BudgetExceeded, SourceFailure, SteamSource, HTTP, Gate
from conftest import finish


def report_for(db, tid):
    with db.Session() as session:
        return session.scalar(select(Report).where(Report.task_id == tid)).data


@pytest.mark.asyncio
async def test_second_game_budget_keeps_first_verified_and_unknown_is_not_zero(stack, monkeypatch):
    s, db, registry, engine = stack
    original = registry.call
    tid = engine.create(ResearchRequest(recommendation_mode="player_tags", query="构筑", game_ids=["fx-01", "fx-02"]))

    async def limited(action, *args, **kwargs):
        if action["arguments"].get("game_id") == "fx-02":
            assert any(c["evidence_status"] == "supported" for c in engine.knowledge.claims(tid))
            raise BudgetExceeded("external_requests 预算耗尽")
        return await original(action, *args, **kwargs)

    monkeypatch.setattr(registry, "call", limited)
    await finish(engine, tid)
    report = report_for(db, tid)
    first, second = report["results"]
    assert first["game"]["id"] == "fx-01" and first["supporting_reviews"] > 0
    assert second["match"] is None and second["coverage"] is None and second["rank_score"] is None
    assert second["diagnosis"]["code"] == "budget_stopped"
    assert report["diagnostics"]["with_matches"] == 1
    assert report["query"] == "构筑" and db.task(tid)["status"] == "partial"


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_stage", ["extract", "verify"])
async def test_model_failure_isolated_to_game_and_later_game_finishes(stack, monkeypatch, failed_stage):
    s, db, registry, engine = stack
    tid = engine.create(ResearchRequest(recommendation_mode="player_tags", query="构筑", game_ids=["fx-01", "fx-02"]))
    await engine.step(tid)  # Real interpretation is separately covered by protocol tests.
    task = db.task(tid)
    ctx = copy.deepcopy(task["context"])
    ctx["llm_mode"] = "live"
    s.llm_mode = "live"
    engine.commit(tid, ctx, task["phase"])
    calls = []

    async def model(engine, tid, ctx, gate, stage, payload):
        from app.tags.models import Extraction, Verification

        gid = payload["documents"][0]["game_id"]
        calls.append((gid, stage))
        if gid == "fx-01" and stage == failed_stage:
            raise SourceFailure("invalid_tag_model_protocol")
        if stage == "extract":
            findings = workflow.demo_findings(payload["documents"], payload["intents"])
            return Extraction(
                summary="合成契约测试", findings=[{k: v for k, v in f.items() if k != "feature_key"} for f in findings]
            )
        return Verification(
            summary="合成契约测试",
            verdicts=[
                dict(claim_id=c["id"], verdict="supported", reason="Authored fixture", evidence_ids=c["evidence_ids"])
                for c in payload["claims"]
            ],
        )

    monkeypatch.setattr(workflow, "model_call", model)
    await finish(engine, tid)
    rows = {r["game"]["id"]: r for r in report_for(db, tid)["results"]}
    assert rows["fx-02"]["supporting_reviews"] > 0
    assert rows["fx-01"]["supporting_reviews"] == 0
    assert rows["fx-01"]["diagnosis"]["code"] == (
        "analysis_failed" if failed_stage == "extract" else "verification_failed"
    )
    assert calls.index(("fx-02", "verify")) > calls.index(("fx-02", "extract"))


@pytest.mark.asyncio
async def test_source_failure_has_per_game_reason_without_fake_metric(stack):
    s, db, registry, engine = stack
    tid = engine.create(ResearchRequest(recommendation_mode="player_tags", query="构筑", demo_fault="rate_limited"))
    await finish(engine, tid)
    report = report_for(db, tid)
    selected = [r for r in report["results"] if r["diagnosis"].get("review_fetch")]
    assert selected and all(r["diagnosis"]["code"] == "source_failed" for r in selected)
    assert all(r["coverage"] is None and r["rank_score"] is None for r in report["results"])
    assert all(r["diagnosis"]["errors"][0]["code"] == "rate_limited" for r in selected)
    assert report["diagnostics"]["with_reviews"] == 0


@pytest.mark.asyncio
async def test_scope_only_and_unrelated_findings_have_distinct_diagnostics(stack):
    s, db, registry, engine = stack
    tid = engine.create(ResearchRequest(recommendation_mode="player_tags", query="构筑", game_ids=["fx-01"]))
    await finish(engine, tid)
    ctx = db.task(tid)["context"]
    docs = engine.knowledge.documents(ctx["document_ids"])
    claims = engine.knowledge.claims(tid)
    for c in claims:
        c["scope"] = {"stage": "endgame"}
    row = rank(db, [db.get("game", "fx-01")], claims, docs, ctx)[0]
    assert row["diagnosis"]["code"] == "scope_only" and row["match"] is None
    assert row["diagnosis"]["contextual"] > 0


@pytest.mark.asyncio
async def test_rating_and_first_reviews_survive_later_rate_limit(stack):
    s, db, _, _ = stack
    db.put("game", "igdb-1", {"external_ids": {"steam": "123"}})
    seen = []

    async def handler(request):
        p = json.loads(request.url.params["input_json"])
        seen.append(p)
        if len(seen) > 1:
            return httpx.Response(429, headers={"Retry-After": "60"})
        assert p["review_type"] == 0 and p["purchase_type"] == 0
        return httpx.Response(
            200,
            json={
                "response": {
                    "reviews": [{"recommendationid": "1", "review": "Authored complete review.", "author": {}}],
                    "query_summary": {"total_positive": 90, "total_negative": 10},
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await SteamSource(s, HTTP(s, client), db, True).call("get_reviews", {"game_id": "igdb-1"}, Gate(s))
    assert result["rating"]["positive"] == 90
    assert result["sampling"]["errors"] == ["rate_limited"]
    assert len(result["documents"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,valid", [({"total_matching": 0}, True), ({"query_summary": {"num_reviews": 0}}, True), ({}, False)]
)
async def test_empty_protobuf_response_requires_explicit_zero(stack, body, valid):
    s, db, _, _ = stack
    db.put("game", "igdb-1", {"external_ids": {"steam": "123"}})
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"response": body}))
    ) as client:
        call = SteamSource(s, HTTP(s, client), db, True).call("get_reviews", {"game_id": "igdb-1"}, Gate(s))
        if valid:
            assert (await call)["documents"] == []
        else:
            with pytest.raises(SourceFailure, match="invalid_schema"):
                await call


@pytest.mark.asyncio
async def test_live_http_contract_through_interpret_recall_reviews_extract_verify(stack):
    """Actual live classes and JSON protocol, with authored HTTP responses only."""
    from pydantic import SecretStr
    from app.tools import Registry
    from app.engine import Engine

    s, db, _, _ = stack
    s.data_mode = s.llm_mode = "live"
    s.igdb_client_id = "synthetic-client"
    s.igdb_client_secret = SecretStr("synthetic-secret")
    s.llm_api_key = SecretStr("synthetic-key")
    s.llm_base_url = "https://model.example.test/v1"
    s.llm_model = "contract-test"
    s.max_investigated_games = 1
    events = []
    review = "Changing weapons and skill combinations changes how I play each fight. This is an authored test review."

    async def respond(req):
        if req.url.host == "id.twitch.tv":
            return httpx.Response(200, json={"access_token": "synthetic-token", "expires_in": 3600})
        if req.url.host == "api.igdb.com":
            events.append("catalog")
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 123,
                        "name": "Authored contract game",
                        "summary": "An authored game",
                        "websites": [{"url": "https://store.steampowered.com/app/123/"}],
                        "genres": [{"name": "Role-playing (RPG)"}],
                        "keywords": [{"name": "character customization"}],
                        "platforms": [{"name": "PC (Microsoft Windows)"}],
                        "game_type": {"type": "main_game"},
                        "game_status": {"status": "released"},
                    }
                ],
            )
        if req.url.host == "store.steampowered.com":
            return httpx.Response(
                200,
                json={
                    "123": {
                        "success": True,
                        "data": {
                            "steam_appid": 123,
                            "name": "Authored contract game",
                            "short_description": "Authored.",
                            "genres": [],
                            "release_date": {"coming_soon": False},
                        },
                    }
                },
            )
        if req.url.host == "api.steampowered.com":
            events.append("reviews")
            p = json.loads(req.url.params["input_json"])
            return httpx.Response(
                200,
                json={
                    "response": {
                        "reviews": [
                            {
                                "recommendationid": str(p["review_type"]),
                                "review": review + str(p["review_type"]),
                                "author": {"steamid": str(76561190000000000 + p["review_type"])},
                            }
                        ],
                        "query_summary": {"total_positive": 90, "total_negative": 10},
                    }
                },
            )
        assert req.url.host == "model.example.test"
        body = json.loads(req.content)
        payload = json.loads(body["messages"][1]["content"])
        if "query" in payload:
            events.append("interpret")
            result = {
                "summary": "希望构筑有变化",
                "intents": [{"tag_id": "build_variety", "source_quote": "构筑"}],
                "unmapped": [],
            }
        elif "claims" in payload:
            events.append("verify")
            assert all(review in d["text"] for d in payload["documents"] if d["kind"] == "reviews")
            result = {
                "summary": "核验作者的完整正文",
                "verdicts": [
                    {
                        "claim_id": c["id"],
                        "verdict": "supported",
                        "reason": "Authored review supports this claim.",
                        "evidence_ids": c["evidence_ids"],
                    }
                    for c in payload["claims"]
                ],
            }
        else:
            events.append("extract")
            assert all(d["complete"] and review in d["text"] for d in payload["documents"] if d["kind"] == "reviews")
            result = {
                "summary": "提取完整评价",
                "findings": [
                    {
                        "game_id": d["game_id"],
                        "document_id": d["id"],
                        "aspect": "tag",
                        "tag_id": "build_variety",
                        "tag_polarity": "present",
                        "scope": {},
                        "quote": review,
                        "claim_text": "武器与技能组合改变战斗方式。",
                    }
                    for d in payload["documents"]
                    if d["kind"] == "reviews"
                ]
                + [
                    {
                        "game_id": d["game_id"],
                        "document_id": d["id"],
                        "aspect": "core_loop",
                        "quote": d["text"],
                        "claim_text": "合成游戏介绍",
                    }
                    for d in payload["documents"]
                    if d["kind"] == "details"
                ],
            }
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [{"function": {"name": "submit_tag_result", "arguments": json.dumps(result)}}]
                        }
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        registry = Registry(s, db, client)
        engine = Engine(s, db, registry)
        tid = engine.create(
            ResearchRequest(recommendation_mode="player_tags", recommendation_count=1, query="希望构筑有变化")
        )
        await finish(engine, tid)
    report = report_for(db, tid)
    assert events[0] == "interpret" and events[-2:] == ["extract", "verify"]
    assert db.task(tid)["status"] == "completed", report["errors"]
    row = report["results"][0]
    assert row["supporting_reviews"] == 3 and row["rating"]["positive_rate"] == 0.9
    assert row["reading"]["full_read"] == row["reading"]["fetched"] == 3
    assert row["diagnosis"]["verified"] == 4
