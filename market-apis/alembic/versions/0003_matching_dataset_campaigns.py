"""matching dataset campaigns

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-27
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "matching_dataset_campaigns",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("target_train_count", sa.Integer(), nullable=False),
        sa.Column("target_test_count", sa.Integer(), nullable=False),
        sa.Column("queries", sa.JSON(), nullable=False),
        sa.Column("query_categories", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("name"),
    )
    op.create_table(
        "matching_dataset_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("campaign_name", sa.String(length=120), nullable=False),
        sa.Column("candidate_id", sa.Integer(), nullable=False),
        sa.Column("query", sa.String(length=240), nullable=False),
        sa.Column("category", sa.String(length=120), nullable=False),
        sa.Column("selection_bucket", sa.String(length=80), nullable=False),
        sa.Column("split", sa.String(length=32), nullable=False),
        sa.Column("model_version", sa.String(length=80), nullable=True),
        sa.Column("model_match_probability", sa.Float(), nullable=True),
        sa.Column("model_decision", sa.String(length=32), nullable=True),
        sa.Column("label", sa.String(length=32), nullable=True),
        sa.Column("label_source", sa.String(length=80), nullable=True),
        sa.Column("label_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["campaign_name"], ["matching_dataset_campaigns.name"]),
        sa.ForeignKeyConstraint(["candidate_id"], ["product_match_candidates.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "campaign_name",
            "candidate_id",
            name="uq_matching_dataset_campaign_candidate",
        ),
    )


def downgrade() -> None:
    op.drop_table("matching_dataset_items")
    op.drop_table("matching_dataset_campaigns")
