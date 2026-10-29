"""Add disposable retrieval indexes without rewriting documents, quotes or user history."""
from alembic import op
from app.db import DocumentIndex, Chunk, MemoryIndex
from app.retrieval import initialize

revision = '0003'
down_revision = '0002'


def upgrade():
    for model in (DocumentIndex, Chunk, MemoryIndex):
        model.__table__.create(op.get_bind(), checkfirst=True)
    initialize(op.get_bind())


def downgrade():
    for table in ('chunk_fts', 'memory_fts'):
        op.execute('DROP TABLE IF EXISTS ' + table)
    for model in (MemoryIndex, Chunk, DocumentIndex):
        model.__table__.drop(op.get_bind(), checkfirst=True)
