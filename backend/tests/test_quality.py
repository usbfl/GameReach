"""Regression contracts for complete, traceable recommendations (all sources synthetic)."""

import json
import pytest
import respx
from pydantic import SecretStr
from app.quality import assess_card, quality_policy
from app.config import Settings
from app.tools import HTTP, Gate, SteamSource, SteamStoreSource
from app.schemas import ResearchRequest, Finding
from app.agents import MockLLM, extract_synthetic_documents
from app.db import Report
from app.dna.projection import current_features, rerank
from app.dna.query import DnaQuery
from conftest import finish


def complete_row():
    return {
        "game": {"source_url": "https://www.igdb.com/games/example"},
        "hard_status": "pass",
        "coverage": 1,
        "fit_known": 80,
        "reading": {
            "intro": "A sourced loop",
            "intro_evidence_ids": ["e"],
            "strengths": [{"text": "good"}],
            "tradeoffs": [{"text": "cost"}],
            "citations_have_links": True,
            "source_providers": ["steam_reviews", "steam_store"],
            "diagnosis": {"reviews_read": 20, "cited_reviews": 3, "known_mechanics": 3},
        },
    }


@pytest.mark.parametrize(
    "path,value,code",
    [
        (("hard_status",), "unknown", "constraints"),
        (("hard_status",), "fail", "constraints"),
        (("coverage",), 0.99, "preference_unknown"),
        (("fit_known",), 0, "low_fit"),
        (("game", "source_url"), None, "source_link"),
        (("game", "source_url"), "javascript:alert(1)", "source_link"),
        (("reading", "strengths"), [], "strengths"),
        (("reading", "tradeoffs"), [], "tradeoffs"),
        (("reading", "intro_evidence_ids"), [], "intro"),
        (("reading", "diagnosis", "reviews_read"), 19, "reviews"),
        (("reading", "diagnosis", "cited_reviews"), 2, "citations"),
        (("reading", "diagnosis", "known_mechanics"), 0, "mechanics"),
        (("reading", "citations_have_links"), False, "evidence_link"),
        (("reading", "source_providers"), ["steam_reviews"], "source_diversity"),
    ],
)
def test_incomplete_or_unmatched_cards_never_publish(path, value, code):
    policy = quality_policy(Settings(_env_file=None), "live")
    row = complete_row()
    assert assess_card(row, policy)["eligible"]
    target = row
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    result = assess_card(row, policy)
    assert not result["eligible"] and code in [x["code"] for x in result["reasons"]]


async def test_balanced_review_cap_pagination_dedup_and_public_links(stack):
    settings, db, _, _ = stack
    settings.max_reviews_per_game = 240
    settings.steam_web_api_key = SecretStr("never-send-personal-key")
    db.put("game", "igdb-1", {"external_ids": {"steam": "123"}})
    http = HTTP(settings)
    requests = []

    def respond(request):
        assert "key" not in request.url.params
        params = json.loads(request.url.params["input_json"])
        requests.append(params)
        n = params["num_per_page"]
        assert n <= 100
        return __import__("httpx").Response(
            200,
            json={
                "response": {
                    "reviews": [
                        {
                            "recommendationid": f"{params['review_type']}-{i}",
                            "review": f"An authored review {i}",
                            "author": {"steamid": "76561190000000000"},
                            "voted_up": params["review_type"] != 2,
                        }
                        for i in range(n)
                    ],
                    "cursor": "next",
                }
            },
        )

    with respx.mock() as mock:
        mock.get("https://api.steampowered.com/IUserReviewsService/GetAppReviews/v1/").mock(side_effect=respond)
        result = await SteamSource(settings, http, db, True).call("get_reviews", {"game_id": "igdb-1"}, Gate(settings))
    assert len(result["documents"]) == 240
    assert [p["review_type"] for p in requests] == [0, 1, 2]
    assert result["documents"][0]["url"].endswith("/recommended/123/")
    assert result["sampling"]["representative"] is False
    await http.client.aclose()


async def test_store_identity_and_release_do_not_use_dates_alone(stack):
    s, _, _, _ = stack
    http = HTTP(s)
    game = {"id": "igdb-1", "name": "Authored", "released": None, "external_ids": {"steam": "123"}}
    with respx.mock() as mock:
        route = mock.get("https://store.steampowered.com/api/appdetails")
        route.respond(
            200,
            json={
                "123": {
                    "success": True,
                    "data": {
                        "steam_appid": 123,
                        "release_date": {"coming_soon": False},
                        "genres": [{"id": "37"}],
                        "short_description": "A game",
                    },
                }
            },
        )
        doc = await SteamStoreSource(s, http).enrich(game, Gate(s))
        assert game["released"] is False and doc["provider"] == "steam_store"
        assert game["source_url"] == "https://store.steampowered.com/app/123/"
        assert game["source_identity_verified_by"] == "steam_store_appid"
        route.respond(200, json={"123": {"success": True, "data": {"steam_appid": 999}}})
        with pytest.raises(Exception, match="identity_unconfirmed"):
            await SteamStoreSource(s, http).enrich(game, Gate(s))
    await http.client.aclose()


async def test_sequential_budget_keeps_completed_card_and_hides_unread_candidate(stack):
    s, db, _, engine = stack
    s.llm_mode = "live"
    s.llm_base_url = "https://contract.invalid"
    s.llm_model = "contract"
    s.llm_api_key = SecretStr("contract")
    s.max_llm_calls = 3

    class Model(MockLLM):
        async def run(self, role, context, gate):
            if role == "researcher":
                gate.use("llm_calls")
                result = extract_synthetic_documents(context)
                result.findings = result.findings[:12]
                return result
            return await super().run(role, context, gate)

    engine.llm = Model()
    tid = engine.create(ResearchRequest(game_ids=["fx-01", "fx-02"], query="构筑"))
    task = await finish(engine, tid)
    assert task["status"] == "partial"
    with db.Session() as session:
        report = session.get(Report, (tid, 1)).data
    assert [r["game"]["id"] for r in report["results"]] == ["fx-01"]
    assert report["withheld_candidates"][0]["game_id"] == "fx-02"
    query = DnaQuery.model_validate(report["dna_query"])
    output = rerank(db, report["candidate_snapshot_id"], "synthetic-v1", query)
    assert [r["game"]["id"] for r in output["results"]] == ["fx-01"]
    assert all(r["qualification"]["eligible"] for r in output["results"])


def test_default_mode_is_normalized_but_special_mode_stays_scoped(stack):
    _, db, registry, engine = stack
    tid = engine.create(ResearchRequest(game_ids=["fx-01"], query="弹反"))
    doc = next(d for d in registry.fixture.docs if d["game_id"] == "fx-01" and "（parry）=true" in d["text"])
    did = engine.knowledge.ingest([doc])[0]
    for mode, key in [("标准爬塔", "parry"), ("挑战模式", "dodge")]:
        engine.knowledge.extract(
            tid,
            [
                Finding(
                    game_id="fx-01",
                    document_id=did,
                    feature_key=key,
                    value=True,
                    quote=doc["text"][:100],
                    claim_text=key,
                    scope={"mode": mode},
                )
            ],
            [did],
            "synthetic-v1",
        )
    claims = engine.knowledge.claims(tid)
    engine.knowledge.verify(
        tid,
        verdicts=[
            {"claim_id": c["id"], "verdict": "supported", "reason": "contract", "evidence_ids": c["evidence_ids"]}
            for c in claims
        ],
    )
    fs = current_features(db, "synthetic-v1", "fx-01")
    assert fs["parry"]["value"] is True
    assert fs["dodge"]["value"] is None
    assert current_features(db, "synthetic-v1", "fx-01", {"mode": "挑战模式"})["dodge"]["value"] is True
