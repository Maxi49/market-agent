from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator
from typing import Any

_logger = logging.getLogger(__name__)

from app.database import (
    AppendResultsJob,
    FinishRunJob,
    GenerateMatchCandidatesJob,
    OptionalRepository,
    PersistenceWorker,
    SaveMetricsJob,
    SaveSnapshotJob,
)
from app.link_guard import LinkGuard, ProductLinkGuard
from app.models import (
    AgentHistoryResponse,
    AdapterMetric,
    AgentMatch,
    AgentSearchResponse,
    HistoryStatus,
    HistoryStatusValue,
    Product,
    ProductMatchCandidate,
    ProductMatchLabelRequest,
    ProductMatchLabelResponse,
    ProductMatchSummary,
    ScrapeRunSummary,
    ScoredProduct,
    SearchLocation,
    SearchMode,
    SearchResponse,
    StoreError,
)
from app.normalization import ProductNormalizer
from app.ranking import rank_products
from app.routing import StoreRouter, normalize_text
from app.scoring import ProductScorer
from app.semantic import SemanticMatcher
from app.scrapers.base import ScraperError, StoreAdapter

INTERACTIVE_STORE_TIMEOUT_SECONDS = 15
DEEP_STORE_TIMEOUT_SECONDS = 30
STREAM_GLOBAL_TIMEOUT_SECONDS = 50


class SearchService:
    def __init__(
        self,
        adapters: list[StoreAdapter],
        repository: OptionalRepository,
        location: SearchLocation,
        semantic_enabled: bool = False,
        worker: PersistenceWorker | None = None,
        link_guard: LinkGuard | None = None,
    ) -> None:
        self.adapters = adapters
        self.adapter_by_id = {adapter.store_id: adapter for adapter in adapters}
        self.repository = repository
        self.location = location
        self.normalizer = ProductNormalizer()
        self.scorer = ProductScorer()
        self.semantic_matcher = SemanticMatcher(repository)
        self.semantic_enabled = semantic_enabled
        self.worker = worker
        self.link_guard = link_guard or ProductLinkGuard()

    async def search(
        self,
        query: str,
        limit: int,
        mode: SearchMode = SearchMode.INTERACTIVE,
        save_snapshot: bool = True,
    ) -> SearchResponse:
        products, errors, metrics = await self._collect_products(
            query=query,
            per_store_limit=_per_store_limit(limit, mode),
            mode=mode,
        )
        ranked = rank_products(query, products, limit)

        if save_snapshot:
            if self.worker is not None:
                self.worker.enqueue(SaveSnapshotJob(
                    query=query,
                    postal_code=self.location.postal_code,
                    products=products,
                    errors=errors,
                    metrics=metrics,
                ))
            else:
                run_id, persistence_error = await asyncio.to_thread(
                    self.repository.save_search_snapshot,
                    query,
                    self.location.postal_code,
                    products,
                    errors,
                )
                if persistence_error:
                    errors.append(persistence_error)
                elif run_id is not None:
                    metric_error = await asyncio.to_thread(
                        self.repository.save_adapter_metrics,
                        run_id,
                        metrics,
                    )
                    if metric_error:
                        errors.append(metric_error)

        return SearchResponse(query=query, count=len(ranked), results=ranked, errors=errors)

    async def run_tracked_query(self, query: str, limit: int) -> ScrapeRunSummary:
        products, errors, metrics = await self._collect_products(
            query=query,
            per_store_limit=limit,
            mode=SearchMode.DEEP,
        )
        run_id, persistence_error = await asyncio.to_thread(
            self.repository.save_search_snapshot,
            query,
            self.location.postal_code,
            products,
            errors,
        )
        if persistence_error:
            errors.append(persistence_error)
        else:
            metric_error = await asyncio.to_thread(
                self.repository.save_adapter_metrics,
                run_id,
                metrics,
            )
            if metric_error:
                errors.append(metric_error)
        return ScrapeRunSummary(
            run_id=run_id,
            query=query,
            results_count=len(products),
            errors=errors,
        )

    async def agent_search(
        self,
        query: str,
        limit: int,
        mode: SearchMode = SearchMode.INTERACTIVE,
        stores: list[str] | None = None,
        max_price_ars: float | None = None,
        min_price_ars: float | None = None,
    ) -> AgentSearchResponse:
        router = StoreRouter(list(self.adapter_by_id))
        routing = router.route(query)
        routing = _routing_with_store_override(routing, stores, self.adapter_by_id)
        selected_store_ids = routing.selected_store_ids
        selected_adapters = [
            self.adapter_by_id[store_id]
            for store_id in selected_store_ids
            if store_id in self.adapter_by_id
        ]

        run_id: int | None = None
        errors: list[StoreError] = []

        if self.worker is not None:
            run_id, create_error = await asyncio.to_thread(
                self.repository.create_scrape_run,
                query,
                self.location.postal_code,
                "running",
            )
            if create_error:
                errors.append(create_error)

        products, collect_errors, metrics = await self._collect_products(
            query=query,
            per_store_limit=_per_store_limit(limit, mode),
            adapters=selected_adapters,
            mode=mode,
        )
        errors.extend(collect_errors)

        scored = await self._score_products(
            query,
            routing.query_understanding,
            products,
            include_history=mode == SearchMode.DEEP,
        )
        ranked = sorted(scored, key=lambda item: item.score, reverse=True)

        if self.worker is not None:
            status = "completed_with_errors" if errors else "completed"
            self.worker.enqueue(AppendResultsJob(run_id=run_id, query=query, products=products, scored=ranked))
            self.worker.enqueue(GenerateMatchCandidatesJob(run_id=run_id, query=query, scored=ranked))
            self.worker.enqueue(SaveMetricsJob(run_id=run_id, metrics=metrics))
            self.worker.enqueue(FinishRunJob(run_id=run_id, status=status, errors=errors))
        else:
            run_id, persistence_error = await asyncio.to_thread(
                self.repository.save_search_snapshot,
                query,
                self.location.postal_code,
                products,
                errors,
                ranked,
            )
            if persistence_error:
                errors.append(persistence_error)
            elif run_id is not None:
                metric_error = await asyncio.to_thread(
                    self.repository.save_adapter_metrics,
                    run_id,
                    metrics,
                )
                if metric_error:
                    errors.append(metric_error)
            if run_id is not None:
                match_error = await asyncio.to_thread(
                    self.repository.save_match_candidates,
                    run_id,
                    query,
                    ranked,
                )
                if match_error:
                    errors.append(match_error)

        warnings = self._global_warnings(ranked, errors, routing.query_understanding)
        if self.semantic_enabled and not self._semantic_available(ranked):
            warnings.append("semantic_search_unavailable")
        # No filtramos accesorios aquí — el warning ya está en scored.warnings
        # (→ risks en AgentMatch) y el LLM puede decidir ignorarlos.
        agent_candidates = ranked
        if max_price_ars is not None:
            filtered = [
                sp for sp in agent_candidates
                if sp.product.currency != "$" or (sp.product.price or 0) <= max_price_ars * 1.15
            ]
            if not filtered:
                warnings.append(f"no_results_within_budget:{int(max_price_ars)}")
            else:
                agent_candidates = filtered
        if min_price_ars is not None:
            price_floor = min_price_ars * 0.80
            filtered_min = [
                sp for sp in agent_candidates
                if sp.product.currency != "$" or (sp.product.price or 0) >= price_floor
            ]
            if not filtered_min:
                warnings.append(f"no_results_above_minimum:{int(min_price_ars)}")
            else:
                agent_candidates = filtered_min
        selected_candidates = await self.link_guard.guard(agent_candidates[:limit])
        return AgentSearchResponse(
            query=query,
            debug_ref=run_id,
            routing=routing,
            query_understanding=routing.query_understanding,
            best_matches=[
                self._to_agent_match(scored_product)
                for scored_product in selected_candidates
            ],
            history_status=_history_status(mode, run_id),
            warnings=warnings,
            errors=errors,
        )

    async def agent_search_history(self, run_id: int) -> AgentHistoryResponse:
        items, error = await asyncio.to_thread(self.repository.get_run_history_items, run_id)
        errors = [error] if error else []
        return AgentHistoryResponse(
            run_id=run_id,
            count=len(items),
            items=items,
            errors=errors,
        )

    async def list_match_candidates(
        self,
        status: str = "unlabeled",
        limit: int = 50,
        query_text: str | None = None,
        run_id: int | None = None,
    ) -> list[ProductMatchCandidate]:
        candidates, error = await asyncio.to_thread(
            self.repository.list_match_candidates,
            status,
            limit,
            query_text,
            run_id,
        )
        if error:
            raise RuntimeError(error.message)
        return candidates

    async def label_match_candidate(
        self,
        candidate_id: int,
        request: ProductMatchLabelRequest,
    ) -> ProductMatchLabelResponse | None:
        response, error = await asyncio.to_thread(
            self.repository.label_match_candidate,
            candidate_id,
            request,
        )
        if error:
            raise RuntimeError(error.message)
        return response

    async def match_summary(self) -> ProductMatchSummary:
        summary, error = await asyncio.to_thread(self.repository.get_match_summary)
        if error:
            raise RuntimeError(error.message)
        return summary

    async def agent_search_events(
        self,
        query: str,
        limit: int,
        mode: SearchMode = SearchMode.INTERACTIVE,
        stores: list[str] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        producer = asyncio.create_task(self._produce_agent_search_events(query, limit, mode, queue, stores))
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
        finally:
            if producer.done():
                producer.result()
            else:
                producer.add_done_callback(_consume_task_exception)

    async def _produce_agent_search_events(
        self,
        query: str,
        limit: int,
        mode: SearchMode,
        queue: asyncio.Queue[dict[str, Any] | None],
        stores: list[str] | None = None,
    ) -> None:
        all_products: list[Product] = []
        all_scored: list[ScoredProduct] = []
        errors: list[StoreError] = []
        timed_out = False

        router = StoreRouter(list(self.adapter_by_id))
        routing = router.route(query)
        routing = _routing_with_store_override(routing, stores, self.adapter_by_id)
        await queue.put(_event("routing", routing.model_dump(mode="json")))

        selected_store_ids = routing.selected_store_ids
        selected_adapters = [
            self.adapter_by_id[store_id]
            for store_id in selected_store_ids
            if store_id in self.adapter_by_id
        ]
        per_store_limit = _per_store_limit(limit, mode)
        tasks = [
            asyncio.create_task(
                self._search_adapter_timed(adapter, query, per_store_limit, mode),
                name=adapter.store_id,
            )
            for adapter in selected_adapters
        ]

        # create_scrape_run corre en paralelo con los scrapers — para cuando
        # termine el primer adapter (~2s), el INSERT (~500ms) ya habrá vuelto.
        run_id_task: asyncio.Task | None = None
        run_id: int | None = None
        if self.worker is not None:
            run_id_task = asyncio.create_task(
                asyncio.to_thread(
                    self.repository.create_scrape_run,
                    query,
                    self.location.postal_code,
                    "running",
                )
            )
        else:
            run_id, persistence_error = await asyncio.to_thread(
                self.repository.create_scrape_run,
                query,
                self.location.postal_code,
                "running",
            )
            if persistence_error:
                errors.append(persistence_error)
                await queue.put(_event("error", persistence_error.model_dump(mode="json")))

        for adapter in selected_adapters:
            await queue.put(
                _event(
                    "store_started",
                    {"store_id": adapter.store_id, "store_name": adapter.store_name},
                )
            )

        try:
            for next_result in asyncio.as_completed(tasks, timeout=STREAM_GLOBAL_TIMEOUT_SECONDS):
                store_id, store_name, products, store_error, metric = await next_result
                if store_error:
                    errors.append(store_error)
                    await queue.put(_event("error", {**store_error.model_dump(mode="json"), "metric": metric.model_dump(mode="json")}))

                all_products.extend(products)
                scored = await self._score_products(
                    query,
                    routing.query_understanding,
                    products,
                    include_history=mode == SearchMode.DEEP,
                )
                all_scored.extend(scored)
                ranked_store = sorted(scored, key=lambda item: item.score, reverse=True)

                await queue.put(
                    _event(
                        "store_done",
                        {
                            "store_id": store_id,
                            "store_name": store_name,
                            "count": len(products),
                            "elapsed_ms": metric.elapsed_ms,
                            "mode": metric.mode.value,
                            "strategy": metric.strategy,
                            "products_count": metric.products_count,
                            "status": metric.status,
                            "error_type": metric.error_type,
                        },
                    )
                )
                for scored_product in [
                    item for item in ranked_store if _is_agent_candidate(item, routing.query_understanding)
                ][:limit]:
                    await queue.put(
                        _event(
                            "match",
                            self._to_agent_match(scored_product).model_dump(mode="json"),
                        )
                    )

                if self.worker is not None:
                    if run_id_task is not None and not run_id_task.done():
                        run_id, persistence_error = await run_id_task
                        run_id_task = None
                        if persistence_error:
                            errors.append(persistence_error)
                    elif run_id_task is not None and run_id is None:
                        run_id, persistence_error = run_id_task.result()
                        run_id_task = None
                        if persistence_error:
                            errors.append(persistence_error)
                    self.worker.enqueue(AppendResultsJob(run_id=run_id, query=query, products=products, scored=ranked_store))
                    self.worker.enqueue(SaveMetricsJob(run_id=run_id, metrics=[metric]))
                else:
                    persistence_error = await asyncio.to_thread(
                        self.repository.append_search_run_results,
                        run_id,
                        query,
                        products,
                        ranked_store,
                    )
                    metric_error = await asyncio.to_thread(
                        self.repository.save_adapter_metrics,
                        run_id,
                        [metric],
                    )
                    if metric_error:
                        errors.append(metric_error)
                        await queue.put(_event("error", metric_error.model_dump(mode="json")))
                    if persistence_error:
                        errors.append(persistence_error)
                        await queue.put(_event("error", persistence_error.model_dump(mode="json")))
        except TimeoutError:
            timed_out = True
            for task in tasks:
                if not task.done():
                    task.cancel()
            for adapter in selected_adapters:
                if any(task.get_name() == adapter.store_id and not task.done() for task in tasks):
                    error = StoreError(
                        store_id=adapter.store_id,
                        store_name=adapter.store_name,
                        message="Timeout global de busqueda.",
                    )
                    errors.append(error)
                    await queue.put(_event("error", error.model_dump(mode="json")))

        # Asegurar que run_id esté resuelto (edge case: 0 stores seleccionados o timeout muy rápido)
        if run_id_task is not None:
            run_id, _ = await run_id_task
            run_id_task = None

        ranked = sorted(all_scored, key=lambda item: item.score, reverse=True)
        warnings = self._global_warnings(ranked, errors, routing.query_understanding)
        if timed_out:
            warnings.append("La busqueda se devolvio con resultados parciales por timeout.")
        if self.semantic_enabled and not self._semantic_available(ranked):
            warnings.append("semantic_search_unavailable")
        agent_candidates = ranked  # el LLM filtra por risks/warnings
        selected_candidates = await self.link_guard.guard(agent_candidates[:limit])
        response = AgentSearchResponse(
            query=query,
            debug_ref=run_id,
            routing=routing,
            query_understanding=routing.query_understanding,
            best_matches=[
                self._to_agent_match(scored_product)
                for scored_product in selected_candidates
            ],
            history_status=_history_status(mode, run_id),
            warnings=warnings,
            errors=errors,
        )

        for warning in warnings:
            await queue.put(_event("warning", {"message": warning}))
        await queue.put(_event("final", response.model_dump(mode="json")))
        await queue.put(None)

        status = "timeout_partial" if timed_out else ("completed_with_errors" if errors else "completed")
        if self.worker is not None:
            self.worker.enqueue(GenerateMatchCandidatesJob(run_id=run_id, query=query, scored=ranked))
            self.worker.enqueue(FinishRunJob(run_id=run_id, status=status, errors=errors))
        else:
            match_error = await asyncio.to_thread(
                self.repository.save_match_candidates,
                run_id,
                query,
                ranked,
            )
            if match_error:
                _logger.error("save_match_candidates error: %s", match_error)
            persistence_error = await asyncio.to_thread(
                self.repository.finish_scrape_run,
                run_id,
                status,
                errors,
            )
            if persistence_error:
                _logger.error("finish_scrape_run error: %s", persistence_error)

    async def _collect_products(
        self,
        query: str,
        per_store_limit: int,
        adapters: list[StoreAdapter] | None = None,
        mode: SearchMode = SearchMode.INTERACTIVE,
    ) -> tuple[list[Product], list[StoreError], list[AdapterMetric]]:
        selected_adapters = adapters if adapters is not None else self.adapters
        tasks = [
            self._search_adapter_timed(adapter, query=query, limit=per_store_limit, mode=mode)
            for adapter in selected_adapters
        ]
        results = await asyncio.gather(*tasks)
        products: list[Product] = []
        errors: list[StoreError] = []
        metrics: list[AdapterMetric] = []

        for _, _, adapter_products, adapter_error, metric in results:
            products.extend(adapter_products)
            metrics.append(metric)
            if adapter_error:
                errors.append(adapter_error)

        return products, errors, metrics

    async def _score_products(
        self,
        query: str,
        query_understanding,
        products: list[Product],
        include_history: bool,
    ) -> list[ScoredProduct]:
        normalized_pairs = [
            (product, self.normalizer.normalize(product, query_understanding))
            for product in products
        ]
        comparable_prices = [
            product.price
            for product, normalized in normalized_pairs
            if product.price is not None and (
                not normalized.is_accessory or _query_allows_accessories(query_understanding)
            )
        ]
        if include_history:
            history_baselines = await asyncio.to_thread(
                self.repository.get_history_baselines,
                [normalized.canonical_key for _, normalized in normalized_pairs],
            )
        else:
            history_baselines = {}

        scored: list[ScoredProduct] = []
        for product, normalized in normalized_pairs:
            history_signal = _history_signal(
                history_baselines.get(normalized.canonical_key),
                product.price,
            )
            scored.append(
                self.scorer.score(
                    query=query,
                    query_understanding=query_understanding,
                    product=product,
                    normalized=normalized,
                    comparable_prices=comparable_prices,
                    history_signal=history_signal,
                )
            )
        return scored

    def _to_agent_match(self, scored: ScoredProduct) -> AgentMatch:
        product = scored.product
        return AgentMatch(
            normalized_name=scored.normalized.normalized_title,
            store_id=product.store_id,
            store_name=product.store_name,
            title=product.title,
            price=product.price,
            currency=product.currency,
            price_ars=_price_ars(product),
            price_usd=_price_usd(product),
            product_url=product.product_url,
            image_url=product.image_url,
            score=scored.score,
            score_breakdown=scored.score_breakdown,
            explanation=scored.explanation,
            risks=scored.warnings,
            trust_signals=scored.trust_signals,
            historical_signal=scored.trust_signals.history_signal,
            semantic_match=self.semantic_matcher.match(scored) if self.semantic_enabled else None,
        )

    def _global_warnings(
        self,
        scored: list[ScoredProduct],
        errors: list[StoreError],
        query_understanding=None,
    ) -> list[str]:
        warnings: list[str] = []
        if errors:
            warnings.append("Algunas tiendas no pudieron consultarse.")
        # Solo advertir si HAY productos pero todos son accesorios (no cuando scored está vacío,
        # ya que eso dispara un falso positivo cuando el scraper no devuelve nada).
        if (
            scored
            and not _query_allows_accessories(query_understanding)
            and not any(not item.normalized.is_accessory for item in scored)
        ):
            warnings.append("No se encontraron candidatos claros que no sean accesorios.")
        if any(item.product.price is None for item in scored):
            warnings.append("Algunos resultados no tienen precio detectable.")
        return warnings

    def _semantic_available(self, scored: list[ScoredProduct]) -> bool:
        return any(self.semantic_matcher.match(item) is not None for item in scored)

    async def _search_adapter(
        self,
        adapter: StoreAdapter,
        query: str,
        limit: int,
        mode: SearchMode,
    ) -> tuple[list[Product], StoreError | None]:
        try:
            products = await adapter.search(query, limit, self.location, mode=mode)
        except ScraperError as exc:
            return [], StoreError(
                store_id=adapter.store_id,
                store_name=adapter.store_name,
                message=str(exc),
            )
        except Exception as exc:
            return [], StoreError(
                store_id=adapter.store_id,
                store_name=adapter.store_name,
                message=f"Error inesperado: {exc}",
            )
        return products, None

    async def _search_adapter_timed(
        self,
        adapter: StoreAdapter,
        query: str,
        limit: int,
        mode: SearchMode,
    ) -> tuple[str, str, list[Product], StoreError | None, AdapterMetric]:
        started = time.perf_counter()
        strategy = _adapter_strategy(adapter.store_id, mode)
        error_type = None
        try:
            products, error = await asyncio.wait_for(
                self._search_adapter(adapter, query, limit, mode),
                timeout=_store_timeout(mode),
            )
        except TimeoutError:
            products = []
            error_type = "timeout"
            error = StoreError(
                store_id=adapter.store_id,
                store_name=adapter.store_name,
                message="Timeout consultando tienda.",
            )
        if error and error_type is None:
            error_type = "scraper_error"
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        metric = AdapterMetric(
            store_id=adapter.store_id,
            store_name=adapter.store_name,
            query=query,
            mode=mode,
            strategy=strategy,
            elapsed_ms=elapsed_ms,
            status="error" if error else "ok",
            products_count=len(products),
            error_type=error_type,
        )
        return adapter.store_id, adapter.store_name, products, error, metric


def _per_store_limit(limit: int, mode: SearchMode) -> int:
    if mode == SearchMode.DEEP:
        return limit
    return min(12, max(4, limit * 3))


def _store_timeout(mode: SearchMode) -> int:
    return DEEP_STORE_TIMEOUT_SECONDS if mode == SearchMode.DEEP else INTERACTIVE_STORE_TIMEOUT_SECONDS


def _adapter_strategy(store_id: str, mode: SearchMode) -> str:
    if store_id in {"carrefour_ar", "samsung_ar"}:
        return "vtex_json_parallel" if mode == SearchMode.INTERACTIVE else "vtex_json_then_html"
    return "html"


def _history_status(mode: SearchMode, run_id: int | None) -> HistoryStatus:
    if mode == SearchMode.DEEP:
        return HistoryStatus(
            status=HistoryStatusValue.INCLUDED,
            reason="Historico incluido en el scoring deep.",
        )
    if run_id is None:
        return HistoryStatus(
            status=HistoryStatusValue.UNAVAILABLE,
            reason="No hay debug_ref persistido para consultar historico.",
        )
    return HistoryStatus(
        status=HistoryStatusValue.AVAILABLE_ON_DEMAND,
        lookup_url=f"/agent/search/{run_id}/history",
        reason="Historico omitido del path interactivo para reducir latencia.",
    )


def _history_signal(
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


def _price_ars(product: Product) -> float | None:
    metadata_price = _metadata_number(product.raw_metadata.get("price_ars"))
    if metadata_price is not None:
        return metadata_price
    if product.currency in {"$", "ARS"}:
        return product.price
    return None


def _price_usd(product: Product) -> float | None:
    metadata_price = _metadata_number(product.raw_metadata.get("price_usd"))
    if metadata_price is not None:
        return metadata_price
    if product.currency == "USD":
        return product.price
    return None


def _metadata_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _is_agent_candidate(scored: ScoredProduct, query_understanding) -> bool:
    if scored.normalized.is_accessory and not _query_allows_accessories(query_understanding):
        return False
    return True


def _query_allows_accessories(query_understanding) -> bool:
    return getattr(query_understanding, "detected_category", None) == "accessories"



def _routing_with_store_override(routing, stores: list[str] | None, adapter_by_id: dict[str, StoreAdapter]):
    if not stores:
        return routing

    requested = [store_id for store_id in stores if store_id in adapter_by_id]
    requested_set = set(requested)
    original_store_ids = [*routing.selected_store_ids, *routing.excluded_store_ids]
    excluded = [store_id for store_id in original_store_ids if store_id not in requested_set]
    reasons = dict(routing.reasons)
    for store_id in routing.selected_store_ids:
        if store_id not in requested_set:
            reasons[store_id] = "excluded_by_store_override"

    return routing.model_copy(
        update={
            "selected_store_ids": requested,
            "excluded_store_ids": excluded,
            "reasons": reasons,
        }
    )


def _query_model_constraint(normalized_query: str, query_brands: set[str]) -> str | None:
    if "apple" in query_brands:
        iphone = re.search(r"\biphone\s+\d{1,2}(?:\s+(?:pro|max|plus))*\b", normalized_query)
        if iphone:
            return iphone.group(0)
    if "samsung" in query_brands:
        galaxy = re.search(r"\bgalaxy\s+[a-z]?\d{1,3}\+?(?:\s+(?:fe|ultra|plus))*\b", normalized_query)
        if galaxy:
            return galaxy.group(0)
    return None


def _event(event_type: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"event": event_type, "data": data}


def _consume_task_exception(task: asyncio.Task) -> None:
    try:
        task.result()
    except Exception:
        return
