"""Local-only RAG. FTS is a disposable index; documents and exact quote spans are authoritative."""

import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, text, delete
from .db import Document, DocumentIndex, Chunk, MemoryIndex, Task, Span
from .dna.ontology import FEATURES

CHUNK_VERSION = "cjk-bigram-alias-1:800:120"
MAX_AGE_DAYS = 730
ALLOWED_SCOPES = ("platform", "difficulty", "mode", "patch")


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def timestamp(value):
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except (AttributeError, ValueError, TypeError):
        return ""


def source_time(document):
    # Fetch time cannot make an old review fresh. An actual source edit can.
    return max(timestamp(document.get("published_at")), timestamp(document.get("updated_at")))


def tokens(value):
    value = value.lower()
    found = []
    for part in re.findall(r"[a-z0-9]+|[\u3400-\u9fff]+", value):
        if "\u3400" <= part[0] <= "\u9fff":
            found.extend(part[i : i + 2] for i in range(len(part) - 1))
            if len(part) == 1:
                found.append(part)
        else:
            found.append(part)
    # Canonical tokens connect Chinese and English mechanics without another dependency.
    for key, meta in FEATURES.items():
        aliases = [key, meta["label"], *meta.get("aliases", [])]
        if any(
            (
                a.lower() in value
                if re.search(r"[\u3400-\u9fff]", a)
                else re.search(r"(?<![a-z0-9])" + re.escape(a.lower()) + r"(?![a-z0-9])", value)
            )
            for a in aliases
        ):
            found.insert(0, "mechanic" + key.replace("_", ""))
    return list(dict.fromkeys(found))


def expression(value):
    # Never interpolate the user's FTS grammar; only quote our own tokens.
    return " OR ".join('"' + t.replace('"', '""') + '"' for t in tokens(value)[:80])


def split_ranges(value):
    start = 0
    while start < len(value):
        end = min(len(value), start + 800)
        if end < len(value):
            boundary = max(value.rfind(mark, start + 560, end) for mark in ["。", "\n", ". ", "！", "？"])
            if boundary >= 0:
                end = boundary + 1
        yield start, end
        if end == len(value):
            break
        start = end - 120


def initialize(connection):
    for table, key, fts in [("document_chunks", "id", "chunk_fts"), ("memory_index", "task_id", "memory_fts")]:
        connection.exec_driver_sql(f"CREATE VIRTUAL TABLE IF NOT EXISTS {fts} USING fts5(item_id UNINDEXED, terms)")
        connection.exec_driver_sql(
            f"CREATE TRIGGER IF NOT EXISTS {table}_ai AFTER INSERT ON {table} BEGIN INSERT INTO {fts}(item_id,terms) VALUES (new.{key},new.terms); END"
        )
        connection.exec_driver_sql(
            f"CREATE TRIGGER IF NOT EXISTS {table}_ad AFTER DELETE ON {table} BEGIN DELETE FROM {fts} WHERE item_id=old.{key}; END"
        )
        connection.exec_driver_sql(
            f"CREATE TRIGGER IF NOT EXISTS {table}_au AFTER UPDATE ON {table} BEGIN DELETE FROM {fts} WHERE item_id=old.{key}; INSERT INTO {fts}(item_id,terms) VALUES(new.{key},new.terms); END"
        )
        # Idempotent repair also covers an index removed while its source rows survived.
        connection.exec_driver_sql(
            f"INSERT INTO {fts}(item_id,terms) SELECT t.{key},t.terms FROM {table} t WHERE NOT EXISTS(SELECT 1 FROM {fts} f WHERE f.item_id=t.{key})"
        )


def source_key(d):
    return digest(
        json.dumps(
            [
                d["dataset_id"],
                d["game_id"],
                d["provider"],
                d["external_id"],
                d.get("owner_id", "local" if d.get("provenance_type") == "user" else ""),
            ],
            ensure_ascii=False,
        )
    )


def index_document(session, d, activate=True, force=False):
    state = session.get(DocumentIndex, d["id"])
    if state and (state.revoked or not state.active):
        session.execute(delete(Chunk).where(Chunk.document_id == d["id"]))
        return
    if not force and state and state.content_hash == d["content_hash"] and state.chunk_version == CHUNK_VERSION:
        return
    if not state:
        key = source_key(d)
        if activate:
            for old in session.scalars(select(DocumentIndex).where(DocumentIndex.source_key == key)):
                old.active = 0
                session.execute(delete(Chunk).where(Chunk.document_id == old.document_id))
        visibility = d.get("visibility", "owner" if d.get("provenance_type") == "user" else "public")
        state = DocumentIndex(
            document_id=d["id"],
            source_key=key,
            active=int(activate),
            revoked=0,
            content_hash=d["content_hash"],
            chunk_version=CHUNK_VERSION,
            embedding_version="none",
            visibility=visibility,
            owner_id=d.get("owner_id", "local" if visibility == "owner" else ""),
            published_at=source_time(d),
        )
        session.add(state)
    else:
        state.chunk_version = CHUNK_VERSION
        state.content_hash = d["content_hash"]
    session.execute(delete(Chunk).where(Chunk.document_id == d["id"]))
    if d.get("kind") not in ("reviews", "details", "updates", "user") or d.get("provenance_type") in (
        "report",
        "derived",
    ):
        state.active = 0
        return
    for start, end in split_ranges(d.get("text", "")):
        raw = d["text"][start:end]
        session.add(
            Chunk(
                id=digest(d["id"] + CHUNK_VERSION + str(start)),
                document_id=d["id"],
                start=start,
                end=end,
                content_hash=digest(raw),
                chunk_version=CHUNK_VERSION,
                embedding_version="none",
                terms=" ".join(tokens(raw)),
            )
        )


def usable(session, did, dataset=None, principal="local", scope=None):
    doc = session.get(Document, did)
    state = session.get(DocumentIndex, did)
    if not doc or (dataset and doc.dataset_id != dataset):
        return False
    if not state or not state.active or state.revoked or state.content_hash != doc.content_hash:
        return False
    if state.visibility != "public" and not (state.visibility == "owner" and state.owner_id == principal):
        return False
    if doc.data.get("kind") not in ("reviews", "details", "updates", "user") or doc.data.get("provenance_type") in (
        "report",
        "derived",
    ):
        return False
    if (
        state.published_at
        and doc.dataset_id != "synthetic-v1"
        and state.published_at < (datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)).isoformat()
    ):
        return False
    if scope is not None and any(scope.get(k) != v for k, v in doc.data.get("scope", {}).items()):
        return False
    return digest(doc.data.get("text", "")) == state.content_hash


class Retrieval:
    def __init__(self, db):
        self.db = db

    def rebuild(self, force=True):
        with self.db.Session.begin() as session:
            documents = sorted(session.scalars(select(Document)), key=lambda d: (d.data.get("fetched_at", ""), d.id))
            for doc in documents:
                index_document(session, doc.data, force=force)
            session.flush()
            # Rebuild copies, preserving active heads and revocation metadata.
            for table, key, fts in [("document_chunks", "id", "chunk_fts"), ("memory_index", "task_id", "memory_fts")]:
                session.execute(text(f"DELETE FROM {fts}"))
                session.execute(text(f"INSERT INTO {fts}(item_id,terms) SELECT {key},terms FROM {table}"))
        self.sync_memory()

    def revoke(self, did):
        with self.db.Session.begin() as session:
            state = session.get(DocumentIndex, did)
            if state:
                state.revoked = 1
                state.active = 0
                session.execute(delete(Chunk).where(Chunk.document_id == did))
                doc = session.get(Document, did)
                # Adapter caches are derived copies too. Remove entries containing this source version.
                from .db import Record

                for row in session.scalars(select(Record).where(Record.kind == "cache")):
                    if doc and any(
                        x.get("content_hash") == doc.content_hash
                        or (x.get("game_id") == doc.game_id and x.get("external_id") == doc.data.get("external_id"))
                        for x in row.data.get("result", row.data).get("data", {}).get("documents", [])
                    ):
                        session.delete(row)

    def search(self, query, dataset, game_id, allowed_ids=None, scope=None, principal="local", limit=12):
        if allowed_ids is not None and not allowed_ids:
            return []
        query_expr = expression(query)
        params = {
            "dataset": dataset,
            "gid": game_id,
            "principal": principal,
            "cutoff": (datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)).isoformat(),
        }
        clauses = [
            "d.dataset_id=:dataset",
            "d.game_id=:gid",
            "i.active=1",
            "i.revoked=0",
            "i.content_hash=d.content_hash",
            "(i.visibility='public' OR (i.visibility='owner' AND i.owner_id=:principal))",
            "(d.dataset_id='synthetic-v1' OR i.published_at='' OR i.published_at>=:cutoff)",
        ]
        if allowed_ids is not None:
            names = []
            for n, did in enumerate(dict.fromkeys(allowed_ids)):
                params["d" + str(n)] = did
                names.append(":d" + str(n))
            clauses.append("d.id IN (" + ",".join(names) + ")")
        # Scoped sources only enter an explicitly matching context; generic claims cannot inherit them.
        for k in ALLOWED_SCOPES:
            params["scope_" + k] = (scope or {}).get(k)
            clauses.append(
                f"(json_extract(d.data,'$.scope.{k}') IS NULL OR json_extract(d.data,'$.scope.{k}')=:scope_{k})"
            )
        if query_expr:
            params["q"] = query_expr
            clauses.append("chunk_fts MATCH :q")
        sql = (
            "SELECT c.id, c.document_id, c.start, c.end, c.content_hash"
            + (", bm25(chunk_fts) AS score" if query_expr else ", 0 AS score")
            + " FROM chunk_fts JOIN document_chunks c ON c.id=chunk_fts.item_id JOIN documents d ON d.id=c.document_id JOIN document_index i ON i.document_id=d.id WHERE "
            + " AND ".join(clauses)
            + " ORDER BY score, c.document_id, c.start LIMIT 200"
        )
        with self.db.Session() as session:
            rows = session.execute(text(sql), params).mappings().all()
            result = []
            counts = {}
            for row in rows:
                did = row["document_id"]
                if counts.get(did, 0) >= 2 or not usable(session, did, dataset, principal, scope or {}):
                    continue
                doc = session.get(Document, did).data
                raw = doc["text"][row["start"] : row["end"]]
                if digest(raw) != row["content_hash"]:
                    continue
                result.append(
                    {
                        **doc,
                        "text": raw,
                        "chunk_id": row["id"],
                        "start": row["start"],
                        "end": row["end"],
                        "chunk_version": CHUNK_VERSION,
                        "embedding_version": "none",
                        "retrieval_score": row["score"],
                        "freshness": "unknown" if not source_time(doc) else "current_window",
                    }
                )
                counts[did] = counts.get(did, 0) + 1
                if len(result) >= limit:
                    break
            return result

    def context(self, query, dataset, gid, allowed_ids=None, scope=None):
        started = time.perf_counter()
        from .dna.projection import current_features

        facts = current_features(self.db, dataset, gid, scope)
        verified = {
            k: v for k, v in facts.items() if v.get("value") is not None and v.get("evidence_status") == "supported"
        }
        hits = self.search(query, dataset, gid, allowed_ids, scope)
        # An empty lexical match means insufficient evidence, not proof of absence.
        if not hits:
            hits = self.search("", dataset, gid, allowed_ids, scope, limit=6)
        return {
            "assessments": verified,
            "documents": hits,
            "metrics": {
                "method": "sqlite_fts5",
                "chunks": len(hits),
                "characters": sum(len(h["text"]) for h in hits),
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "external_requests": 0,
                "model_calls": 0,
                "chunk_version": CHUNK_VERSION,
                "embedding_version": "none",
            },
        }

    def verification_context(self, claims):
        documents = []
        with self.db.Session() as session:
            for claim in claims:
                doc = session.get(Document, claim["document_id"])
                if not doc:
                    continue
                span = session.get(Span, claim["evidence_ids"][0])
                if not span or span.document_id != doc.id:
                    continue
                start = max(0, span.start - 400)
                end = min(len(doc.data["text"]), span.end + 400)
                documents.append(
                    {
                        **doc.data,
                        "text": doc.data["text"][start:end],
                        "start": start,
                        "end": end,
                        "for_claim_id": claim["id"],
                        "current_source": usable(session, doc.id, doc.dataset_id),
                    }
                )
        return documents

    def sync_memory(self):
        with self.db.Session.begin() as session:
            # Updates and deletions invalidate the disposable index; private text never enters fact FTS.
            indexed = {r.task_id: r for r in session.scalars(select(MemoryIndex))}
            for task in session.scalars(select(Task)):
                row = indexed.pop(task.id, None)
                query = task.request.get("query", "")
                dataset = task.context.get("dataset_id")
                if task.request.get("kind") != "scout" or not dataset:
                    if row:
                        session.delete(row)
                    continue
                h = digest(query)
                if row and row.content_hash == h and row.chunk_version == CHUNK_VERSION and row.dataset_id == dataset:
                    continue
                if row:
                    row.content_hash = h
                    row.terms = " ".join(tokens(query))
                    row.chunk_version = CHUNK_VERSION
                    row.dataset_id = dataset
                else:
                    session.add(
                        MemoryIndex(
                            task_id=task.id,
                            dataset_id=dataset,
                            content_hash=h,
                            chunk_version=CHUNK_VERSION,
                            embedding_version="none",
                            terms=" ".join(tokens(query)),
                        )
                    )
            for row in indexed.values():
                session.delete(row)

    def history(self, dataset, query, limit=10):
        self.sync_memory()
        q = expression(query)
        with self.db.Session() as session:
            recent = list(
                session.scalars(
                    select(Task)
                    .where(
                        Task.context["dataset_id"].as_string() == dataset, Task.request["kind"].as_string() == "scout"
                    )
                    .order_by(Task.created_at.desc())
                    .limit(2)
                )
            )
            ids = []
            if q:
                ids = (
                    session.execute(
                        text(
                            "SELECT m.task_id FROM memory_fts JOIN memory_index m ON m.task_id=memory_fts.item_id WHERE memory_fts MATCH :q AND m.dataset_id=:dataset ORDER BY bm25(memory_fts) LIMIT :limit"
                        ),
                        {"q": q, "dataset": dataset, "limit": limit},
                    )
                    .scalars()
                    .all()
                )
            ordered = recent + [session.get(Task, i) for i in ids]
            if not q:
                ordered = list(
                    session.scalars(
                        select(Task)
                        .where(
                            Task.context["dataset_id"].as_string() == dataset,
                            Task.request["kind"].as_string() == "scout",
                        )
                        .order_by(Task.created_at.desc())
                        .limit(limit + 2)
                    )
                )
            unique = {t.id: t for t in ordered if t}
            return [
                {"id": t.id, "kind": "input", "label": "过往探索", "text": t.request.get("query", "")[:3000]}
                for t in unique.values()
            ]
