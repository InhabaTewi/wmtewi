"""add durable inference jobs

Revision ID: 20260925_0006
Revises: 20260924_0005
Create Date: 2026-09-25 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260925_0006"
down_revision = "20260924_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inference_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("trace_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("target_worker_id", sa.String(length=128), nullable=False),
        sa.Column("model_alias", sa.String(length=128)),
        sa.Column("model_version_requirement", sa.String(length=256)),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column("result_payload", sa.JSON()),
        sa.Column("error_code", sa.String(length=64)),
        sa.Column("error_message", sa.String(length=512)),
        sa.Column("claimed_by_worker_id", sa.String(length=128)),
        sa.Column("claim_token", sa.String(length=256)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("failed_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_inference_jobs_status_target_worker_created", "inference_jobs", ["status", "target_worker_id", "created_at"])
    op.create_index("ix_inference_jobs_expires_at", "inference_jobs", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_inference_jobs_expires_at", table_name="inference_jobs")
    op.drop_index("ix_inference_jobs_status_target_worker_created", table_name="inference_jobs")
    op.drop_table("inference_jobs")