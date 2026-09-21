"""add HNSW cosine index for knowledge vector search

Revision ID: 20260922_0004
Revises: 20260921_0003
Create Date: 2026-09-22 00:04:00
"""

from alembic import op

revision = "20260922_0004"
down_revision = "20260921_0003"
branch_labels = None
depends_on = None

INDEX_NAME = "ix_knowledge_embeddings_embedding_hnsw_cosine"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS {INDEX_NAME}
        ON knowledge_embeddings
        USING hnsw (embedding vector_cosine_ops)
        """
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")
