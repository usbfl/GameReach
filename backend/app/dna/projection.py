import copy
import hashlib
import json
from datetime import datetime, timezone
from sqlalchemy import select, text
from ..db import FeatureAssessment, DnaSnapshot, now, uid, Claim
from .ontology import FEATURES, VERSION, RUBRIC
from .query import DnaQuery, leaves, scope_narrows, relaxes
from .ranking import rank_game, sort_rows, RANKING_VERSION
from ..retrieval import usable, source_time
from ..quality import publishable, exploration_outcome


def empty_features():
    return {
        k: {
            "value": None,
            "evidence_ids": [],
            "opposing_evidence_ids": [],
            "assessment_revision_ids": [],
            "assessment_type": "unknown",
            "evidence_status": "insufficient",
            "freshness": "unknown",
            "scope": {},
            "schema_version": 2,
        }
        for k in FEATURES
    }


def persist_assessments(db, claims, documents):
    docs = {d["id"]: d for d in documents}
    grouped = {}
    for c in claims:
        if c.get("feature_key") in FEATURES:
            d = docs.get(c["document_id"])
            if d:
                grouped.setdefault(
                    (d["dataset_id"], c["game_id"], c["feature_key"], json.dumps(c.get("scope", {}), sort_keys=True)),
                    [],
                ).append(c)
    with db.Session.begin() as session:
        # Serialize read-revision/write-revision under SQLite's writer lock.
        # Unique(dataset,game,feature,scope,revision) remains the final conflict guard.
        if db.engine.dialect.name == "sqlite":
            session.execute(text("BEGIN IMMEDIATE"))
        for (dataset, gid, key, scope_key), items in grouped.items():
            rubric_version = FEATURES[key].get("rubric_version", RUBRIC)
            old = session.scalars(
                select(FeatureAssessment)
                .where(
                    FeatureAssessment.dataset_id == dataset,
                    FeatureAssessment.game_id == gid,
                    FeatureAssessment.feature_key == key,
                    FeatureAssessment.scope_key == scope_key,
                )
                .order_by(FeatureAssessment.revision.desc())
            ).first()
            # Same semantic evidence content is not a new independent vote.
            digest = hashlib.sha256(
                json.dumps(
                    [{k: c.get(k) for k in ["id", "value", "evidence_status", "verification"]} for c in items],
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            same_rubric = old and old.data.get("rubric_version") == rubric_version
            if same_rubric and old.data.get("digest") == digest:
                continue
            evidence = list(dict.fromkeys(e for c in items for e in c["evidence_ids"]))
            supported = [c for c in items if c["evidence_status"] == "supported" and c.get("value") is not None]
            values = {json.dumps(c["value"], sort_keys=True) for c in supported}
            conflict = any(c["evidence_status"] == "disputed" for c in items) or len(values) > 1
            value = supported[0]["value"] if len(values) == 1 and not conflict else None
            status = (
                "disputed"
                if conflict
                else "supported"
                if value is not None
                else "rejected"
                if all(c["evidence_status"] == "rejected" for c in items)
                else "insufficient"
            )
            # An unrelated unsuccessful extraction is not counter-evidence to an already
            # checked fact. Retain it; changed/rejected original claims still invalidate it
            # in current_features(), and contradictory supported claims remain disputed.
            if same_rubric and old.data.get("evidence_status") == "supported" and status == "insufficient":
                old_ids = set(old.data.get("supporting_claim_ids", []))
                old_claims = [session.get(Claim, cid) for cid in old_ids]
                if (
                    old_claims
                    and all(c and usable(session, c.data["document_id"], dataset) for c in old_claims)
                    and not old_ids.intersection(c["id"] for c in items)
                ):
                    continue
            # A different task's opposing supported fact in the same scope remains a conflict,
            # unless that older claim was explicitly rejected. Newness alone is not proof.
            if same_rubric and old.data.get("evidence_status") in ["supported", "disputed"] and value is not None:
                old_claims = [session.get(Claim, cid) for cid in old.data.get("supporting_claim_ids", [])]
                active = [
                    c.data
                    for c in old_claims
                    if c
                    and c.data.get("evidence_status") == "supported"
                    and usable(session, c.data["document_id"], dataset)
                ]
                if any(c.get("value") != value for c in active):
                    conflict = True
                    status = "disputed"
                    value = None
                    evidence = list(dict.fromkeys(evidence + old.data.get("evidence_ids", [])))
            sources = list({docs[c["document_id"]]["content_hash"]: docs[c["document_id"]] for c in items}.values())
            fresh = "fresh" if all(d.get("provenance_type") == "synthetic" for d in sources) else "unknown"
            published = [source_time(d) for d in sources if source_time(d)]
            if published and all(
                (datetime.now(timezone.utc) - datetime.fromisoformat(t)).days > 730 for t in published
            ):
                fresh = "stale"
            revision = (old.revision + 1) if old else 1
            aid = uid("dna-a-")
            data = {
                "id": aid,
                "dataset_id": dataset,
                "canonical_game_id": gid,
                "feature_key": key,
                "value": value,
                "evidence_status": status,
                "assessment_type": "interpretation"
                if any(c.get("assessment_type") == "interpretation" for c in items)
                else "fact",
                "freshness": fresh,
                "scope": json.loads(scope_key),
                "supporting_claim_ids": [c["id"] for c in supported],
                "opposing_claim_ids": [c["id"] for c in items if c["evidence_status"] in ["rejected", "disputed"]],
                "evidence_ids": evidence,
                "opposing_evidence_ids": list(dict.fromkeys(e for c in items for e in c.get("oppose", []))),
                "published_at": published,
                "fetched_at": [d.get("fetched_at") for d in sources],
                "assessed_at": now(),
                "ontology_version": VERSION,
                "rubric_version": rubric_version,
                "extractor_version": "gamereach-dna-2",
                "revision": revision,
                "supersedes": old.id if old else None,
                "digest": digest,
                "schema_version": 2,
            }
            session.add(
                FeatureAssessment(
                    id=aid,
                    dataset_id=dataset,
                    game_id=gid,
                    feature_key=key,
                    scope_key=scope_key,
                    revision=revision,
                    data=data,
                )
            )


def current_features(db, dataset, gid, scope=None):
    features = empty_features()
    scope_key = json.dumps(scope or {}, sort_keys=True)
    with db.Session() as s:
        rows = s.scalars(
            select(FeatureAssessment)
            .where(FeatureAssessment.dataset_id == dataset, FeatureAssessment.game_id == gid)
            .order_by(FeatureAssessment.revision.desc())
        ).all()
        seen = set()
        variants = {}
        for row in rows:
            d = row.data
            k = row.feature_key
            token = (k, row.scope_key)
            if (token in seen or d.get("ontology_version") != VERSION
                or d.get("rubric_version") != FEATURES.get(k, {}).get("rubric_version", RUBRIC)):
                continue
            seen.add(token)
            f = copy.deepcopy(d)
            if f["evidence_status"] == "supported":
                supports = [s.get(Claim, cid) for cid in f.get("supporting_claim_ids", [])]
                if not supports or any(
                    not c
                    or c.data.get("evidence_status") != "supported"
                    or not usable(s, c.data["document_id"], dataset)
                    for c in supports
                ):
                    f["evidence_status"] = "insufficient"
                    f["invalidation_reason"] = "supporting_claim_or_source_changed"
            if f["evidence_status"] != "supported" or f["freshness"] == "stale":
                f["value"] = None
            f["assessment_revision_ids"] = [row.id]
            variants.setdefault(k, {})[row.scope_key] = f
            if row.scope_key == scope_key:
                features[k] = copy.deepcopy(f)
        for key, values in variants.items():
            features[key]["scope_variants"] = values
    return features


def make_snapshot(db, dataset, games, query, constraints, task_id=None, threshold=0.6):
    features = {g["id"]: current_features(db, dataset, g["id"]) for g in games}
    rows = sort_rows([rank_game(g, features[g["id"]], query, constraints, threshold) for g in games])
    from ..reading import reader_card

    for row in rows:
        row["reading"] = reader_card(db, task_id, row)
    policy = db.task(task_id)["context"].get("quality_policy") if task_id else None
    publishable(rows, policy)
    sid = uid("dna-s-")
    data = {
        "id": sid,
        "dataset_id": dataset,
        "games": copy.deepcopy(games),
        "features": features,
        "constraints": constraints,
        "query": query.model_dump(),
        "query_hash": query.query_hash,
        "scope": query.discovery_scope.model_dump(),
        "ontology_version": VERSION,
        "rubric_version": RUBRIC,
        "ranking_version": RANKING_VERSION,
        "threshold": threshold,
        "quality_policy": policy,
        "rows": rows,
        "created_at": now(),
        "assessment_revision_ids": list(
            dict.fromkeys(
                a
                for fs in features.values()
                for f in fs.values()
                for variant in [f] + list(f.get("scope_variants", {}).values())
                for a in variant.get("assessment_revision_ids", [])
            )
        ),
    }
    with db.Session.begin() as s:
        s.add(DnaSnapshot(id=sid, dataset_id=dataset, task_id=task_id, query_hash=query.query_hash, data=data))
    return data


def get_snapshot(db, sid, dataset):
    with db.Session() as s:
        row = s.get(DnaSnapshot, sid)
        if not row:
            raise ValueError("snapshot_not_found")
        if row.dataset_id != dataset:
            raise ValueError("dataset_mismatch")
        if row.data["ontology_version"] != VERSION:
            raise ValueError("ontology_version_mismatch")
        return copy.deepcopy(row.data)


def rerank(db, sid, dataset, query, confirm_relaxation=False):
    snap = get_snapshot(db, sid, dataset)
    if snap.get("rubric_version") not in (None, RUBRIC):
        raise ValueError("玩法解释量表已更新；旧报告保留为历史，请重新探索后再调整排序。")
    # A frozen report remains an archive. It cannot supply current facts after source withdrawal.
    with db.Session() as session:
        for row in snap["rows"]:
            for did in row.get("reading", {}).get("citation_document_ids", []):
                if not usable(session, did, dataset):
                    raise ValueError("介绍引用的来源已更新或失效，请重新探索。")
        for features in snap["features"].values():
            for feature in features.values():
                for variant in [feature] + list(feature.get("scope_variants", {}).values()):
                    for cid in variant.get("supporting_claim_ids", []):
                        claim = session.get(Claim, cid)
                        if (
                            not claim
                            or claim.data.get("evidence_status") != "supported"
                            or not usable(session, claim.data["document_id"], dataset)
                        ):
                            raise ValueError("来源已更新或失效；此报告保留为历史快照，请重新探索后再调整排序。")
    old = DnaQuery.model_validate(snap["query"])
    if not scope_narrows(snap["scope"], query.discovery_scope.model_dump()):
        raise ValueError("scope_expansion_requires_new_exploration")
    if relaxes(old, query) and not confirm_relaxation:
        raise ValueError("hard_relaxation_requires_confirmation")
    from ..discovery import scope_constraints

    constraints = scope_constraints(snap["constraints"], query.discovery_scope.model_dump())
    rows = []
    for game in snap["games"]:
        # Missing store data must not sneak into a requested known store subset.
        if (
            query.discovery_scope.stores
            and not set(query.discovery_scope.stores).intersection(game.get("store_links", {}))
            and not (dataset == "synthetic-v1" and query.discovery_scope.stores == snap["scope"]["stores"])
        ):
            continue
        if query.discovery_scope.platforms:
            from ..discovery import PLATFORMS

            if not {PLATFORMS[k][1] for k in query.discovery_scope.platforms}.intersection(game.get("platforms") or []):
                continue
        rows.append(rank_game(game, snap["features"][game["id"]], query, constraints, snap["threshold"]))
        original = next((r for r in snap["rows"] if r["game"]["id"] == game["id"]), {})
        if "reading" in original:
            rows[-1]["reading"] = copy.deepcopy(original["reading"])
    rows = sort_rows(rows)
    candidate_count = len(rows)
    rows, withheld = publishable(rows, snap.get("quality_policy"))
    positions = {r["game"]["id"]: i + 1 for i, r in enumerate(snap["rows"])}
    return {
        "candidate_snapshot_id": sid,
        "dataset_id": dataset,
        "query": query.model_dump(),
        "query_hash": query.query_hash,
        "results": rows,
        "withheld_candidates": withheld,
        "outcome": exploration_outcome(rows, withheld, [], snap.get("quality_policy") or {}),
        "candidate_count": candidate_count,
        "quality_policy": snap.get("quality_policy"),
        "changes": [
            {
                "game_id": r["game"]["id"],
                "old_rank": positions[r["game"]["id"]],
                "new_rank": i + 1,
                "reason": "仅按修改后的条件重排；游戏事实与候选快照未改变。",
            }
            for i, r in enumerate(rows)
        ],
        "external_requests": 0,
        "model_calls": 0,
        "assessment_revision_ids": snap["assessment_revision_ids"],
    }


def local_recall(db, dataset, query, scope, max_candidates=20):
    from ..ranking import hard_filter
    from ..discovery import scope_constraints

    keys = {
        p.feature_key
        for root in query.must + query.must_not + [s.condition for s in query.soft_preferences]
        for p in leaves(root)
    }
    if not keys:
        return []
    with db.Session() as s:
        gids = list(
            dict.fromkeys(
                s.scalars(
                    select(FeatureAssessment.game_id).where(
                        FeatureAssessment.dataset_id == dataset, FeatureAssessment.feature_key.in_(keys)
                    )
                ).all()
            )
        )
    found = []
    for gid in gids[:200]:
        game = db.get("game", gid)
        if not game or game.get("dataset_id") != dataset:
            continue
        if scope.get("stores") and not set(scope["stores"]).intersection(game.get("store_links", {})):
            continue
        state, _ = hard_filter(game, scope_constraints({"released_only": False}, scope))
        if state == "fail":
            continue
        fs = current_features(db, dataset, gid)
        if not any(fs[k].get("value") is not None for k in keys):
            continue
        found.append({**game, "recall_reasons": ["已有经核验的机制资料"], "source_provider": "local_dna"})
    return found[:max_candidates]
