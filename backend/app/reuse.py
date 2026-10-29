"""Bounded local reuse; failures and unverified text never become reusable evidence."""

import hashlib
import json
import time
from sqlalchemy import select
from .db import Record
from .retrieval import usable


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def identity(settings):
    return [
        settings.data_mode,
        settings.llm_mode,
        settings.llm_model,
        digest(settings.llm_base_url),
        settings.llm_use_tools,
        settings.enable_verifier,
        settings.reuse_ttl_seconds,
    ]


def get(db, kind, key):
    row = db.get(kind, key)
    return row if row and row["expires"] > time.time() else None


def put(db, kind, key, value, ttl):
    db.put(kind, key, value | {"expires": time.time() + ttl})
    # Each namespace is bounded; old tasks retain their own immutable evidence.
    with db.Session.begin() as session:
        rows = list(session.scalars(select(Record).where(Record.kind == kind).order_by(Record.updated_at.desc())))
        for n, row in enumerate(rows):
            if n >= 200 or row.data.get("expires", 0) <= time.time():
                session.delete(row)


def batch_key(settings, ctx, documents):
    from .tags.llm import PROTOCOL
    from .tags.catalog import VERSION

    # Weight-only changes reuse valid evidence. Hard constraints, scopes and new focuses invalidate.
    intents = [
        {k: v for k, v in i.items() if k not in {"priority", "confirmed_by_user"}}
        for i in ctx["interpretation"]["intents"]
    ]
    intents.sort(key=lambda i: json.dumps(i, sort_keys=True))
    return digest(
        [
            "verified-batch-2",
            PROTOCOL,
            VERSION,
            identity(settings),
            ctx["dataset_id"],
            intents,
            ctx.get("constraints", {}),
            ctx.get("custom_requirements", []),
            [
                {
                    k: d.get(k)
                    for k in (
                        "id",
                        "content_hash",
                        "scope",
                        "complete",
                        "kind",
                        "author_key",
                        "sampling_stratum",
                        "visibility",
                        "owner_id",
                        "updated_at",
                    )
                }
                for d in documents
            ],
        ]
    )


def load_batch(engine, tid, ctx, docs):
    if not engine.s.reuse_enabled or not engine.s.enable_verifier:
        return None
    row = get(engine.db, "verified_batch_cache", batch_key(engine.s, ctx, docs))
    if not row:
        return None
    with engine.db.Session() as session:
        if not all(usable(session, d["id"], ctx["dataset_id"]) for d in docs):
            return None
    ids = [d["id"] for d in docs]
    accepted, rejected = engine.knowledge.extract(tid, row["findings"], ids, ctx["dataset_id"])
    if rejected or len(accepted) != len(row["findings"]):
        return None
    for cid, decision in zip(accepted, row["verdicts"]):
        ctx["verdicts"].append(decision | {"claim_id": cid})
    engine.knowledge.verify(tid, verdicts=ctx["verdicts"])
    savings = ctx.setdefault("savings", {})
    savings["verified_batches_reused"] = savings.get("verified_batches_reused", 0) + 1
    savings["reused_documents"] = savings.get("reused_documents", 0) + sum(d["kind"] == "reviews" for d in docs)
    return {"claim_ids": accepted, "origin_task_id": row["origin_task_id"]}


def save_batch(engine, tid, ctx, ids, dids):
    if not engine.s.reuse_enabled or not engine.s.enable_verifier:
        return
    claims = {c["id"]: c for c in engine.knowledge.claims(tid)}
    ordered = [claims[cid] for cid in ids if cid in claims]
    if (
        not ordered
        or len(ordered) != len(ids)
        or any(c["evidence_status"] not in {"supported", "rejected"} for c in ordered)
    ):
        return
    docs = engine.knowledge.documents(dids)
    with engine.db.Session() as session:
        if len(docs) != len(dids) or not all(usable(session, d["id"], ctx["dataset_id"]) for d in docs):
            return
    fields = (
        "game_id",
        "document_id",
        "aspect",
        "tag_id",
        "tag_polarity",
        "claim_text",
        "quote",
        "scope",
        "feature_key",
    )
    findings = [{k: c.get(k) for k in fields} for c in ordered]
    verdicts = [{k: c["verification"][k] for k in ("verdict", "reason", "evidence_ids")} for c in ordered]
    put(
        engine.db,
        "verified_batch_cache",
        batch_key(engine.s, ctx, docs),
        {
            "origin_task_id": tid,
            "findings": findings,
            "verdicts": verdicts,
        },
        engine.s.reuse_ttl_seconds,
    )
