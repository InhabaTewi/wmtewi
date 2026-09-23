"""add worker registry metadata

Revision ID: 20260924_0005
Revises: 20260922_0004
Create Date: 2026-09-24 00:05:00
"""

from alembic import op

revision = "20260924_0005"
down_revision = "20260922_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'worker_nodes' AND column_name = 'worker_id'
            ) THEN
                ALTER TABLE worker_nodes ADD COLUMN worker_id VARCHAR(128);
            END IF;
            UPDATE worker_nodes SET worker_id = node_name WHERE worker_id IS NULL;
            ALTER TABLE worker_nodes ALTER COLUMN worker_id SET NOT NULL;
            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE tablename = 'worker_nodes' AND indexname = 'ix_worker_nodes_worker_id'
            ) THEN
                CREATE UNIQUE INDEX ix_worker_nodes_worker_id ON worker_nodes (worker_id);
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'worker_nodes' AND column_name = 'last_seen_at'
            ) THEN
                ALTER TABLE worker_nodes ADD COLUMN last_seen_at TIMESTAMP WITH TIME ZONE;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'worker_nodes' AND column_name = 'gpu_name'
            ) THEN
                ALTER TABLE worker_nodes ADD COLUMN gpu_name VARCHAR(256);
                ALTER TABLE worker_nodes ADD COLUMN gpu_count INTEGER;
                ALTER TABLE worker_nodes ADD COLUMN vram_total_mb INTEGER;
                ALTER TABLE worker_nodes ADD COLUMN vram_used_mb INTEGER;
                ALTER TABLE worker_nodes ADD COLUMN vram_free_mb INTEGER;
                ALTER TABLE worker_nodes ADD COLUMN loaded_model VARCHAR(256);
                ALTER TABLE worker_nodes ADD COLUMN model_version VARCHAR(256);
                ALTER TABLE worker_nodes ADD COLUMN model_alias VARCHAR(128);
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_worker_nodes_worker_id")
    op.drop_column("worker_nodes", "model_alias")
    op.drop_column("worker_nodes", "model_version")
    op.drop_column("worker_nodes", "loaded_model")
    op.drop_column("worker_nodes", "vram_free_mb")
    op.drop_column("worker_nodes", "vram_used_mb")
    op.drop_column("worker_nodes", "vram_total_mb")
    op.drop_column("worker_nodes", "gpu_count")
    op.drop_column("worker_nodes", "gpu_name")
    op.drop_column("worker_nodes", "last_seen_at")
    op.drop_column("worker_nodes", "worker_id")