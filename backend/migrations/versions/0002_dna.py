"""Add audit DNA revisions and frozen candidate snapshots, preserve all legacy rows."""

from alembic import op
from app.db import FeatureAssessment, DnaSnapshot

revision = "0002"
down_revision = "0001"


def upgrade():
    FeatureAssessment.__table__.create(op.get_bind(), checkfirst=True)
    DnaSnapshot.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    # Explicit downgrade drops only DNA materializations; backup before running.
    DnaSnapshot.__table__.drop(op.get_bind(), checkfirst=True)
    FeatureAssessment.__table__.drop(op.get_bind(), checkfirst=True)
