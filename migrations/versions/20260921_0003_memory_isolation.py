"""add persona and session isolation to memory atoms

Revision ID: 20260921_0003
Revises: 20260921_0002
Create Date: 2026-09-21 00:02:00
"""

from alembic import op

revision = "20260921_0003"
down_revision = "20260921_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'memory_atoms' AND column_name = 'persona_id'
            ) THEN
                ALTER TABLE memory_atoms ADD COLUMN persona_id VARCHAR(128);
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'memory_atoms' AND column_name = 'session_id'
            ) THEN
                ALTER TABLE memory_atoms ADD COLUMN session_id VARCHAR(256);
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE tablename = 'memory_atoms' AND indexname = 'ix_memory_atoms_persona_id'
            ) THEN
                CREATE INDEX ix_memory_atoms_persona_id ON memory_atoms (persona_id);
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE tablename = 'memory_atoms' AND indexname = 'ix_memory_atoms_session_id'
            ) THEN
                CREATE INDEX ix_memory_atoms_session_id ON memory_atoms (session_id);
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.drop_index("ix_memory_atoms_session_id", table_name="memory_atoms")
    op.drop_index("ix_memory_atoms_persona_id", table_name="memory_atoms")
    op.drop_column("memory_atoms", "session_id")
    op.drop_column("memory_atoms", "persona_id")