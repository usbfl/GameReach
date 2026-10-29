"""Regression coverage for the uploaded task's failure modes; all HTTP here is synthetic."""

import copy
import json
import httpx
import pytest
from sqlalchemy import select
from pydantic import SecretStr
from app.db import Report
from app.tools import Gate, HTTP, SourceFailure, IGDBSource
from app.schemas import ResearchRequest
from app.tags.llm import TagLLM, parse_result
from app.tags.models import Extraction
from app.tags.catalog import catalog_priority, recall_branches
from app.tags.workflow import batches
from conftest import finish


def finding(**extra):
    return (
        dict(
            game_id="g",
            document_id="d",
            aspect="tag",
            tag_id="build_variety",
            quote="whole quote",
            claim_text="Authored finding",
            scope={},
        )
        | extra
    )


def test_partial_findings_preserve_valid_evidence_without_relaxing_scope_or_tag_rules():
    raw = {"summary": "Authored", "findings": [finding(), finding(aspect="strength"), finding(scope={"invented": "x"})]}
    result, errors = parse_result("extract", Extraction, json.dumps(raw))
    assert len(result.findings) == 1 and len(errors) == 2
    assert errors[0]["path"] == ["findings", 1]
    assert "其他主张不填" in errors[0]["rule"]
    assert errors[1]["rule"] == "无效作用域"


def test_overproduced_claims_do_not_erase_whole_batch_or_gameplay_intro():
    raw = {
        "summary": "Authored",
        "findings": [finding() for _ in range(18)] + [finding(aspect="core_loop", tag_id=None)],
    }
    result, errors = parse_result("extract", Extraction, json.dumps(raw))
    assert len(result.findings) == 16 and result.findings[0].aspect == "core_loop"
    assert errors[0]["omitted"] == 3


@pytest.mark.asyncio
async def test_repair_reports_specific_rule_and_switches_encoding_once(stack):
    s, _, _, _ = stack
    s.llm_mode = "live"
    s.llm_base_url = "https://model.example.test/v1"
    s.llm_model = "synthetic"
    s.llm_api_key = SecretStr("synthetic")
    requests = []

    async def handler(req):
        body = json.loads(req.content)
        requests.append(body)
        raw = {
            "summary": "Authored",
            "findings": [finding(aspect="strength", tag_id="build_variety" if len(requests) == 1 else None)],
        }
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "tool_calls": [
                                    {"function": {"name": "submit_tag_result", "arguments": json.dumps(raw)}}
                                ]
                            }
                        }
                    ]
                },
            )
        assert "其他主张不填" in body["messages"][-1]["content"]
        assert "tools" not in body and body["response_format"]["type"] == "json_object"
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(raw)}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        gate = Gate(s)
        r = await TagLLM(s, HTTP(s, c)).run("extract", {"intents": [], "documents": []}, gate)
    assert len(r.findings) == 1 and len(requests) == 2
    assert gate.ctx["model_protocol_errors"][0]["recovered"] is True
    assert gate.ctx["model_protocol_errors"][0]["fields"][0]["rule"].endswith("其他主张不填 tag_id")


@pytest.mark.asyncio
async def test_read_timeout_is_distinct_and_never_logs_exception_secret(stack):
    s, _, _, _ = stack

    async def handler(req):
        raise httpx.ReadTimeout("private-secret-in-error")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        gate = Gate(s)
        with pytest.raises(SourceFailure, match="read_timeout"):
            await HTTP(s, c).request(gate, "GET", "https://test.invalid", attempts=0)
    assert "private-secret" not in json.dumps(gate.ctx)


def test_current_request_outweighs_many_attachment_terms():
    main = {"tag_id": "build_variety", "priority": 3, "polarity": "want", "source_id": "current"}
    extras = [
        {"tag_id": k, "priority": 1, "polarity": "want", "source_id": "card"}
        for k in ["exploration_discovery", "roguelite", "knowledge_progression"]
    ]
    good = {"tags": [], "catalog_keywords": ["character customization"]}
    broad = {"tags": [], "catalog_keywords": ["exploration", "roguelite", "puzzle"]}
    assert catalog_priority(good, [main] + extras) > catalog_priority(broad, [main] + extras)
    branches = recall_branches([main] + extras, {})
    assert not any("exploration" in x["arguments"]["tag_keywords"] for x in branches)


@pytest.mark.asyncio
async def test_paginated_catalog_no_longer_truncates_at_20(stack):
    s, _, _, _ = stack
    s.igdb_client_id = "synthetic"
    s.igdb_client_secret = SecretStr("synthetic")
    source = IGDBSource(s, None)
    source.token = "synthetic"
    source.expires = 10**20

    class Transport:
        async def request(self, gate, method, url, **kw):
            assert "offset 100;" in kw["content"] and "limit 50;" in kw["content"]
            return [{"id": i, "name": f"Authored {i}"} for i in range(50)]

    source.http = Transport()
    r = await source.call("search_games", {"tag_recall": True, "catalog_offset": 100, "catalog_page_size": 50}, Gate(s))
    assert len(r["games"]) == 50 and r["search_info"]["candidates_scanned"] == 50
    assert r["search_info"]["exhausted"] is False


def test_whole_text_batch_includes_intro_and_keeps_long_reviews_deferred():
    docs = [
        dict(id="r1", game_id="g", kind="reviews", complete=True, provenance_type="live", text="x" * 3000),
        dict(id="r2", game_id="g", kind="reviews", complete=True, provenance_type="live", text="y" * 17000),
        dict(id="d", game_id="g", kind="details", provenance_type="live", text="Whole catalog introduction."),
    ]
    assert batches(docs, 4, 16000, []) == [["r1", "d"]]
    assert len(docs[1]["text"]) == 17000


@pytest.mark.asyncio
async def test_target_one_stops_only_after_complete_verified_card(stack):
    s, db, _, e = stack
    tid = e.create(
        ResearchRequest(
            recommendation_mode="player_tags", query="构筑", recommendation_count=1, game_ids=["fx-01", "fx-02"]
        )
    )
    await finish(e, tid)
    with db.Session() as session:
        r = session.scalar(select(Report).where(Report.task_id == tid)).data
    assert r["target"]["met"] and len(r["recommendation_ids"]) == 1
    assert r["diagnostics"]["with_reviews"] == 1
    row = next(x for x in r["results"] if x["game"]["id"] == r["recommendation_ids"][0])
    assert row["card"]["introduction"] and row["card"]["strengths"]
    assert all(c["evidence_status"] == "supported" for c in row["card"]["introduction"])


def test_overlong_verdicts_cannot_hide_a_conflicting_duplicate():
    from pydantic import ValidationError
    from app.tags.models import Verification
    verdicts = [{"claim_id": str(i), "verdict": "supported", "reason": "Authored", "evidence_ids": ["e"]} for i in range(16)]
    verdicts.append(dict(verdicts[0], verdict="rejected"))
    with pytest.raises(ValidationError):
        parse_result("verify", Verification, json.dumps({"summary": "Authored", "verdicts": verdicts}))
