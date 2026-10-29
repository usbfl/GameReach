import copy
import uuid
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import create_engine, event, select, String, Text, JSON, Integer, ForeignKey, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def now():
    return datetime.now(timezone.utc).isoformat()


def uid(prefix=""):
    return prefix + uuid.uuid4().hex[:16]


class Base(DeclarativeBase):
    pass


class Record(Base):
    """Versioned domain aggregates: game/external IDs, profile/revisions, features, tool health/cache."""

    __tablename__ = "records"
    kind: Mapped[str] = mapped_column(String, primary_key=True)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[str] = mapped_column(String, default=now)


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    status: Mapped[str] = mapped_column(String, index=True, default="queued")
    phase: Mapped[str] = mapped_column(String, default="interpret")
    version: Mapped[int] = mapped_column(Integer, default=1)
    request: Mapped[dict] = mapped_column(JSON)
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(String, default=now)
    updated_at: Mapped[str] = mapped_column(String, default=now)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    timestamp: Mapped[str] = mapped_column(String, default=now)
    role: Mapped[str] = mapped_column(String)
    event_type: Mapped[str] = mapped_column(String)
    summary: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    game_id: Mapped[str] = mapped_column(String, index=True)
    dataset_id: Mapped[str] = mapped_column(String, index=True)
    content_hash: Mapped[str] = mapped_column(String)
    data: Mapped[dict] = mapped_column(JSON)


class Span(Base):
    __tablename__ = "spans"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"))
    start: Mapped[int] = mapped_column(Integer)
    end: Mapped[int] = mapped_column(Integer)
    quote: Mapped[str] = mapped_column(Text)


class Claim(Base):
    """Evidence links stored in validated data; game_id/task_id are indexed columns."""

    __tablename__ = "claims"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    game_id: Mapped[str] = mapped_column(String, index=True)
    data: Mapped[dict] = mapped_column(JSON)


class Report(Base):
    __tablename__ = "reports"
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    data: Mapped[dict] = mapped_column(JSON)
    markdown: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String, default=now)


class HumanAction(Base):
    __tablename__ = "human_actions"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"))
    state_version: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String, default=now)
    __table_args__ = (UniqueConstraint("task_id", "state_version"),)


class Watch(Base):
    """Interest shortlist; legacy table name preserves existing user choices."""

    __tablename__ = "watches"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)


class DB:
    def __init__(self, url):
        if url.startswith("sqlite:///") and ":memory:" not in url:
            Path(url[10:]).parent.mkdir(parents=True, exist_ok=True)
        kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
        if ":memory:" in url:
            from sqlalchemy.pool import StaticPool

            kwargs["poolclass"] = StaticPool
        self.engine = create_engine(url, **kwargs)
        if url.startswith("sqlite"):

            @event.listens_for(self.engine, "connect")
            def pragmas(conn, record):
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=5000")

        self.Session = sessionmaker(self.engine, expire_on_commit=False)

    def create(self):
        Base.metadata.create_all(self.engine)
        from .retrieval import initialize

        with self.engine.begin() as connection:
            initialize(connection)

    def get(self, kind, key, default=None):
        with self.Session() as s:
            r = s.get(Record, (kind, key))
            return copy.deepcopy(r.data) if r else copy.deepcopy(default)

    def put(self, kind, key, data):
        with self.Session.begin() as s:
            s.merge(Record(kind=kind, id=key, data=copy.deepcopy(data), updated_at=now()))

    def list(self, kind):
        with self.Session() as s:
            return [copy.deepcopy(r.data) for r in s.scalars(select(Record).where(Record.kind == kind))]

    def task(self, task_id):
        with self.Session() as s:
            t = s.get(Task, task_id)
            if not t:
                return None
            return {
                k: copy.deepcopy(getattr(t, k))
                for k in ["id", "status", "phase", "version", "request", "context", "created_at", "updated_at"]
            }

    def event(self, task_id, role, event_type, summary, payload=None):
        with self.Session.begin() as s:
            e = Event(task_id=task_id, role=role, event_type=event_type, summary=summary, payload=payload or {})
            s.add(e)
            s.flush()
            return e.id

    def events(self, task_id, after=0, limit=500):
        with self.Session() as s:
            rows = s.scalars(
                select(Event).where(Event.task_id == task_id, Event.id > after).order_by(Event.id).limit(limit)
            )
            return [
                {k: getattr(e, k) for k in ["id", "task_id", "timestamp", "role", "event_type", "summary", "payload"]}
                for e in rows
            ]


class FeatureAssessment(Base):
    __tablename__ = "feature_assessments"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String, index=True)
    game_id: Mapped[str] = mapped_column(String, index=True)
    feature_key: Mapped[str] = mapped_column(String, index=True)
    scope_key: Mapped[str] = mapped_column(String)
    revision: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (UniqueConstraint("dataset_id", "game_id", "feature_key", "scope_key", "revision"),)


class DnaSnapshot(Base):
    __tablename__ = "dna_snapshots"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String, index=True)
    task_id: Mapped[str | None] = mapped_column(String, nullable=True)
    query_hash: Mapped[str] = mapped_column(String, index=True)
    created_at: Mapped[str] = mapped_column(String, default=now)
    data: Mapped[dict] = mapped_column(JSON)


class DocumentIndex(Base):
    """Rebuildable source eligibility; original Document versions remain immutable."""

    __tablename__ = "document_index"
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True)
    source_key: Mapped[str] = mapped_column(String, index=True)
    active: Mapped[int] = mapped_column(Integer, default=1)
    revoked: Mapped[int] = mapped_column(Integer, default=0)
    content_hash: Mapped[str] = mapped_column(String)
    chunk_version: Mapped[str] = mapped_column(String)
    embedding_version: Mapped[str] = mapped_column(String, default="none")
    visibility: Mapped[str] = mapped_column(String, default="public")
    owner_id: Mapped[str] = mapped_column(String, default="")
    published_at: Mapped[str] = mapped_column(String, default="")


class Chunk(Base):
    __tablename__ = "document_chunks"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    start: Mapped[int] = mapped_column(Integer)
    end: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String)
    chunk_version: Mapped[str] = mapped_column(String)
    embedding_version: Mapped[str] = mapped_column(String, default="none")
    terms: Mapped[str] = mapped_column(Text)


class MemoryIndex(Base):
    __tablename__ = "memory_index"
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String, index=True)
    content_hash: Mapped[str] = mapped_column(String)
    chunk_version: Mapped[str] = mapped_column(String)
    embedding_version: Mapped[str] = mapped_column(String, default="none")
    terms: Mapped[str] = mapped_column(Text)
