"""version knowledge documents and convert embeddings to pgvector

Revision ID: 20260921_0002
Revises: 20260921_0001
Create Date: 2026-09-21 00:01:00
"""

from alembic import op

revision = "20260921_0002"
down_revision = "20260921_0001"
branch_labels = None
depends_on = None

HISTORICAL_EMBEDDING_DIMENSION = 1536


def upgrade() -> None:
    op.execute(
        f"""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'knowledge_documents_source_uri_key'
            ) THEN
                ALTER TABLE knowledge_documents DROP CONSTRAINT knowledge_documents_source_uri_key;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'uq_knowledge_document_version'
            ) THEN
                ALTER TABLE knowledge_documents
                    ADD CONSTRAINT uq_knowledge_document_version UNIQUE (source_uri, version);
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'knowledge_documents' AND column_name = 'is_active'
            ) THEN
                ALTER TABLE knowledge_documents
                    ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT TRUE;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE tablename = 'knowledge_documents' AND indexname = 'ix_knowledge_documents_is_active'
            ) THEN
                CREATE INDEX ix_knowledge_documents_is_active ON knowledge_documents (is_active);
            END IF;
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'knowledge_embeddings'
                  AND column_name = 'embedding'
                  AND udt_name <> 'vector'
            ) THEN
                ALTER TABLE knowledge_embeddings
                    ALTER COLUMN embedding TYPE vector({HISTORICAL_EMBEDDING_DIMENSION}) USING embedding::text::vector;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE knowledge_embeddings ALTER COLUMN embedding TYPE json USING to_json(embedding)")
    op.drop_index("ix_knowledge_documents_is_active", table_name="knowledge_documents")
    op.drop_column("knowledge_documents", "is_active")
    op.drop_constraint("uq_knowledge_document_version", "knowledge_documents", type_="unique")
    op.create_unique_constraint("knowledge_documents_source_uri_key", "knowledge_documents", ["source_uri"])