from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query

from app.config import Settings, get_settings
from app.database import OptionalRepository, PersistenceWorker, SearchRepository, build_repository
from app.models import (
    AgentHistoryResponse,
    AgentSearchResponse,
    ProductMatchCandidate,
    ProductMatchLabelRequest,
    ProductMatchLabelResponse,
    ProductMatchSummary,
    SearchMode,
    SearchEverywhereResponse,
    StoreError,
)
from app.scrapers.registry import build_store_registry
from app.scrapers.url_analyzer import ProductAnalysis, analyze_url
from app.services import SearchService


class AppContainer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.http_client: httpx.AsyncClient | None = None
        self.registry = {}
        self.adapters = []
        self.configure_store_registry()
        self.repository = build_repository(settings.database_url)
        self.optional_repository = OptionalRepository(self.repository)
        self.startup_errors: list[StoreError] = []
        self.persistence_worker: PersistenceWorker | None = None

    def configure_store_registry(self) -> None:
        self.registry = build_store_registry(self.http_client)
        self.adapters = [
            adapter
            for store_id, adapter in self.registry.items()
            if store_id in self.settings.active_store_ids
        ]

    def search_service(self) -> SearchService:
        return SearchService(
            adapters=self.adapters,
            repository=self.optional_repository,
            location=self.settings.default_location,
            semantic_enabled=self.settings.embeddings_enabled and bool(self.settings.openai_api_key),
            worker=self.persistence_worker,
        )


container = AppContainer(get_settings())


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    container.http_client = httpx.AsyncClient(
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        timeout=httpx.Timeout(8.0, connect=3.0, read=8.0, write=3.0, pool=1.0),
    )
    container.configure_store_registry()
    schema_error = container.optional_repository.init_schema()
    if schema_error:
        container.startup_errors.append(schema_error)
    else:
        store_map = {adapter.store_id: adapter.store_name for adapter in container.adapters}
        container.repository.seed_stores(store_map)

    container.persistence_worker = PersistenceWorker(container.optional_repository)
    await container.persistence_worker.start()

    try:
        yield
    finally:
        if container.persistence_worker:
            await container.persistence_worker.stop()
        if container.http_client:
            await container.http_client.aclose()


app = FastAPI(
    title="Multi Store ETL API",
    version="0.2.0",
    description="API modular para buscar productos en tiendas online de Argentina.",
    lifespan=lifespan,
)


def get_search_service() -> SearchService:
    return container.search_service()


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok" if not container.startup_errors else "degraded",
        "stores": [adapter.store_id for adapter in container.adapters],
        "startup_errors": [error.model_dump() for error in container.startup_errors],
    }


@app.get("/agent/search", response_model=AgentSearchResponse)
async def agent_search(
    query: str = Query(..., min_length=2, max_length=120),
    limit: int = Query(3, ge=1, le=10),
    mode: SearchMode = Query(SearchMode.INTERACTIVE),
    stores: str | None = Query(None),
    max_price_ars: float | None = Query(None),
    min_price_ars: float | None = Query(None),
    service: SearchService = Depends(get_search_service),
) -> AgentSearchResponse:
    stores_list = [s.strip() for s in stores.split(",")] if stores else None
    return await service.agent_search(query=query, limit=limit, mode=mode, stores=stores_list, max_price_ars=max_price_ars, min_price_ars=min_price_ars)


@app.get("/agent/search/{run_id}/history", response_model=AgentHistoryResponse)
async def agent_search_history(
    run_id: int,
    service: SearchService = Depends(get_search_service),
) -> AgentHistoryResponse:
    return await service.agent_search_history(run_id)


@app.get("/agent/search-everywhere", response_model=SearchEverywhereResponse)
async def agent_search_everywhere(
    query: str = Query(..., min_length=2, max_length=120),
    url: str | None = Query(None),
    limit: int = Query(10, ge=1, le=50),
    max_price_ars: float | None = Query(None, gt=0),
    min_price_ars: float | None = Query(None, gt=0),
    strict: bool = Query(False),
    service: SearchService = Depends(get_search_service),
) -> SearchEverywhereResponse:
    return await service.search_everywhere(
        query=query,
        target_url=url,
        limit=limit,
        max_price_ars=max_price_ars,
        min_price_ars=min_price_ars,
        strict=strict
    )


@app.get("/internal/matching/candidates", response_model=list[ProductMatchCandidate])
async def list_match_candidates(
    status: str = Query("unlabeled", pattern="^(unlabeled|labeled|all)$"),
    limit: int = Query(50, ge=1, le=200),
    query: str | None = Query(None, min_length=2, max_length=120),
    run_id: int | None = Query(None, ge=1),
    service: SearchService = Depends(get_search_service),
) -> list[ProductMatchCandidate]:
    try:
        return await service.list_match_candidates(
            status=status,
            limit=limit,
            query_text=query,
            run_id=run_id,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post(
    "/internal/matching/candidates/{candidate_id}/label",
    response_model=ProductMatchLabelResponse,
)
async def label_match_candidate(
    candidate_id: int,
    request: ProductMatchLabelRequest,
    service: SearchService = Depends(get_search_service),
) -> ProductMatchLabelResponse:
    try:
        response = await service.label_match_candidate(candidate_id, request)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if response is None:
        raise HTTPException(status_code=404, detail="Matching candidate not found.")
    return response


@app.get("/internal/matching/summary", response_model=ProductMatchSummary)
async def match_summary(
    service: SearchService = Depends(get_search_service),
) -> ProductMatchSummary:
    try:
        return await service.match_summary()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/agent/analyze-url", response_model=ProductAnalysis)
async def agent_analyze_url(
    url: str = Query(..., min_length=10, max_length=500),
) -> ProductAnalysis:
    return await analyze_url(url)
