"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-04-27
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stores",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "tracked_queries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("query", sa.String(length=240), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("limit", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("query"),
    )
    op.create_table(
        "scrape_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("query", sa.String(length=240), nullable=False),
        sa.Column("location_postal_code", sa.String(length=32), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("errors", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "canonical_products",
        sa.Column("canonical_key", sa.String(length=240), nullable=False),
        sa.Column("normalized_title", sa.Text(), nullable=False),
        sa.Column("brand", sa.String(length=120), nullable=True),
        sa.Column("model", sa.String(length=160), nullable=True),
        sa.Column("category", sa.String(length=120), nullable=True),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("embedding_text", sa.Text(), nullable=True),
        sa.Column("embedding_text_hash", sa.String(length=128), nullable=True),
        sa.Column("embedding_model", sa.String(length=120), nullable=True),
        sa.Column("embedding_dimensions", sa.Integer(), nullable=True),
        sa.Column("embedding", sa.JSON(), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("estimated_cost_usd", sa.Float(), nullable=True),
        sa.Column("embedded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("canonical_key"),
    )
    op.create_table(
        "embedding_usage_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("items_processed", sa.Integer(), nullable=False),
        sa.Column("tokens_used", sa.Integer(), nullable=False),
        sa.Column("estimated_cost_usd", sa.Float(), nullable=False),
        sa.Column("dry_run", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("errors", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "scrape_adapter_metrics",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("scrape_run_id", sa.Integer(), nullable=True),
        sa.Column("store_id", sa.String(length=80), nullable=False),
        sa.Column("store_name", sa.String(length=160), nullable=False),
        sa.Column("query", sa.String(length=240), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("strategy", sa.String(length=80), nullable=False),
        sa.Column("elapsed_ms", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("products_count", sa.Integer(), nullable=False),
        sa.Column("error_type", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["scrape_run_id"], ["scrape_runs.id"]),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "product_observations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("scrape_run_id", sa.Integer(), nullable=False),
        sa.Column("store_id", sa.String(length=80), nullable=False),
        sa.Column("store_name", sa.String(length=160), nullable=False),
        sa.Column("query", sa.String(length=240), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("currency", sa.String(length=16), nullable=True),
        sa.Column("original_price", sa.Float(), nullable=True),
        sa.Column("discount", sa.String(length=80), nullable=True),
        sa.Column("installments", sa.Text(), nullable=True),
        sa.Column("shipping", sa.Text(), nullable=True),
        sa.Column("seller", sa.String(length=240), nullable=True),
        sa.Column("rating", sa.Float(), nullable=True),
        sa.Column("reviews_count", sa.Integer(), nullable=True),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column("product_url", sa.Text(), nullable=False),
        sa.Column("condition", sa.String(length=32), nullable=False),
        sa.Column("availability", sa.String(length=32), nullable=False),
        sa.Column("sponsored", sa.Boolean(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("scraped_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_metadata", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["scrape_run_id"], ["scrape_runs.id"]),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scrape_run_id", "store_id", "product_url", name="uq_run_store_product"),
    )
    op.create_table(
        "transformed_product_observations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("scrape_run_id", sa.Integer(), nullable=False),
        sa.Column("store_id", sa.String(length=80), nullable=False),
        sa.Column("product_url", sa.Text(), nullable=False),
        sa.Column("canonical_key", sa.String(length=240), nullable=False),
        sa.Column("normalized_title", sa.Text(), nullable=False),
        sa.Column("brand", sa.String(length=120), nullable=True),
        sa.Column("model", sa.String(length=160), nullable=True),
        sa.Column("category", sa.String(length=120), nullable=True),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("is_accessory", sa.Boolean(), nullable=False),
        sa.Column("condition", sa.String(length=32), nullable=False),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("score_breakdown", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("trust_signals", sa.JSON(), nullable=False),
        sa.Column("raw_compact", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["canonical_key"], ["canonical_products.canonical_key"]),
        sa.ForeignKeyConstraint(["scrape_run_id"], ["scrape_runs.id"]),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "scrape_run_id",
            "store_id",
            "product_url",
            name="uq_run_store_transformed",
        ),
    )
    op.create_table(
        "product_match_candidates",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("scrape_run_id", sa.Integer(), nullable=False),
        sa.Column("query", sa.String(length=240), nullable=False),
        sa.Column("left_store_id", sa.String(length=80), nullable=False),
        sa.Column("left_title", sa.Text(), nullable=False),
        sa.Column("left_product_url", sa.Text(), nullable=False),
        sa.Column("left_canonical_key", sa.String(length=240), nullable=False),
        sa.Column("left_price", sa.Float(), nullable=True),
        sa.Column("right_store_id", sa.String(length=80), nullable=False),
        sa.Column("right_title", sa.Text(), nullable=False),
        sa.Column("right_product_url", sa.Text(), nullable=False),
        sa.Column("right_canonical_key", sa.String(length=240), nullable=False),
        sa.Column("right_price", sa.Float(), nullable=True),
        sa.Column("features", sa.JSON(), nullable=False),
        sa.Column("match_confidence", sa.Float(), nullable=False),
        sa.Column("label", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["left_store_id"], ["stores.id"]),
        sa.ForeignKeyConstraint(["right_store_id"], ["stores.id"]),
        sa.ForeignKeyConstraint(["scrape_run_id"], ["scrape_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "scrape_run_id",
            "left_store_id",
            "left_product_url",
            "right_store_id",
            "right_product_url",
            name="uq_run_match_candidate_pair",
        ),
    )
    op.create_table(
        "product_match_labels",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("candidate_id", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=32), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["product_match_candidates.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("product_match_labels")
    op.drop_table("product_match_candidates")
    op.drop_table("transformed_product_observations")
    op.drop_table("product_observations")
    op.drop_table("scrape_adapter_metrics")
    op.drop_table("embedding_usage_log")
    op.drop_table("canonical_products")
    op.drop_table("scrape_runs")
    op.drop_table("tracked_queries")
    op.drop_table("stores")
