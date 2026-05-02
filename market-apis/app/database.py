from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterator

_logger = logging.getLogger(__name__)

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    case,
    create_engine,
    func,
    text,
    select,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import StaticPool

from app.embeddings import EmbeddingCandidate, build_embedding_text, embedding_hash, utcnow
from app.matching import build_pair_features, build_pair_features_from_values, estimate_match_confidence
from app.models import (
    AgentHistoryItem,
    AdapterMetric,
    Product,
    ProductMatchCandidate,
    ProductMatchLabelRequest,
    ProductMatchLabelResponse,
    ProductMatchSummary,
    ProductPairFeatures,
    ScoredProduct,
    SemanticMatch,
    StoreError,
    TrackedQuery,
)

metadata = MetaData()

stores = Table(
    "stores",
    metadata,
    Column("id", String(80), primary_key=True),
    Column("name", String(160), nullable=False),
    Column("enabled", Boolean, nullable=False, default=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

tracked_queries = Table(
    "tracked_queries",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("query", String(240), nullable=False, unique=True),
    Column("enabled", Boolean, nullable=False, default=True),
    Column("limit", Integer, nullable=False, default=50),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

scrape_runs = Table(
    "scrape_runs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("query", String(240), nullable=False),
    Column("location_postal_code", String(32), nullable=True),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True), nullable=True),
    Column("status", String(32), nullable=False),
    Column("errors", JSON, nullable=False, default=list),
)

scrape_adapter_metrics = Table(
    "scrape_adapter_metrics",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("scrape_run_id", ForeignKey("scrape_runs.id"), nullable=True),
    Column("store_id", ForeignKey("stores.id"), nullable=False),
    Column("store_name", String(160), nullable=False),
    Column("query", String(240), nullable=False),
    Column("mode", String(32), nullable=False),
    Column("strategy", String(80), nullable=False),
    Column("elapsed_ms", Integer, nullable=False),
    Column("status", String(32), nullable=False),
    Column("products_count", Integer, nullable=False),
    Column("error_type", String(120), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

product_observations = Table(
    "product_observations",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("scrape_run_id", ForeignKey("scrape_runs.id"), nullable=False),
    Column("store_id", ForeignKey("stores.id"), nullable=False),
    Column("store_name", String(160), nullable=False),
    Column("query", String(240), nullable=False),
    Column("title", Text, nullable=False),
    Column("price", Float, nullable=True),
    Column("currency", String(16), nullable=True),
    Column("original_price", Float, nullable=True),
    Column("discount", String(80), nullable=True),
    Column("installments", Text, nullable=True),
    Column("shipping", Text, nullable=True),
    Column("seller", String(240), nullable=True),
    Column("rating", Float, nullable=True),
    Column("reviews_count", Integer, nullable=True),
    Column("image_url", Text, nullable=True),
    Column("product_url", Text, nullable=False),
    Column("condition", String(32), nullable=False),
    Column("availability", String(32), nullable=False),
    Column("sponsored", Boolean, nullable=False),
    Column("position", Integer, nullable=False),
    Column("scraped_at", DateTime(timezone=True), nullable=False),
    Column("raw_metadata", JSON, nullable=False, default=dict),
    UniqueConstraint("scrape_run_id", "store_id", "product_url", name="uq_run_store_product"),
)

canonical_products = Table(
    "canonical_products",
    metadata,
    Column("canonical_key", String(240), primary_key=True),
    Column("normalized_title", Text, nullable=False),
    Column("brand", String(120), nullable=True),
    Column("model", String(160), nullable=True),
    Column("category", String(120), nullable=True),
    Column("attributes", JSON, nullable=False, default=dict),
    Column("embedding_text", Text, nullable=True),
    Column("embedding_text_hash", String(128), nullable=True),
    Column("embedding_model", String(120), nullable=True),
    Column("embedding_dimensions", Integer, nullable=True),
    Column("embedding", JSON, nullable=True),
    Column("token_count", Integer, nullable=True),
    Column("estimated_cost_usd", Float, nullable=True),
    Column("embedded_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

embedding_usage_log = Table(
    "embedding_usage_log",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("model", String(120), nullable=False),
    Column("items_processed", Integer, nullable=False),
    Column("tokens_used", Integer, nullable=False),
    Column("estimated_cost_usd", Float, nullable=False),
    Column("dry_run", Boolean, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("errors", JSON, nullable=False, default=list),
)

transformed_product_observations = Table(
    "transformed_product_observations",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("scrape_run_id", ForeignKey("scrape_runs.id"), nullable=False),
    Column("store_id", ForeignKey("stores.id"), nullable=False),
    Column("product_url", Text, nullable=False),
    Column("canonical_key", ForeignKey("canonical_products.canonical_key"), nullable=False),
    Column("normalized_title", Text, nullable=False),
    Column("brand", String(120), nullable=True),
    Column("model", String(160), nullable=True),
    Column("category", String(120), nullable=True),
    Column("attributes", JSON, nullable=False, default=dict),
    Column("is_accessory", Boolean, nullable=False),
    Column("condition", String(32), nullable=False),
    Column("price", Float, nullable=True),
    Column("score", Float, nullable=False),
    Column("score_breakdown", JSON, nullable=False, default=dict),
    Column("warnings", JSON, nullable=False, default=list),
    Column("trust_signals", JSON, nullable=False, default=dict),
    Column("raw_compact", JSON, nullable=False, default=dict),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("scrape_run_id", "store_id", "product_url", name="uq_run_store_transformed"),
)

product_match_candidates = Table(
    "product_match_candidates",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("scrape_run_id", ForeignKey("scrape_runs.id"), nullable=False),
    Column("query", String(240), nullable=False),
    Column("left_store_id", ForeignKey("stores.id"), nullable=False),
    Column("left_title", Text, nullable=False),
    Column("left_product_url", Text, nullable=False),
    Column("left_canonical_key", String(240), nullable=False),
    Column("left_price", Float, nullable=True),
    Column("right_store_id", ForeignKey("stores.id"), nullable=False),
    Column("right_title", Text, nullable=False),
    Column("right_product_url", Text, nullable=False),
    Column("right_canonical_key", String(240), nullable=False),
    Column("right_price", Float, nullable=True),
    Column("features", JSON, nullable=False, default=dict),
    Column("match_confidence", Float, nullable=False),
    Column("label", String(32), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "scrape_run_id",
        "left_store_id",
        "left_product_url",
        "right_store_id",
        "right_product_url",
        name="uq_run_match_candidate_pair",
    ),
)

product_match_labels = Table(
    "product_match_labels",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("candidate_id", ForeignKey("product_match_candidates.id"), nullable=False),
    Column("label", String(32), nullable=False),
    Column("comment", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

product_match_models = Table(
    "product_match_models",
    metadata,
    Column("version", String(80), primary_key=True),
    Column("algorithm", String(120), nullable=False),
    Column("features_version", String(80), nullable=False),
    Column("artifact_path", Text, nullable=False),
    Column("trained_at", DateTime(timezone=True), nullable=False),
    Column("labels_count", Integer, nullable=False),
    Column("positive_count", Integer, nullable=False),
    Column("negative_count", Integer, nullable=False),
    Column("metrics", JSON, nullable=False, default=dict),
    Column("active", Boolean, nullable=False, default=False),
)

product_match_predictions = Table(
    "product_match_predictions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("candidate_id", ForeignKey("product_match_candidates.id"), nullable=False),
    Column("model_version", ForeignKey("product_match_models.version"), nullable=False),
    Column("match_probability", Float, nullable=False),
    Column("decision", String(32), nullable=False),
    Column("predicted_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "candidate_id",
        "model_version",
        name="uq_match_prediction_candidate_model",
    ),
)

matching_dataset_campaigns = Table(
    "matching_dataset_campaigns",
    metadata,
    Column("name", String(120), primary_key=True),
    Column("description", Text, nullable=True),
    Column("status", String(32), nullable=False),
    Column("target_train_count", Integer, nullable=False),
    Column("target_test_count", Integer, nullable=False),
    Column("queries", JSON, nullable=False, default=list),
    Column("query_categories", JSON, nullable=False, default=dict),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("frozen_at", DateTime(timezone=True), nullable=True),
)

matching_dataset_items = Table(
    "matching_dataset_items",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("campaign_name", ForeignKey("matching_dataset_campaigns.name"), nullable=False),
    Column("candidate_id", ForeignKey("product_match_candidates.id"), nullable=False),
    Column("query", String(240), nullable=False),
    Column("category", String(120), nullable=False),
    Column("selection_bucket", String(80), nullable=False),
    Column("split", String(32), nullable=False),
    Column("model_version", String(80), nullable=True),
    Column("model_match_probability", Float, nullable=True),
    Column("model_decision", String(32), nullable=True),
    Column("label", String(32), nullable=True),
    Column("label_source", String(80), nullable=True),
    Column("label_reason", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("campaign_name", "candidate_id", name="uq_matching_dataset_campaign_candidate"),
)


@dataclass
class AppendResultsJob:
    run_id: int | None
    query: str
    products: list[Product]
    scored: list[ScoredProduct]


@dataclass
class SaveMetricsJob:
    run_id: int | None
    metrics: list[AdapterMetric]


@dataclass
class FinishRunJob:
    run_id: int | None
    status: str
    errors: list[StoreError]


@dataclass
class SaveSnapshotJob:
    query: str
    postal_code: str
    products: list[Product]
    errors: list[StoreError]
    metrics: list[AdapterMetric]


@dataclass
class GenerateMatchCandidatesJob:
    run_id: int | None
    query: str
    scored: list[ScoredProduct]


PersistenceJob = (
    AppendResultsJob
    | SaveMetricsJob
    | FinishRunJob
    | SaveSnapshotJob
    | GenerateMatchCandidatesJob
)


class PersistenceWorker:
    def __init__(
        self,
        repository: OptionalRepository,
        on_match_candidates_saved: Callable[[], None] | None = None,
    ) -> None:
        self._repo = repository
        self._on_match_candidates_saved = on_match_candidates_saved
        self._queue: asyncio.Queue[PersistenceJob | None] = asyncio.Queue()
        self._task: asyncio.Task | None = None

    def enqueue(self, job: PersistenceJob) -> None:
        self._queue.put_nowait(job)

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._queue.put_nowait(None)
        if self._task:
            await self._task

    async def _run(self) -> None:
        while True:
            job = await self._queue.get()
            if job is None:
                break
            try:
                await asyncio.to_thread(self._execute, job)
            except Exception as exc:
                _logger.error("persistence_worker error: %s", exc)

    def _execute(self, job: PersistenceJob) -> None:
        if isinstance(job, AppendResultsJob):
            self._repo.append_search_run_results(job.run_id, job.query, job.products, job.scored)
        elif isinstance(job, SaveMetricsJob):
            self._repo.save_adapter_metrics(job.run_id, job.metrics)
        elif isinstance(job, FinishRunJob):
            self._repo.finish_scrape_run(job.run_id, job.status, job.errors)
        elif isinstance(job, SaveSnapshotJob):
            run_id, _ = self._repo.save_search_snapshot(
                job.query, job.postal_code, job.products, job.errors
            )
            if run_id is not None:
                self._repo.save_adapter_metrics(run_id, job.metrics)
        elif isinstance(job, GenerateMatchCandidatesJob):
            self._repo.save_match_candidates(job.run_id, job.query, job.scored)
            if self._on_match_candidates_saved is not None:
                self._on_match_candidates_saved()


class SearchRepository:
    def __init__(self, database_url: str) -> None:
        if database_url == "sqlite+pysqlite:///:memory:":
            self.engine = create_engine(
                database_url,
                future=True,
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )
        else:
            self.engine = create_engine(database_url, future=True)

    def init_schema(self) -> None:
        metadata.create_all(self.engine)
        self._enable_pgvector_if_available()

    def seed_stores(self, store_map: dict[str, str]) -> None:
        now = datetime.now(timezone.utc)
        with self._transaction() as conn:
            existing = set(conn.execute(select(stores.c.id)).scalars().all())
            for store_id, store_name in store_map.items():
                if store_id in existing:
                    continue
                conn.execute(
                    stores.insert().values(
                        id=store_id,
                        name=store_name,
                        enabled=True,
                        created_at=now,
                    )
                )

    def seed_tracked_queries(self, queries: list[TrackedQuery]) -> None:
        now = datetime.now(timezone.utc)
        with self._transaction() as conn:
            existing = set(conn.execute(select(tracked_queries.c.query)).scalars().all())
            for query in queries:
                if query.query in existing:
                    continue
                conn.execute(
                    tracked_queries.insert().values(
                        query=query.query,
                        enabled=query.enabled,
                        limit=query.limit,
                        created_at=now,
                    )
                )

    def list_tracked_queries(self) -> list[TrackedQuery]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(
                    tracked_queries.c.query,
                    tracked_queries.c.enabled,
                    tracked_queries.c.limit,
                ).where(tracked_queries.c.enabled.is_(True))
            ).mappings()
            return [TrackedQuery(**dict(row)) for row in rows]

    def save_search_snapshot(
        self,
        query: str,
        postal_code: str,
        products: list[Product],
        errors: list[StoreError],
        transformed_products: list[ScoredProduct] | None = None,
    ) -> int:
        now = datetime.now(timezone.utc)
        with self._transaction() as conn:
            run_id = conn.execute(
                scrape_runs.insert()
                .values(
                    query=query,
                    location_postal_code=postal_code,
                    started_at=now,
                    finished_at=now,
                    status="completed_with_errors" if errors else "completed",
                    errors=[error.model_dump() for error in errors],
                )
                .returning(scrape_runs.c.id)
            ).scalar_one()

            seen_products: set[tuple[str, str]] = set()
            for product in products:
                dedupe_key = (product.store_id, str(product.product_url))
                if dedupe_key in seen_products:
                    continue
                seen_products.add(dedupe_key)
                conn.execute(
                    product_observations.insert().values(
                        scrape_run_id=run_id,
                        store_id=product.store_id,
                        store_name=product.store_name,
                        query=query,
                        title=product.title,
                        price=product.price,
                        currency=product.currency,
                        original_price=product.original_price,
                        discount=product.discount,
                        installments=product.installments,
                        shipping=product.shipping,
                        seller=product.seller,
                        rating=product.rating,
                        reviews_count=product.reviews_count,
                        image_url=str(product.image_url) if product.image_url else None,
                        product_url=str(product.product_url),
                        condition=product.condition.value,
                        availability=product.availability.value,
                        sponsored=product.sponsored,
                        position=product.position,
                        scraped_at=product.scraped_at,
                        raw_metadata=product.raw_metadata,
                    )
                )
            if transformed_products:
                self._save_transformed_products(conn, run_id, transformed_products, now)
            return int(run_id)

    def create_scrape_run(
        self,
        query: str,
        postal_code: str,
        status: str = "running",
    ) -> int:
        now = datetime.now(timezone.utc)
        with self._transaction() as conn:
            run_id = conn.execute(
                scrape_runs.insert()
                .values(
                    query=query,
                    location_postal_code=postal_code,
                    started_at=now,
                    finished_at=None,
                    status=status,
                    errors=[],
                )
                .returning(scrape_runs.c.id)
            ).scalar_one()
        return int(run_id)

    def append_search_run_results(
        self,
        run_id: int,
        query: str,
        products: list[Product],
        transformed_products: list[ScoredProduct] | None = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        with self._transaction() as conn:
            self._save_product_observations(conn, run_id, query, products)
            if transformed_products:
                self._save_transformed_products(conn, run_id, transformed_products, now)

    def finish_scrape_run(
        self,
        run_id: int,
        status: str,
        errors: list[StoreError],
    ) -> None:
        with self._transaction() as conn:
            conn.execute(
                scrape_runs.update()
                .where(scrape_runs.c.id == run_id)
                .values(
                    finished_at=datetime.now(timezone.utc),
                    status=status,
                    errors=[error.model_dump() for error in errors],
                )
            )

    def save_adapter_metrics(
        self,
        run_id: int | None,
        metrics: list[AdapterMetric],
    ) -> None:
        if not metrics:
            return
        now = datetime.now(timezone.utc)
        with self._transaction() as conn:
            for metric in metrics:
                conn.execute(
                    scrape_adapter_metrics.insert().values(
                        scrape_run_id=run_id,
                        store_id=metric.store_id,
                        store_name=metric.store_name,
                        query=metric.query,
                        mode=metric.mode.value,
                        strategy=metric.strategy,
                        elapsed_ms=metric.elapsed_ms,
                        status=metric.status,
                        products_count=metric.products_count,
                        error_type=metric.error_type,
                        created_at=now,
                    )
                )

    def get_history_signal(self, canonical_key: str, current_price: float | None) -> str | None:
        if current_price is None:
            return None
        with self.engine.connect() as conn:
            prices = conn.execute(
                select(transformed_product_observations.c.price)
                .where(transformed_product_observations.c.canonical_key == canonical_key)
                .where(transformed_product_observations.c.price.is_not(None))
            ).scalars().all()
        if len(prices) < 2:
            return None

        average = sum(prices) / len(prices)
        if current_price <= average * 0.9:
            return "below_recent_average"
        if current_price >= average * 1.1:
            return "above_recent_average"
        return "near_recent_average"

    def get_history_baselines(self, canonical_keys: list[str]) -> dict[str, tuple[float, int]]:
        if not canonical_keys:
            return {}
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(
                    transformed_product_observations.c.canonical_key,
                    func.avg(transformed_product_observations.c.price).label("average_price"),
                    func.count(transformed_product_observations.c.price).label("price_count"),
                )
                .where(transformed_product_observations.c.canonical_key.in_(set(canonical_keys)))
                .where(transformed_product_observations.c.price.is_not(None))
                .group_by(transformed_product_observations.c.canonical_key)
            ).mappings()
            return {
                row["canonical_key"]: (float(row["average_price"]), int(row["price_count"]))
                for row in rows
            }

    def get_run_history_items(self, run_id: int) -> list[AgentHistoryItem]:
        with self.engine.connect() as conn:
            run_rows = conn.execute(
                select(
                    transformed_product_observations.c.store_id,
                    transformed_product_observations.c.product_url,
                    transformed_product_observations.c.canonical_key,
                    transformed_product_observations.c.normalized_title,
                    transformed_product_observations.c.price,
                ).where(transformed_product_observations.c.scrape_run_id == run_id)
            ).mappings().all()

            canonical_keys = {row["canonical_key"] for row in run_rows}
            if canonical_keys:
                baseline_rows = conn.execute(
                    select(
                        transformed_product_observations.c.canonical_key,
                        func.avg(transformed_product_observations.c.price).label("average_price"),
                        func.count(transformed_product_observations.c.price).label("price_count"),
                    )
                    .where(transformed_product_observations.c.canonical_key.in_(canonical_keys))
                    .where(transformed_product_observations.c.scrape_run_id != run_id)
                    .where(transformed_product_observations.c.price.is_not(None))
                    .group_by(transformed_product_observations.c.canonical_key)
                ).mappings().all()
            else:
                baseline_rows = []

        baselines = {
            row["canonical_key"]: (float(row["average_price"]), int(row["price_count"]))
            for row in baseline_rows
        }
        return [
            AgentHistoryItem(
                store_id=row["store_id"],
                product_url=row["product_url"],
                canonical_key=row["canonical_key"],
                normalized_title=row["normalized_title"],
                price=row["price"],
                historical_signal=_history_signal_from_baseline(
                    baselines.get(row["canonical_key"]),
                    row["price"],
                ),
                average_price=(
                    baselines[row["canonical_key"]][0]
                    if row["canonical_key"] in baselines
                    else None
                ),
                price_count=(
                    baselines[row["canonical_key"]][1]
                    if row["canonical_key"] in baselines
                    else 0
                ),
            )
            for row in run_rows
        ]

    def save_match_candidates(
        self,
        run_id: int,
        query: str,
        scored: list[ScoredProduct],
        max_products: int = 20,
    ) -> int:
        candidates = _build_match_candidate_rows(run_id, query, scored, max_products)
        if not candidates:
            return 0
        with self._transaction() as conn:
            existing = {
                (
                    row["left_store_id"],
                    row["left_product_url"],
                    row["right_store_id"],
                    row["right_product_url"],
                )
                for row in conn.execute(
                    select(
                        product_match_candidates.c.left_store_id,
                        product_match_candidates.c.left_product_url,
                        product_match_candidates.c.right_store_id,
                        product_match_candidates.c.right_product_url,
                    ).where(product_match_candidates.c.scrape_run_id == run_id)
                ).mappings()
            }
            missing = [
                row
                for row in candidates
                if (
                    row["left_store_id"],
                    row["left_product_url"],
                    row["right_store_id"],
                    row["right_product_url"],
                )
                not in existing
            ]
            if missing:
                conn.execute(product_match_candidates.insert(), missing)
        return len(missing)

    def list_match_candidates(
        self,
        status: str = "unlabeled",
        limit: int = 50,
        query_text: str | None = None,
        run_id: int | None = None,
    ) -> list[ProductMatchCandidate]:
        query = select(product_match_candidates)
        if status == "unlabeled":
            query = query.where(product_match_candidates.c.label.is_(None))
        elif status == "labeled":
            query = query.where(product_match_candidates.c.label.is_not(None))
        if query_text:
            query = query.where(product_match_candidates.c.query == query_text)
        if run_id is not None:
            query = query.where(product_match_candidates.c.scrape_run_id == run_id)
        query = query.order_by(
            func.abs(product_match_candidates.c.match_confidence - 0.5),
            product_match_candidates.c.created_at.desc(),
        ).limit(limit)
        with self.engine.connect() as conn:
            rows = conn.execute(query).mappings().all()
            predictions = self._latest_predictions_for_candidates(
                conn,
                [row["id"] for row in rows],
            )
        return [_match_candidate_from_row(row, predictions.get(row["id"])) for row in rows]

    def label_match_candidate(
        self,
        candidate_id: int,
        request: ProductMatchLabelRequest,
    ) -> ProductMatchLabelResponse | None:
        now = datetime.now(timezone.utc)
        with self._transaction() as conn:
            exists = conn.execute(
                select(product_match_candidates.c.id).where(
                    product_match_candidates.c.id == candidate_id
                )
            ).scalar_one_or_none()
            if exists is None:
                return None
            conn.execute(
                product_match_labels.insert().values(
                    candidate_id=candidate_id,
                    label=request.label.value,
                    comment=request.comment,
                    created_at=now,
                )
            )
            conn.execute(
                product_match_candidates.update()
                .where(product_match_candidates.c.id == candidate_id)
                .values(label=request.label.value, updated_at=now)
            )
        return ProductMatchLabelResponse(
            candidate_id=candidate_id,
            label=request.label,
            comment=request.comment,
        )

    def get_match_summary(self) -> ProductMatchSummary:
        with self.engine.connect() as conn:
            aggregate = conn.execute(
                select(
                    func.count().label("total"),
                    func.sum(
                        case((product_match_candidates.c.label.is_(None), 1), else_=0)
                    ).label("unlabeled"),
                    func.sum(
                        case((product_match_candidates.c.match_confidence < 0.35, 1), else_=0)
                    ).label("low"),
                    func.sum(
                        case(
                            (
                                product_match_candidates.c.match_confidence.between(
                                    0.35,
                                    0.75,
                                ),
                                1,
                            ),
                            else_=0,
                        )
                    ).label("uncertain"),
                    func.sum(
                        case((product_match_candidates.c.match_confidence > 0.75, 1), else_=0)
                    ).label("high"),
                ).select_from(product_match_candidates)
            ).mappings().one()
            label_rows = conn.execute(
                select(
                    product_match_candidates.c.label,
                    func.count().label("count"),
                )
                .where(product_match_candidates.c.label.is_not(None))
                .group_by(product_match_candidates.c.label)
            ).mappings()
            active_model = conn.execute(
                select(product_match_models)
                .where(product_match_models.c.active.is_(True))
                .order_by(product_match_models.c.trained_at.desc())
                .limit(1)
            ).mappings().first()
            predictions_count = conn.execute(
                select(func.count()).select_from(product_match_predictions)
            ).scalar_one()

        return ProductMatchSummary(
            total_candidates=int(aggregate["total"] or 0),
            unlabeled_candidates=int(aggregate["unlabeled"] or 0),
            labels_by_value={row["label"]: int(row["count"]) for row in label_rows},
            confidence_buckets={
                "low": int(aggregate["low"] or 0),
                "uncertain": int(aggregate["uncertain"] or 0),
                "high": int(aggregate["high"] or 0),
            },
            active_model_version=active_model["version"] if active_model else None,
            model_predictions_count=int(predictions_count),
            latest_model_metrics=active_model["metrics"] if active_model else None,
        )

    def get_match_training_rows(self) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(
                    product_match_candidates.c.id,
                    product_match_candidates.c.features,
                    product_match_candidates.c.label,
                    product_match_candidates.c.left_title,
                    product_match_candidates.c.left_canonical_key,
                    product_match_candidates.c.left_price,
                    product_match_candidates.c.right_title,
                    product_match_candidates.c.right_canonical_key,
                    product_match_candidates.c.right_price,
                )
                .where(product_match_candidates.c.label.in_(["same", "different"]))
                .order_by(product_match_candidates.c.updated_at.asc())
            ).mappings().all()
        return [dict(row) for row in rows]

    def get_unlabeled_match_candidates_for_prediction(
        self,
        limit: int = 1000,
    ) -> list[ProductMatchCandidate]:
        query = (
            select(product_match_candidates)
            .where(product_match_candidates.c.label.is_(None))
            .order_by(
                func.abs(product_match_candidates.c.match_confidence - 0.5),
                product_match_candidates.c.created_at.desc(),
            )
            .limit(limit)
        )
        with self.engine.connect() as conn:
            rows = conn.execute(query).mappings().all()
            predictions = self._latest_predictions_for_candidates(
                conn,
                [row["id"] for row in rows],
            )
        return [_match_candidate_from_row(row, predictions.get(row["id"])) for row in rows]

    def save_match_model(
        self,
        *,
        version: str,
        algorithm: str,
        features_version: str,
        artifact_path: str,
        labels_count: int,
        positive_count: int,
        negative_count: int,
        metrics: dict,
        active: bool = True,
    ) -> None:
        now = datetime.now(timezone.utc)
        with self._transaction() as conn:
            if active:
                conn.execute(product_match_models.update().values(active=False))
            conn.execute(
                product_match_models.insert().values(
                    version=version,
                    algorithm=algorithm,
                    features_version=features_version,
                    artifact_path=artifact_path,
                    trained_at=now,
                    labels_count=labels_count,
                    positive_count=positive_count,
                    negative_count=negative_count,
                    metrics=metrics,
                    active=active,
                )
            )

    def get_active_match_model(self) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(product_match_models)
                .where(product_match_models.c.active.is_(True))
                .order_by(product_match_models.c.trained_at.desc())
                .limit(1)
            ).mappings().first()
        return dict(row) if row else None

    def save_match_predictions(
        self,
        model_version: str,
        predictions: list[tuple[int, float, str]],
    ) -> int:
        if not predictions:
            return 0
        now = datetime.now(timezone.utc)
        rows = [
            {
                "candidate_id": candidate_id,
                "model_version": model_version,
                "match_probability": probability,
                "decision": decision,
                "predicted_at": now,
            }
            for candidate_id, probability, decision in predictions
        ]
        with self._transaction() as conn:
            existing = {
                (row["candidate_id"], row["model_version"])
                for row in conn.execute(
                    select(
                        product_match_predictions.c.candidate_id,
                        product_match_predictions.c.model_version,
                    ).where(product_match_predictions.c.model_version == model_version)
                ).mappings()
            }
            missing = [
                row
                for row in rows
                if (row["candidate_id"], row["model_version"]) not in existing
            ]
            if missing:
                conn.execute(product_match_predictions.insert(), missing)
        return len(missing)

    def _latest_predictions_for_candidates(self, conn, candidate_ids: list[int]) -> dict[int, dict]:
        if not candidate_ids:
            return {}
        rows = conn.execute(
            select(
                product_match_predictions.c.candidate_id,
                product_match_predictions.c.model_version,
                product_match_predictions.c.match_probability,
                product_match_predictions.c.decision,
                product_match_predictions.c.predicted_at,
            )
            .where(product_match_predictions.c.candidate_id.in_(candidate_ids))
            .order_by(product_match_predictions.c.predicted_at.desc())
        ).mappings().all()
        latest: dict[int, dict] = {}
        for row in rows:
            latest.setdefault(row["candidate_id"], dict(row))
        return latest

    def create_matching_dataset_campaign(
        self,
        *,
        name: str,
        description: str | None,
        queries: list[str],
        query_categories: dict[str, str],
        target_train_count: int = 200,
        target_test_count: int = 100,
    ) -> None:
        now = datetime.now(timezone.utc)
        with self._transaction() as conn:
            existing = conn.execute(
                select(matching_dataset_campaigns.c.name).where(
                    matching_dataset_campaigns.c.name == name
                )
            ).scalar_one_or_none()
            values = {
                "description": description,
                "status": "collecting",
                "target_train_count": target_train_count,
                "target_test_count": target_test_count,
                "queries": queries,
                "query_categories": query_categories,
                "updated_at": now,
            }
            if existing:
                existing_row = conn.execute(
                    select(
                        matching_dataset_campaigns.c.queries,
                        matching_dataset_campaigns.c.query_categories,
                    ).where(matching_dataset_campaigns.c.name == name)
                ).mappings().one()
                merged_queries = list(dict.fromkeys([
                    *(existing_row["queries"] or []),
                    *queries,
                ]))
                merged_categories = {
                    **(existing_row["query_categories"] or {}),
                    **query_categories,
                }
                conn.execute(
                    matching_dataset_campaigns.update()
                    .where(matching_dataset_campaigns.c.name == name)
                    .values(
                        **{
                            **values,
                            "queries": merged_queries,
                            "query_categories": merged_categories,
                        }
                    )
                )
            else:
                conn.execute(
                    matching_dataset_campaigns.insert().values(
                        name=name,
                        created_at=now,
                        frozen_at=None,
                        **values,
                    )
                )

    def add_matching_dataset_items(
        self,
        campaign_name: str,
        items: list[dict],
    ) -> int:
        if not items:
            return 0
        now = datetime.now(timezone.utc)
        rows = [
            {
                "campaign_name": campaign_name,
                "candidate_id": item["candidate_id"],
                "query": item["query"],
                "category": item["category"],
                "selection_bucket": item.get("selection_bucket", "pool"),
                "split": item.get("split", "pool"),
                "model_version": item.get("model_version"),
                "model_match_probability": item.get("model_match_probability"),
                "model_decision": item.get("model_decision"),
                "label": item.get("label"),
                "label_source": item.get("label_source"),
                "label_reason": item.get("label_reason"),
                "created_at": now,
                "updated_at": now,
            }
            for item in items
        ]
        with self._transaction() as conn:
            existing = set(
                conn.execute(
                    select(matching_dataset_items.c.candidate_id).where(
                        matching_dataset_items.c.campaign_name == campaign_name
                    )
                ).scalars().all()
            )
            missing = [row for row in rows if row["candidate_id"] not in existing]
            if missing:
                conn.execute(matching_dataset_items.insert(), missing)
                conn.execute(
                    matching_dataset_campaigns.update()
                    .where(matching_dataset_campaigns.c.name == campaign_name)
                    .values(updated_at=now)
                )
        return len(missing)

    def list_matching_dataset_rows(
        self,
        campaign_name: str,
        *,
        split: str | None = None,
        labeled: bool | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        query = (
            select(
                matching_dataset_items,
                product_match_candidates.c.scrape_run_id,
                product_match_candidates.c.left_store_id,
                product_match_candidates.c.left_title,
                product_match_candidates.c.left_product_url,
                product_match_candidates.c.left_canonical_key,
                product_match_candidates.c.left_price,
                product_match_candidates.c.right_store_id,
                product_match_candidates.c.right_title,
                product_match_candidates.c.right_product_url,
                product_match_candidates.c.right_canonical_key,
                product_match_candidates.c.right_price,
                product_match_candidates.c.features,
                product_match_candidates.c.match_confidence,
            )
            .join(
                product_match_candidates,
                matching_dataset_items.c.candidate_id == product_match_candidates.c.id,
            )
            .where(matching_dataset_items.c.campaign_name == campaign_name)
            .order_by(matching_dataset_items.c.id.asc())
        )
        if split:
            query = query.where(matching_dataset_items.c.split == split)
        if labeled is True:
            query = query.where(matching_dataset_items.c.label.is_not(None))
        elif labeled is False:
            query = query.where(matching_dataset_items.c.label.is_(None))
        if limit:
            query = query.limit(limit)
        with self.engine.connect() as conn:
            rows = conn.execute(query).mappings().all()
        return [dict(row) for row in rows]

    def update_matching_dataset_item_splits(
        self,
        campaign_name: str,
        assignments: list[tuple[int, str, str]],
        status: str = "sampled",
    ) -> int:
        now = datetime.now(timezone.utc)
        with self._transaction() as conn:
            conn.execute(
                matching_dataset_items.update()
                .where(matching_dataset_items.c.campaign_name == campaign_name)
                .values(
                    split="pool",
                    selection_bucket="pool",
                    updated_at=now,
                )
            )
            for item_id, split, selection_bucket in assignments:
                conn.execute(
                    matching_dataset_items.update()
                    .where(matching_dataset_items.c.id == item_id)
                    .where(matching_dataset_items.c.campaign_name == campaign_name)
                    .values(
                        split=split,
                        selection_bucket=selection_bucket,
                        updated_at=now,
                    )
                )
            conn.execute(
                matching_dataset_campaigns.update()
                .where(matching_dataset_campaigns.c.name == campaign_name)
                .values(status=status, updated_at=now)
            )
        return len(assignments)

    def label_matching_dataset_item(
        self,
        item_id: int,
        label: str,
        *,
        label_source: str = "human_assisted",
        label_reason: str | None = None,
    ) -> bool:
        now = datetime.now(timezone.utc)
        with self._transaction() as conn:
            result = conn.execute(
                matching_dataset_items.update()
                .where(matching_dataset_items.c.id == item_id)
                .values(
                    label=label,
                    label_source=label_source,
                    label_reason=label_reason,
                    updated_at=now,
                )
            )
        return bool(result.rowcount)

    def freeze_matching_dataset_campaign(self, campaign_name: str) -> None:
        now = datetime.now(timezone.utc)
        with self._transaction() as conn:
            conn.execute(
                matching_dataset_campaigns.update()
                .where(matching_dataset_campaigns.c.name == campaign_name)
                .values(status="frozen", frozen_at=now, updated_at=now)
            )

    def matching_dataset_summary(self, campaign_name: str) -> dict:
        with self.engine.connect() as conn:
            campaign = conn.execute(
                select(matching_dataset_campaigns).where(
                    matching_dataset_campaigns.c.name == campaign_name
                )
            ).mappings().first()
            split_rows = conn.execute(
                select(
                    matching_dataset_items.c.split,
                    func.count().label("count"),
                )
                .where(matching_dataset_items.c.campaign_name == campaign_name)
                .group_by(matching_dataset_items.c.split)
            ).mappings().all()
            label_rows = conn.execute(
                select(
                    matching_dataset_items.c.split,
                    matching_dataset_items.c.label,
                    func.count().label("count"),
                )
                .where(matching_dataset_items.c.campaign_name == campaign_name)
                .group_by(matching_dataset_items.c.split, matching_dataset_items.c.label)
            ).mappings().all()
        return {
            "campaign": dict(campaign) if campaign else None,
            "splits": {row["split"]: int(row["count"]) for row in split_rows},
            "labels": [
                {"split": row["split"], "label": row["label"], "count": int(row["count"])}
                for row in label_rows
            ],
        }

    def list_embedding_candidates(
        self,
        model: str,
        dimensions: int,
        limit: int,
        force: bool = False,
    ) -> list[EmbeddingCandidate]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(canonical_products).limit(limit * 3)).mappings().all()

        candidates: list[EmbeddingCandidate] = []
        for row in rows:
            embedding_text = build_embedding_text(
                normalized_title=row["normalized_title"],
                brand=row["brand"],
                model=row["model"],
                category=row["category"],
                attributes=row["attributes"] or {},
            )
            text_hash = embedding_hash(embedding_text, model, dimensions)
            if not force and row["embedding_text_hash"] == text_hash and row["embedding"] is not None:
                continue
            candidates.append(
                EmbeddingCandidate(
                    canonical_key=row["canonical_key"],
                    embedding_text=embedding_text,
                    embedding_text_hash=text_hash,
                )
            )
            if len(candidates) >= limit:
                break
        return candidates

    def used_embedding_tokens_this_month(self, model: str) -> int:
        now = utcnow()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        with self.engine.connect() as conn:
            value = conn.execute(
                select(func.coalesce(func.sum(embedding_usage_log.c.tokens_used), 0))
                .where(embedding_usage_log.c.model == model)
                .where(embedding_usage_log.c.dry_run.is_(False))
                .where(embedding_usage_log.c.created_at >= month_start)
            ).scalar_one()
        return int(value or 0)

    def save_embeddings(
        self,
        model: str,
        dimensions: int,
        embeddings: list[tuple[EmbeddingCandidate, list[float], int, float]],
    ) -> None:
        now = utcnow()
        with self._transaction() as conn:
            for candidate, embedding, token_count, estimated_cost in embeddings:
                conn.execute(
                    canonical_products.update()
                    .where(canonical_products.c.canonical_key == candidate.canonical_key)
                    .values(
                        embedding_text=candidate.embedding_text,
                        embedding_text_hash=candidate.embedding_text_hash,
                        embedding_model=model,
                        embedding_dimensions=dimensions,
                        embedding=embedding,
                        token_count=token_count,
                        estimated_cost_usd=estimated_cost,
                        embedded_at=now,
                        updated_at=now,
                    )
                )

    def log_embedding_usage(
        self,
        model: str,
        items_processed: int,
        tokens_used: int,
        estimated_cost_usd: float,
        dry_run: bool,
        errors: list[str],
    ) -> None:
        with self._transaction() as conn:
            conn.execute(
                embedding_usage_log.insert().values(
                    model=model,
                    items_processed=items_processed,
                    tokens_used=tokens_used,
                    estimated_cost_usd=estimated_cost_usd,
                    dry_run=dry_run,
                    created_at=utcnow(),
                    errors=errors,
                )
            )

    def find_semantic_match(
        self,
        canonical_key: str,
        embedding: list[float] | None,
        min_score: float = 0.82,
    ) -> SemanticMatch | None:
        if not embedding:
            return None
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(
                    canonical_products.c.canonical_key,
                    canonical_products.c.embedding,
                )
                .where(canonical_products.c.canonical_key != canonical_key)
                .where(canonical_products.c.embedding.is_not(None))
            ).mappings().all()

        best_key = None
        best_score = -1.0
        for row in rows:
            candidate_embedding = row["embedding"]
            if not isinstance(candidate_embedding, list):
                continue
            score = _cosine_similarity(embedding, candidate_embedding)
            if score > best_score:
                best_score = score
                best_key = row["canonical_key"]

        if best_key is None or best_score < min_score:
            return None
        return SemanticMatch(
            canonical_key=best_key,
            score=round(best_score, 4),
            reason="Producto canonico semanticamente similar.",
        )

    def _save_transformed_products(
        self,
        conn,
        run_id: int,
        transformed_products: list[ScoredProduct],
        now: datetime,
    ) -> None:
        seen: set[tuple[str, str]] = set()
        canonical_rows: dict[str, dict] = {}
        observation_rows: list[dict] = []

        for scored in transformed_products:
            product = scored.product
            normalized = scored.normalized
            dedupe_key = (product.store_id, str(product.product_url))
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            if normalized.canonical_key not in canonical_rows:
                canonical_rows[normalized.canonical_key] = {
                    "canonical_key": normalized.canonical_key,
                    "normalized_title": normalized.normalized_title,
                    "brand": normalized.brand,
                    "model": normalized.model,
                    "category": normalized.category,
                    "attributes": normalized.attributes,
                    "created_at": now,
                    "updated_at": now,
                }

            observation_rows.append({
                "scrape_run_id": run_id,
                "store_id": product.store_id,
                "product_url": str(product.product_url),
                "canonical_key": normalized.canonical_key,
                "normalized_title": normalized.normalized_title,
                "brand": normalized.brand,
                "model": normalized.model,
                "category": normalized.category,
                "attributes": normalized.attributes,
                "is_accessory": normalized.is_accessory,
                "condition": normalized.condition.value,
                "price": product.price,
                "score": scored.score,
                "score_breakdown": scored.score_breakdown.model_dump(),
                "warnings": scored.warnings,
                "trust_signals": scored.trust_signals.model_dump(),
                "raw_compact": normalized.raw_compact,
                "created_at": now,
            })

        if not canonical_rows:
            return

        existing_keys = set(
            conn.execute(
                select(canonical_products.c.canonical_key).where(
                    canonical_products.c.canonical_key.in_(canonical_rows.keys())
                )
            ).scalars().all()
        )

        missing = [row for key, row in canonical_rows.items() if key not in existing_keys]
        if missing:
            conn.execute(canonical_products.insert(), missing)

        if observation_rows:
            conn.execute(transformed_product_observations.insert(), observation_rows)

    def _save_product_observations(
        self,
        conn,
        run_id: int,
        query: str,
        products: list[Product],
    ) -> None:
        seen: set[tuple[str, str]] = set()
        rows: list[dict] = []
        for product in products:
            dedupe_key = (product.store_id, str(product.product_url))
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            rows.append({
                "scrape_run_id": run_id,
                "store_id": product.store_id,
                "store_name": product.store_name,
                "query": query,
                "title": product.title,
                "price": product.price,
                "currency": product.currency,
                "original_price": product.original_price,
                "discount": product.discount,
                "installments": product.installments,
                "shipping": product.shipping,
                "seller": product.seller,
                "rating": product.rating,
                "reviews_count": product.reviews_count,
                "image_url": str(product.image_url) if product.image_url else None,
                "product_url": str(product.product_url),
                "condition": product.condition.value,
                "availability": product.availability.value,
                "sponsored": product.sponsored,
                "position": product.position,
                "scraped_at": product.scraped_at,
                "raw_metadata": product.raw_metadata,
            })
        if rows:
            conn.execute(product_observations.insert(), rows)

    @contextmanager
    def _transaction(self) -> Iterator:
        with self.engine.begin() as conn:
            yield conn

    def _enable_pgvector_if_available(self) -> None:
        if self.engine.dialect.name != "postgresql":
            return
        try:
            with self.engine.begin() as conn:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        except SQLAlchemyError:
            # The JSON fallback keeps the app usable even if pgvector is unavailable.
            return


class OptionalRepository:
    def __init__(self, repository: SearchRepository | None) -> None:
        self.repository = repository

    def init_schema(self) -> StoreError | None:
        if self.repository is None:
            return None
        try:
            self.repository.init_schema()
        except SQLAlchemyError as exc:
            return StoreError(
                store_id="persistence",
                store_name="PostgreSQL",
                message=f"No se pudo inicializar la base: {exc}",
            )
        return None

    def save_search_snapshot(
        self,
        query: str,
        postal_code: str,
        products: list[Product],
        errors: list[StoreError],
        transformed_products: list[ScoredProduct] | None = None,
    ) -> tuple[int | None, StoreError | None]:
        if self.repository is None:
            return None, None
        try:
            run_id = self.repository.save_search_snapshot(
                query,
                postal_code,
                products,
                errors,
                transformed_products,
            )
        except SQLAlchemyError as exc:
            return None, StoreError(
                store_id="persistence",
                store_name="PostgreSQL",
                message=f"No se pudo guardar el snapshot: {exc}",
            )
        return run_id, None

    def create_scrape_run(
        self,
        query: str,
        postal_code: str,
        status: str = "running",
    ) -> tuple[int | None, StoreError | None]:
        if self.repository is None:
            return None, None
        try:
            return self.repository.create_scrape_run(query, postal_code, status), None
        except SQLAlchemyError as exc:
            return None, StoreError(
                store_id="persistence",
                store_name="PostgreSQL",
                message=f"No se pudo crear la corrida: {exc}",
            )

    def append_search_run_results(
        self,
        run_id: int | None,
        query: str,
        products: list[Product],
        transformed_products: list[ScoredProduct] | None = None,
    ) -> StoreError | None:
        if self.repository is None or run_id is None:
            return None
        try:
            self.repository.append_search_run_results(
                run_id,
                query,
                products,
                transformed_products,
            )
        except SQLAlchemyError as exc:
            return StoreError(
                store_id="persistence",
                store_name="PostgreSQL",
                message=f"No se pudo guardar resultados parciales: {exc}",
            )
        return None

    def finish_scrape_run(
        self,
        run_id: int | None,
        status: str,
        errors: list[StoreError],
    ) -> StoreError | None:
        if self.repository is None or run_id is None:
            return None
        try:
            self.repository.finish_scrape_run(run_id, status, errors)
        except SQLAlchemyError as exc:
            return StoreError(
                store_id="persistence",
                store_name="PostgreSQL",
                message=f"No se pudo finalizar la corrida: {exc}",
            )
        return None

    def save_adapter_metrics(
        self,
        run_id: int | None,
        metrics: list[AdapterMetric],
    ) -> StoreError | None:
        if self.repository is None:
            return None
        try:
            self.repository.save_adapter_metrics(run_id, metrics)
        except SQLAlchemyError as exc:
            return StoreError(
                store_id="persistence",
                store_name="PostgreSQL",
                message=f"No se pudieron guardar metricas de adapters: {exc}",
            )
        return None

    def save_match_candidates(
        self,
        run_id: int | None,
        query: str,
        scored: list[ScoredProduct],
    ) -> StoreError | None:
        if self.repository is None or run_id is None:
            return None
        try:
            self.repository.save_match_candidates(run_id, query, scored)
        except SQLAlchemyError as exc:
            return StoreError(
                store_id="persistence",
                store_name="PostgreSQL",
                message=f"No se pudieron guardar candidatos de matching: {exc}",
            )
        return None

    def list_match_candidates(
        self,
        status: str = "unlabeled",
        limit: int = 50,
        query_text: str | None = None,
        run_id: int | None = None,
    ) -> tuple[list[ProductMatchCandidate], StoreError | None]:
        if self.repository is None:
            return [], StoreError(
                store_id="persistence",
                store_name="PostgreSQL",
                message="Matching no disponible: persistencia deshabilitada.",
            )
        try:
            return self.repository.list_match_candidates(status, limit, query_text, run_id), None
        except SQLAlchemyError as exc:
            return [], StoreError(
                store_id="persistence",
                store_name="PostgreSQL",
                message=f"No se pudieron listar candidatos de matching: {exc}",
            )

    def label_match_candidate(
        self,
        candidate_id: int,
        request: ProductMatchLabelRequest,
    ) -> tuple[ProductMatchLabelResponse | None, StoreError | None]:
        if self.repository is None:
            return None, StoreError(
                store_id="persistence",
                store_name="PostgreSQL",
                message="Matching no disponible: persistencia deshabilitada.",
            )
        try:
            return self.repository.label_match_candidate(candidate_id, request), None
        except SQLAlchemyError as exc:
            return None, StoreError(
                store_id="persistence",
                store_name="PostgreSQL",
                message=f"No se pudo etiquetar candidato de matching: {exc}",
            )

    def get_match_summary(self) -> tuple[ProductMatchSummary, StoreError | None]:
        if self.repository is None:
            return ProductMatchSummary(), StoreError(
                store_id="persistence",
                store_name="PostgreSQL",
                message="Matching no disponible: persistencia deshabilitada.",
            )
        try:
            return self.repository.get_match_summary(), None
        except SQLAlchemyError as exc:
            return ProductMatchSummary(), StoreError(
                store_id="persistence",
                store_name="PostgreSQL",
                message=f"No se pudo resumir matching: {exc}",
            )

    def get_history_signal(self, canonical_key: str, current_price: float | None) -> str | None:
        if self.repository is None:
            return None
        try:
            return self.repository.get_history_signal(canonical_key, current_price)
        except SQLAlchemyError:
            return None

    def get_history_baselines(self, canonical_keys: list[str]) -> dict[str, tuple[float, int]]:
        if self.repository is None:
            return {}
        try:
            return self.repository.get_history_baselines(canonical_keys)
        except SQLAlchemyError:
            return {}

    def get_run_history_items(self, run_id: int) -> tuple[list[AgentHistoryItem], StoreError | None]:
        if self.repository is None:
            return [], StoreError(
                store_id="persistence",
                store_name="PostgreSQL",
                message="Historico no disponible: persistencia deshabilitada.",
            )
        try:
            return self.repository.get_run_history_items(run_id), None
        except SQLAlchemyError as exc:
            return [], StoreError(
                store_id="persistence",
                store_name="PostgreSQL",
                message=f"No se pudo consultar historico de la corrida: {exc}",
            )

    def list_embedding_candidates(
        self,
        model: str,
        dimensions: int,
        limit: int,
        force: bool = False,
    ) -> list[EmbeddingCandidate]:
        if self.repository is None:
            return []
        try:
            return self.repository.list_embedding_candidates(model, dimensions, limit, force)
        except SQLAlchemyError:
            return []

    def used_embedding_tokens_this_month(self, model: str) -> int:
        if self.repository is None:
            return 0
        try:
            return self.repository.used_embedding_tokens_this_month(model)
        except SQLAlchemyError:
            return 0

    def save_embeddings(
        self,
        model: str,
        dimensions: int,
        embeddings: list[tuple[EmbeddingCandidate, list[float], int, float]],
    ) -> None:
        if self.repository is None:
            return
        try:
            self.repository.save_embeddings(model, dimensions, embeddings)
        except SQLAlchemyError:
            return

    def log_embedding_usage(
        self,
        model: str,
        items_processed: int,
        tokens_used: int,
        estimated_cost_usd: float,
        dry_run: bool,
        errors: list[str],
    ) -> None:
        if self.repository is None:
            return
        try:
            self.repository.log_embedding_usage(
                model,
                items_processed,
                tokens_used,
                estimated_cost_usd,
                dry_run,
                errors,
            )
        except SQLAlchemyError:
            return

    def find_semantic_match(
        self,
        canonical_key: str,
        embedding: list[float] | None,
        min_score: float = 0.82,
    ) -> SemanticMatch | None:
        if self.repository is None:
            return None
        try:
            return self.repository.find_semantic_match(canonical_key, embedding, min_score)
        except SQLAlchemyError:
            return None


def build_repository(database_url: str) -> SearchRepository:
    return SearchRepository(database_url)


def _build_match_candidate_rows(
    run_id: int,
    query: str,
    scored: list[ScoredProduct],
    max_products: int,
) -> list[dict]:
    now = datetime.now(timezone.utc)
    products = [
        item
        for item in sorted(scored, key=lambda value: value.score, reverse=True)
        if not item.normalized.is_accessory
    ][:max_products]
    rows: list[dict] = []
    for left_index, left in enumerate(products):
        for right in products[left_index + 1:]:
            if left.product.store_id == right.product.store_id:
                continue
            left_url = str(left.product.product_url)
            right_url = str(right.product.product_url)
            pair_left = left
            pair_right = right
            if (left.product.store_id, left_url) > (right.product.store_id, right_url):
                pair_left = right
                pair_right = left
            features = build_pair_features(pair_left, pair_right)
            confidence = estimate_match_confidence(features)
            if not 0.35 <= confidence <= 0.75:
                continue
            rows.append({
                "scrape_run_id": run_id,
                "query": query,
                "left_store_id": pair_left.product.store_id,
                "left_title": pair_left.product.title,
                "left_product_url": str(pair_left.product.product_url),
                "left_canonical_key": pair_left.normalized.canonical_key,
                "left_price": pair_left.product.price,
                "right_store_id": pair_right.product.store_id,
                "right_title": pair_right.product.title,
                "right_product_url": str(pair_right.product.product_url),
                "right_canonical_key": pair_right.normalized.canonical_key,
                "right_price": pair_right.product.price,
                "features": features.model_dump(),
                "match_confidence": confidence,
                "label": None,
                "created_at": now,
                "updated_at": now,
            })
    rows.sort(key=lambda row: abs(row["match_confidence"] - 0.5))
    return rows


def _match_candidate_from_row(row, prediction: dict | None = None) -> ProductMatchCandidate:
    base_features = ProductPairFeatures(**(row["features"] or {}))
    features = build_pair_features_from_values(
        left_title=row["left_title"],
        right_title=row["right_title"],
        left_price=row["left_price"],
        right_price=row["right_price"],
        left_canonical_key=row["left_canonical_key"],
        right_canonical_key=row["right_canonical_key"],
        base_features=base_features,
    )
    return ProductMatchCandidate(
        id=row["id"],
        run_id=row["scrape_run_id"],
        query=row["query"],
        left_store_id=row["left_store_id"],
        left_title=row["left_title"],
        left_product_url=row["left_product_url"],
        left_canonical_key=row["left_canonical_key"],
        left_price=row["left_price"],
        right_store_id=row["right_store_id"],
        right_title=row["right_title"],
        right_product_url=row["right_product_url"],
        right_canonical_key=row["right_canonical_key"],
        right_price=row["right_price"],
        features=features,
        match_confidence=row["match_confidence"],
        label=row["label"],
        model_match_probability=prediction["match_probability"] if prediction else None,
        model_decision=prediction["decision"] if prediction else None,
        model_version=prediction["model_version"] if prediction else None,
    )


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = sum(a * a for a in left) ** 0.5
    right_norm = sum(b * b for b in right) ** 0.5
    if not left_norm or not right_norm:
        return 0
    return dot / (left_norm * right_norm)


def _history_signal_from_baseline(
    baseline: tuple[float, int] | None,
    current_price: float | None,
) -> str | None:
    if baseline is None or current_price is None:
        return None
    average, count = baseline
    if count < 2:
        return None
    if current_price <= average * 0.9:
        return "below_recent_average"
    if current_price >= average * 1.1:
        return "above_recent_average"
    return "near_recent_average"
