import asyncio
from dataclasses import dataclass

from app.database import (
    OptionalRepository,
    SearchRepository,
    product_match_candidates,
    scrape_adapter_metrics,
    transformed_product_observations,
)
from app.main import app, get_search_service
from app.models import Product, ProductAvailability, ProductCondition, SearchLocation, SearchMode
from app.routing import understand_query
from app.services import SearchService
from tests.typing_helpers import http_url


@dataclass
class FakeAdapter:
    store_id: str
    store_name: str
    products: list[Product]
    delay_seconds: float = 0

    async def search(
        self,
        query: str,
        limit: int,
        location: SearchLocation,
        mode: SearchMode = SearchMode.INTERACTIVE,
    ) -> list[Product]:
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        return self.products[:limit]


@dataclass
class PositionalLocationAdapter:
    store_id: str
    store_name: str
    products: list[Product]

    async def search(
        self,
        query: str,
        limit: int,
        location: SearchLocation,
        /,
        mode: SearchMode = SearchMode.INTERACTIVE,
    ) -> list[Product]:
        return self.products[:limit]


class CountingRepository(SearchRepository):
    def __init__(self) -> None:
        super().__init__("sqlite+pysqlite:///:memory:")
        self.history_baselines_calls = 0

    def get_history_baselines(self, canonical_keys):
        self.history_baselines_calls += 1
        return super().get_history_baselines(canonical_keys)


class ReplacingLinkGuard:
    async def guard(self, scored_products):
        guarded = []
        for scored in scored_products:
            if scored.product.store_id == "fravega":
                guarded.append(scored.model_copy(update={
                    "product": scored.product.model_copy(update={
                        "product_url": http_url("https://www.fravega.com/l/?keyword=Apple+iPhone+15+128GB"),
                    }),
                    "warnings": [*scored.warnings, "link_dead_fallback:fravega"],
                }))
            else:
                guarded.append(scored)
        return guarded


def make_product(
    store_id: str,
    store_name: str,
    title: str,
    price: float,
    position: int,
) -> Product:
    return Product(
        store_id=store_id,
        store_name=store_name,
        position=position,
        title=title,
        price=price,
        currency="$",
        product_url=http_url(f"https://example.com/{store_id}/{position}"),
        condition=ProductCondition.NEW,
        availability=ProductAvailability.IN_STOCK,
    )


def test_agent_search_returns_opinionated_response_and_persists_transforms() -> None:
    repository = SearchRepository("sqlite+pysqlite:///:memory:")
    repository.init_schema()
    repository.seed_stores(
        {
            "mercado_libre": "Mercado Libre",
            "fravega": "Fravega",
            "samsung_ar": "Samsung Argentina",
            "carrefour_ar": "Carrefour Argentina",
        }
    )
    service = SearchService(
        adapters=[
            FakeAdapter(
                "mercado_libre",
                "Mercado Libre",
                [make_product("mercado_libre", "Mercado Libre", "Apple iPhone 15 128GB", 900000, 1)],
            ),
            FakeAdapter(
                "fravega",
                "Fravega",
                [make_product("fravega", "Fravega", "Funda Spigen iPhone 15", 10000, 1)],
            ),
            FakeAdapter(
                "samsung_ar",
                "Samsung Argentina",
                [make_product("samsung_ar", "Samsung Argentina", "Galaxy S24", 1200000, 1)],
            ),
            FakeAdapter(
                "carrefour_ar",
                "Carrefour Argentina",
                [make_product("carrefour_ar", "Carrefour Argentina", "Celular iPhone 15 128gb negro", 950000, 1)],
            ),
        ],
        repository=OptionalRepository(repository),
        location=SearchLocation(),
    )

    response = asyncio.run(service.agent_search("iphone 15", limit=2))

    assert response.debug_ref == 1
    assert "samsung_ar" not in response.routing.selected_store_ids
    assert "carrefour_ar" not in response.routing.selected_store_ids
    assert [match.store_id for match in response.best_matches] == ["mercado_libre"]
    assert response.best_matches[0].score > 0
    assert response.best_matches[0].explanation
    assert response.best_matches[0].semantic_match is None
    assert response.history_status.status == "available_on_demand"
    assert response.history_status.lookup_url == "/agent/search/1/history"
    assert "semantic_search_unavailable" not in response.warnings

    with repository.engine.connect() as conn:
        transformed = conn.execute(transformed_product_observations.select()).all()
        metrics = conn.execute(scrape_adapter_metrics.select()).all()

    assert len(transformed) == 2
    assert len(metrics) == 2


def test_agent_search_guards_best_match_links_before_returning_response() -> None:
    repository = SearchRepository("sqlite+pysqlite:///:memory:")
    repository.init_schema()
    repository.seed_stores({"fravega": "Fravega"})
    service = SearchService(
        adapters=[
            FakeAdapter(
                "fravega",
                "Fravega",
                [make_product("fravega", "Fravega", "Apple iPhone 15 128GB", 950000, 1)],
            ),
        ],
        repository=OptionalRepository(repository),
        location=SearchLocation(),
        link_guard=ReplacingLinkGuard(),
    )

    response = asyncio.run(service.agent_search("iphone 15", limit=1, stores=["fravega"]))

    assert str(response.best_matches[0].product_url) == "https://www.fravega.com/l/?keyword=Apple+iPhone+15+128GB"
    assert "link_dead_fallback:fravega" in response.best_matches[0].risks


def test_agent_search_calls_adapters_with_positional_location() -> None:
    repository = SearchRepository("sqlite+pysqlite:///:memory:")
    repository.init_schema()
    repository.seed_stores({"fravega": "Fravega"})
    service = SearchService(
        adapters=[
            PositionalLocationAdapter(
                "fravega",
                "Fravega",
                [make_product("fravega", "Fravega", "Apple iPhone 15 128GB", 950000, 1)],
            ),
        ],
        repository=OptionalRepository(repository),
        location=SearchLocation(),
    )

    response = asyncio.run(service.agent_search("iphone 15", limit=1, stores=["fravega"]))

    assert response.errors == []
    assert [match.store_id for match in response.best_matches] == ["fravega"]


def test_agent_search_returns_all_non_accessory_products_ordered_by_score() -> None:
    repository = SearchRepository("sqlite+pysqlite:///:memory:")
    repository.init_schema()
    repository.seed_stores({"fravega": "Fravega", "cetrogar_ar": "Cetrogar"})
    service = SearchService(
        adapters=[
            FakeAdapter(
                "fravega",
                "Fravega",
                [
                    make_product("fravega", "Fravega", "Apple iPhone 15 Pro 256GB Natural", 4300000, 1),
                    make_product("fravega", "Fravega", "iPhone Air 256GB - Cloud White", 4999900, 2),
                ],
            ),
            FakeAdapter(
                "cetrogar_ar",
                "Cetrogar",
                [
                    make_product("cetrogar_ar", "Cetrogar", "Celular Redmi NOTE 14 PRO 6.6'' 8GB 256GB Ocean Blue", 749999, 1),
                    make_product("cetrogar_ar", "Cetrogar", "Celular TCL 605 8GB 256GB Midnight Blue RV", 259999, 2),
                ],
            ),
        ],
        repository=OptionalRepository(repository),
        location=SearchLocation(),
    )

    response = asyncio.run(service.agent_search("iPhone 15 Pro 256GB", limit=5))

    titles = [match.title for match in response.best_matches]
    # El iPhone 15 Pro lidera por relevancia; los demas productos aparecen sin filtrado editorial
    assert titles[0] == "Apple iPhone 15 Pro 256GB Natural"
    assert len(response.best_matches) == 4  # todos los candidatos no-accesorio, hasta limit=5
    # Los scores deben estar en orden descendente
    scores = [match.score for match in response.best_matches]
    assert scores == sorted(scores, reverse=True)


def test_agent_search_allows_accessories_when_query_is_for_accessory() -> None:
    repository = SearchRepository("sqlite+pysqlite:///:memory:")
    repository.init_schema()
    repository.seed_stores({"mercado_libre": "Mercado Libre"})
    service = SearchService(
        adapters=[
            FakeAdapter(
                "mercado_libre",
                "Mercado Libre",
                [
                    make_product(
                        "mercado_libre",
                        "Mercado Libre",
                        "Adaptador Apple 70W USB-C Power Adapter Original MacBook Pro",
                        99999,
                        1,
                    ),
                ],
            ),
        ],
        repository=OptionalRepository(repository),
        location=SearchLocation(),
    )

    response = asyncio.run(
        service.agent_search(
            "adaptador Apple 70W USB-C cargador MacBook Pro original",
            limit=5,
            mode=SearchMode.DEEP,
            stores=["mercado_libre"],
        )
    )

    assert response.query_understanding.detected_category == "accessories"
    assert [match.title for match in response.best_matches] == [
        "Adaptador Apple 70W USB-C Power Adapter Original MacBook Pro"
    ]
    assert "No se encontraron candidatos claros que no sean accesorios." not in response.warnings


def test_agent_search_store_override_is_reflected_in_routing() -> None:
    repository = SearchRepository("sqlite+pysqlite:///:memory:")
    repository.init_schema()
    repository.seed_stores({"mercado_libre": "Mercado Libre", "fravega": "Fravega"})
    service = SearchService(
        adapters=[
            FakeAdapter(
                "mercado_libre",
                "Mercado Libre",
                [make_product("mercado_libre", "Mercado Libre", "Apple iPhone 15 128GB", 900000, 1)],
            ),
            FakeAdapter(
                "fravega",
                "Fravega",
                [make_product("fravega", "Fravega", "Apple iPhone 15 128GB", 950000, 1)],
            ),
        ],
        repository=OptionalRepository(repository),
        location=SearchLocation(),
    )

    response = asyncio.run(
        service.agent_search("iphone 15", limit=2, stores=["mercado_libre"])
    )

    assert response.routing.selected_store_ids == ["mercado_libre"]
    assert "fravega" in response.routing.excluded_store_ids
    assert response.routing.reasons["fravega"] == "excluded_by_store_override"
    assert [match.store_id for match in response.best_matches] == ["mercado_libre"]


def test_agent_search_returns_top_n_by_score_without_diversity_guarantee() -> None:
    repository = SearchRepository("sqlite+pysqlite:///:memory:")
    repository.init_schema()
    repository.seed_stores({"mercado_libre": "Mercado Libre", "amazon_us": "Amazon US"})
    amazon_products = [
        make_product(
            "amazon_us",
            "Amazon US",
            f"96W Charger for MacBook Pro USB C Cable {index}",
            20 + index,
            index,
        ).model_copy(
            update={
                "currency": "USD",
                "rating": 4.8,
                "reviews_count": 1000,
            }
        )
        for index in range(1, 10)
    ]
    service = SearchService(
        adapters=[
            FakeAdapter(
                "mercado_libre",
                "Mercado Libre",
                [
                    make_product(
                        "mercado_libre",
                        "Mercado Libre",
                        "Cargador Macbook Pro Original",
                        51709,
                        1,
                    ),
                ],
            ),
            FakeAdapter("amazon_us", "Amazon US", amazon_products),
        ],
        repository=OptionalRepository(repository),
        location=SearchLocation(),
    )

    response = asyncio.run(
        service.agent_search(
            "cargador MacBook Pro adaptador USB-C 67W 96W con cable",
            limit=8,
            stores=["mercado_libre", "amazon_us"],
        )
    )

    # Sin seleccion diversa: devuelve los top 8 por score sin garantia de representacion por tienda
    assert len(response.best_matches) == 8
    scores = [match.score for match in response.best_matches]
    assert scores == sorted(scores, reverse=True)
    # Todas las tiendas del resultado deben ser de las seleccionadas
    store_ids = {match.store_id for match in response.best_matches}
    assert store_ids.issubset({"mercado_libre", "amazon_us"})


def test_agent_search_exposes_ars_and_usd_prices_for_amazon() -> None:
    repository = SearchRepository("sqlite+pysqlite:///:memory:")
    repository.init_schema()
    repository.seed_stores({"amazon_us": "Amazon US"})
    service = SearchService(
        adapters=[
            FakeAdapter(
                "amazon_us",
                "Amazon US",
                [
                    make_product(
                        "amazon_us",
                        "Amazon US",
                        "96W Charger for MacBook Pro",
                        18247.7,
                        1,
                    ).model_copy(
                        update={
                            "currency": "$",
                            "raw_metadata": {
                                "price_ars": 18247.7,
                                "price_usd": 12.99,
                                "source_currency": "USD",
                            },
                        }
                    )
                ],
            )
        ],
        repository=OptionalRepository(repository),
        location=SearchLocation(),
    )

    response = asyncio.run(
        service.agent_search("cargador MacBook Pro", limit=1, stores=["amazon_us"])
    )

    match = response.best_matches[0]
    assert match.price == 18247.7
    assert match.currency == "$"
    assert match.price_ars == 18247.7
    assert match.price_usd == 12.99


def test_agent_search_warns_when_semantic_is_enabled_but_unavailable() -> None:
    repository = SearchRepository("sqlite+pysqlite:///:memory:")
    repository.init_schema()
    repository.seed_stores({"mercado_libre": "Mercado Libre"})
    service = SearchService(
        adapters=[
            FakeAdapter(
                "mercado_libre",
                "Mercado Libre",
                [make_product("mercado_libre", "Mercado Libre", "Apple iPhone 15 128GB", 900000, 1)],
            ),
        ],
        repository=OptionalRepository(repository),
        location=SearchLocation(),
        semantic_enabled=True,
    )

    response = asyncio.run(service.agent_search("iphone 15", limit=1))

    assert "semantic_search_unavailable" in response.warnings


def test_agent_search_interactive_skips_history_baselines() -> None:
    repository = CountingRepository()
    repository.init_schema()
    repository.seed_stores({"mercado_libre": "Mercado Libre"})
    service = SearchService(
        adapters=[
            FakeAdapter(
                "mercado_libre",
                "Mercado Libre",
                [make_product("mercado_libre", "Mercado Libre", "Apple iPhone 15 128GB", 900000, 1)],
            ),
        ],
        repository=OptionalRepository(repository),
        location=SearchLocation(),
    )

    response = asyncio.run(service.agent_search("iphone 15", limit=1, mode=SearchMode.INTERACTIVE))

    assert repository.history_baselines_calls == 0
    assert response.history_status.status == "available_on_demand"
    assert response.best_matches[0].historical_signal is None


def test_agent_search_deep_includes_history_baselines() -> None:
    repository = CountingRepository()
    repository.init_schema()
    repository.seed_stores({"mercado_libre": "Mercado Libre"})
    service = SearchService(
        adapters=[
            FakeAdapter(
                "mercado_libre",
                "Mercado Libre",
                [make_product("mercado_libre", "Mercado Libre", "Apple iPhone 15 128GB", 900000, 1)],
            ),
        ],
        repository=OptionalRepository(repository),
        location=SearchLocation(),
    )

    response = asyncio.run(service.agent_search("iphone 15", limit=1, mode=SearchMode.DEEP))

    assert repository.history_baselines_calls == 1
    assert response.history_status.status == "included"


def test_agent_search_stream_emits_fast_match_before_slow_store_finishes() -> None:
    repository = SearchRepository("sqlite+pysqlite:///:memory:")
    repository.init_schema()
    repository.seed_stores({"mercado_libre": "Mercado Libre", "fravega": "Fravega"})
    service = SearchService(
        adapters=[
            FakeAdapter(
                "mercado_libre",
                "Mercado Libre",
                [make_product("mercado_libre", "Mercado Libre", "Apple iPhone 15 128GB", 900000, 1)],
            ),
            FakeAdapter(
                "fravega",
                "Fravega",
                [make_product("fravega", "Fravega", "Apple iPhone 15 128GB", 950000, 1)],
                delay_seconds=0.05,
            ),
        ],
        repository=OptionalRepository(repository),
        location=SearchLocation(),
    )

    async def collect_events():
        return [event async for event in service.agent_search_events("iphone 15", limit=1)]

    events = asyncio.run(collect_events())
    event_names = [event["event"] for event in events]
    first_match_index = event_names.index("match")
    slow_done_index = next(
        index
        for index, event in enumerate(events)
        if event["event"] == "store_done" and event["data"]["store_id"] == "fravega"
    )

    assert first_match_index < slow_done_index
    assert events[-1]["event"] == "final"
    assert events[-1]["data"]["debug_ref"] == 1
    assert events[-1]["data"]["history_status"]["status"] == "available_on_demand"


def test_agent_search_stream_store_timeout_does_not_block_final(monkeypatch) -> None:
    import app.services as services

    monkeypatch.setattr(services, "INTERACTIVE_STORE_TIMEOUT_SECONDS", 0.01)
    repository = SearchRepository("sqlite+pysqlite:///:memory:")
    repository.init_schema()
    repository.seed_stores({"mercado_libre": "Mercado Libre", "fravega": "Fravega"})
    service = SearchService(
        adapters=[
            FakeAdapter(
                "mercado_libre",
                "Mercado Libre",
                [make_product("mercado_libre", "Mercado Libre", "Apple iPhone 15 128GB", 900000, 1)],
            ),
            FakeAdapter(
                "fravega",
                "Fravega",
                [make_product("fravega", "Fravega", "Apple iPhone 15 128GB", 950000, 1)],
                delay_seconds=0.05,
            ),
        ],
        repository=OptionalRepository(repository),
        location=SearchLocation(),
    )

    async def collect_events():
        return [event async for event in service.agent_search_events("iphone 15", limit=1)]

    events = asyncio.run(collect_events())
    errors = [event for event in events if event["event"] == "error"]
    final = events[-1]

    assert any(error["data"]["store_id"] == "fravega" for error in errors)
    assert final["event"] == "final"
    assert final["data"]["best_matches"]


def test_agent_search_history_endpoint_returns_run_history() -> None:
    repository = SearchRepository("sqlite+pysqlite:///:memory:")
    repository.init_schema()
    repository.seed_stores({"mercado_libre": "Mercado Libre"})
    service = SearchService(
        adapters=[],
        repository=OptionalRepository(repository),
        location=SearchLocation(),
    )

    base = make_product("mercado_libre", "Mercado Libre", "Apple iPhone 15 128GB", 1000, 1)
    query_understanding = understand_query("iphone 15")
    normalized = service.normalizer.normalize(base, query_understanding)
    first = service.scorer.score("iphone 15", query_understanding, base, normalized, [1000])
    repository.save_search_snapshot("iphone 15", "5800", [base], [], [first])

    second_product = base.model_copy(
        update={"price": 1100, "product_url": "https://example.com/mercado_libre/2"}
    )
    second = service.scorer.score("iphone 15", query_understanding, second_product, normalized, [1100])
    repository.save_search_snapshot("iphone 15", "5800", [second_product], [], [second])

    current_product = base.model_copy(
        update={"price": 800, "product_url": "https://example.com/mercado_libre/3"}
    )
    current = service.scorer.score("iphone 15", query_understanding, current_product, normalized, [800])
    run_id = repository.save_search_snapshot("iphone 15", "5800", [current_product], [], [current])

    app.dependency_overrides[get_search_service] = lambda: service
    try:
        from fastapi.testclient import TestClient

        response = TestClient(app).get(f"/agent/search/{run_id}/history")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["run_id"] == run_id
    assert payload["count"] == 1
    assert payload["items"][0]["historical_signal"] == "below_recent_average"
    assert payload["items"][0]["average_price"] == 1050
    assert payload["items"][0]["price_count"] == 2

    app.dependency_overrides[get_search_service] = lambda: service
    try:
        empty_response = TestClient(app).get("/agent/search/999/history")
    finally:
        app.dependency_overrides.clear()

    assert empty_response.status_code == 200
    assert empty_response.json()["count"] == 0


def test_agent_search_generates_match_candidates_without_changing_ranking() -> None:
    repository = SearchRepository("sqlite+pysqlite:///:memory:")
    repository.init_schema()
    repository.seed_stores({"mercado_libre": "Mercado Libre", "fravega": "Fravega"})
    service = SearchService(
        adapters=[
            FakeAdapter(
                "mercado_libre",
                "Mercado Libre",
                [make_product("mercado_libre", "Mercado Libre", "Apple iPhone 15 128GB", 1000, 1)],
            ),
            FakeAdapter(
                "fravega",
                "Fravega",
                [make_product("fravega", "Fravega", "Celular iPhone 15 128 GB Negro", 1100, 1)],
            ),
        ],
        repository=OptionalRepository(repository),
        location=SearchLocation(),
    )

    response = asyncio.run(service.agent_search("iphone 15", limit=2))

    with repository.engine.connect() as conn:
        candidates = conn.execute(product_match_candidates.select()).mappings().all()

    assert [match.store_id for match in response.best_matches] == ["mercado_libre", "fravega"]
    assert len(candidates) == 1
    assert 0.35 <= candidates[0]["match_confidence"] <= 0.75


def test_internal_matching_endpoints_list_and_label_candidates() -> None:
    repository = SearchRepository("sqlite+pysqlite:///:memory:")
    repository.init_schema()
    repository.seed_stores({"mercado_libre": "Mercado Libre", "fravega": "Fravega"})
    service = SearchService(
        adapters=[
            FakeAdapter(
                "mercado_libre",
                "Mercado Libre",
                [make_product("mercado_libre", "Mercado Libre", "Apple iPhone 15 128GB", 1000, 1)],
            ),
            FakeAdapter(
                "fravega",
                "Fravega",
                [make_product("fravega", "Fravega", "Celular iPhone 15 128 GB Negro", 1100, 1)],
            ),
        ],
        repository=OptionalRepository(repository),
        location=SearchLocation(),
    )
    asyncio.run(service.agent_search("iphone 15", limit=2))

    from fastapi.testclient import TestClient

    app.dependency_overrides[get_search_service] = lambda: service
    try:
        client = TestClient(app)
        list_response = client.get("/internal/matching/candidates")
        candidate_id = list_response.json()[0]["id"]
        label_response = client.post(
            f"/internal/matching/candidates/{candidate_id}/label",
            json={"label": "same", "comment": "same product"},
        )
        summary_response = client.get("/internal/matching/summary")
        invalid_response = client.post(
            f"/internal/matching/candidates/{candidate_id}/label",
            json={"label": "maybe"},
        )
    finally:
        app.dependency_overrides.clear()

    assert list_response.status_code == 200
    assert len(list_response.json()) == 1
    assert label_response.status_code == 200
    assert label_response.json()["label"] == "same"
    assert summary_response.status_code == 200
    assert summary_response.json()["labels_by_value"] == {"same": 1}
    assert invalid_response.status_code == 422
