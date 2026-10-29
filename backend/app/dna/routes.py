from typing import Literal
from fastapi import HTTPException, Query
from pydantic import Field
from .ontology import ONTOLOGY, FEATURES, VERSION
from .query import Strict, DnaQuery, compile_query, leaves
from .projection import current_features, get_snapshot, rerank


class ParseBody(Strict):
    text: str = Field("", max_length=3000)
    preferences: list[dict] = Field(default_factory=list, max_length=16)
    discovery: dict = Field(default_factory=dict)


class RankBody(Strict):
    candidate_snapshot_id: str = Field(max_length=100)
    dataset_id: Literal["live", "synthetic-v1"]
    query: DnaQuery
    confirm_relaxation: bool = False


class ReferenceBody(Strict):
    dataset_id: Literal["live", "synthetic-v1"]
    query: DnaQuery


def add_references(db, dataset, query):
    data = query.model_dump()
    data["soft_preferences"] = [x for x in data["soft_preferences"] if x["condition"]["origin"] != "reference"]
    used = {
        leaf.feature_key
        for root in query.must
        + query.must_not
        + [s.condition for s in query.soft_preferences if s.condition.origin != "reference"]
        for leaf in leaves(root)
    }
    gaps = list(data["unparsed_fragments"])
    for gid in query.reference_game_ids:
        game = db.get("game", gid)
        if not game or game.get("dataset_id") != dataset:
            raise ValueError("reference_game_dataset_mismatch")
        fs = current_features(db, dataset, gid)
        for key in query.selected_features:
            f = fs[key]
            if f.get("value") is None or f.get("evidence_status") != "supported":
                gaps.append(f"参考游戏 {game['name']} 的{FEATURES[key]['label']}未知，未继承。")
            elif key not in used:
                data["soft_preferences"].append(
                    {
                        "condition": {
                            "feature_key": key,
                            "operator": "overlap" if FEATURES[key]["type"] == "range" else "eq",
                            "target": f["value"],
                            "origin": "reference",
                            "scope": f.get("scope", {}),
                        },
                        "weight": 1,
                    }
                )
                used.add(key)
    data["unparsed_fragments"] = list(dict.fromkeys(gaps))[:30]
    return DnaQuery.model_validate(data)


def install_routes(app, db, settings):
    @app.get("/api/dna/ontology")
    async def ontology():
        return ONTOLOGY

    @app.get("/api/dna/references")
    async def references(dataset_id: Literal["live", "synthetic-v1"], q: str = Query("", max_length=100)):
        from sqlalchemy import select
        from ..db import Record

        with db.Session() as session:
            # Local only, bounded. Does not trigger IGDB/model calls.
            games = session.scalars(select(Record).where(Record.kind == "game").limit(500)).all()
        items = []
        for row in games:
            game = row.data
            if game.get("dataset_id") != dataset_id or q.lower() not in game["name"].lower():
                continue
            fs = current_features(db, dataset_id, game["id"])
            items.append(
                {
                    "id": game["id"],
                    "name": game["name"],
                    "known_features": sum(f.get("value") is not None for f in fs.values()),
                }
            )
        return {"items": items[:30], "external_requests": 0, "model_calls": 0}

    @app.post("/api/dna/parse")
    async def parse(body: ParseBody):
        try:
            return compile_query(
                body.text, body.preferences, body.discovery, db.get("profile", "default", {}).get("preferences", [])
            ).model_dump()
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(422, {"code": "validation_error", "message": str(exc)[:500]})

    @app.get("/api/games/{gid}/dna")
    async def dna(gid: str, dataset_id: Literal["live", "synthetic-v1"], snapshot_id: str | None = None):
        if snapshot_id:
            try:
                snap = get_snapshot(db, snapshot_id, dataset_id)
            except ValueError as exc:
                raise HTTPException(409, str(exc))
            if gid not in snap["features"]:
                raise HTTPException(404, "game_not_in_snapshot")
            fs = snap["features"][gid]
        else:
            game = db.get("game", gid)
            if not game or game.get("dataset_id") != dataset_id:
                raise HTTPException(404, "game_dataset_not_found")
            fs = current_features(db, dataset_id, gid)
        return {
            "game_id": gid,
            "dataset_id": dataset_id,
            "features": fs,
            "ontology_version": VERSION,
            "external_requests": 0,
            "model_calls": 0,
        }

    @app.post("/api/dna/reference")
    async def reference(body: ReferenceBody):
        try:
            return add_references(db, body.dataset_id, body.query).model_dump()
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    @app.post("/api/dna/rerank")
    async def reorder(body: RankBody):
        try:
            return rerank(db, body.candidate_snapshot_id, body.dataset_id, body.query, body.confirm_relaxation)
        except ValueError as exc:
            raise HTTPException(
                409,
                {
                    "code": str(exc),
                    "message": {
                        "hard_relaxation_requires_confirmation": "修改会放宽必须条件，请明确确认。",
                        "scope_expansion_requires_new_exploration": "扩大平台范围需要开始新的探索。",
                    }.get(str(exc), str(exc)),
                },
            )
