"""create initial core schema

Revision ID: 20260921_0001
Revises:
Create Date: 2026-09-21 00:00:00
"""

from alembic import op

from packages.persistence.models import Base

revision = "20260921_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
