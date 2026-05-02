"""matching model predictions

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-27
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "product_match_models",
        sa.Column("version", sa.String(length=80), nullable=False),
        sa.Column("algorithm", sa.String(length=120), nullable=False),
        sa.Column("features_version", sa.String(length=80), nullable=False),
        sa.Column("artifact_path", sa.Text(), nullable=False),
        sa.Column("trained_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("labels_count", sa.Integer(), nullable=False),
        sa.Column("positive_count", sa.Integer(), nullable=False),
        sa.Column("negative_count", sa.Integer(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("version"),
    )
    op.create_table(
        "product_match_predictions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("candidate_id", sa.Integer(), nullable=False),
        sa.Column("model_version", sa.String(length=80), nullable=False),
        sa.Column("match_probability", sa.Float(), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("predicted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["product_match_candidates.id"]),
        sa.ForeignKeyConstraint(["model_version"], ["product_match_models.version"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "candidate_id",
            "model_version",
            name="uq_match_prediction_candidate_model",
        ),
    )


def downgrade() -> None:
    op.drop_table("product_match_predictions")
    op.drop_table("product_match_models")
