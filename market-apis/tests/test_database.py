from app.database import (
    SearchRepository,
    canonical_products,
    product_observations,
    product_match_candidates,
    product_match_labels,
    scrape_adapter_metrics,
    scrape_runs,
    stores,
    transformed_product_observations,
)
from app.models import (
    AdapterMetric,
    Product,
    ProductCondition,
    ProductMatchLabelRequest,
    ProductMatchLabelValue,
    SearchLocation,
    SearchMode,
    StoreError,
)
from app.normalization import ProductNormalizer
from app.routing import understand_query
from app.scoring import ProductScorer
from tests.typing_helpers import http_url


def test_repository_saves_run_and_deduplicates_products() -> None:
    repository = SearchRepository("sqlite+pysqlite:///:memory:")
    repository.init_schema()
    repository.seed_stores({"test": "Test Store"})

    item = Product(
        store_id="test",
        store_name="Test Store",
        position=1,
        title="Apple iPhone 15",
        price=1500000,
        currency="$",
        product_url=http_url("https://example.com/iphone-15"),
    )

    run_id = repository.save_search_snapshot(
        query="iphone 15",
        postal_code=SearchLocation().postal_code,
        products=[item, item],
        errors=[StoreError(store_id="samsung_ar", store_name="Samsung Argentina", message="empty")],
    )

    with repository.engine.connect() as conn:
        run_count = conn.execute(scrape_runs.select()).all()
        observation_count = conn.execute(product_observations.select()).all()
        store_count = conn.execute(stores.select()).all()

    assert run_id == 1
    assert len(run_count) == 1
    assert len(observation_count) == 1
    assert len(store_count) == 1


def test_repository_saves_transformed_products_and_history_signal() -> None:
    repository = SearchRepository("sqlite+pysqlite:///:memory:")
    repository.init_schema()
    repository.seed_stores({"test": "Test Store"})

    item = Product(
        store_id="test",
        store_name="Test Store",
        position=1,
        title="Apple iPhone 15 128GB",
        price=1000,
        currency="$",
        product_url=http_url("https://example.com/iphone-15"),
        condition=ProductCondition.NEW,
    )
    understanding = understand_query("iphone 15")
    normalized = ProductNormalizer().normalize(item, understanding)
    scored = ProductScorer().score("iphone 15", understanding, item, normalized, [1000])

    repository.save_search_snapshot(
        query="iphone 15",
        postal_code="5800",
        products=[item],
        errors=[],
        transformed_products=[scored],
    )

    cheaper_item = item.model_copy(update={"price": 800, "product_url": "https://example.com/iphone-15-2"})
    cheaper_scored = ProductScorer().score(
        "iphone 15",
        understanding,
        cheaper_item,
        normalized,
        [800],
    )
    repository.save_search_snapshot(
        query="iphone 15",
        postal_code="5800",
        products=[cheaper_item],
        errors=[],
        transformed_products=[cheaper_scored],
    )

    with repository.engine.connect() as conn:
        canonicals = conn.execute(canonical_products.select()).all()
        transformed = conn.execute(transformed_product_observations.select()).all()

    assert len(canonicals) == 1
    assert len(transformed) == 2
    assert repository.get_history_signal(normalized.canonical_key, 700) == "below_recent_average"


def test_repository_returns_history_baselines_in_batch() -> None:
    repository = SearchRepository("sqlite+pysqlite:///:memory:")
    repository.init_schema()
    repository.seed_stores({"test": "Test Store"})

    item = Product(
        store_id="test",
        store_name="Test Store",
        position=1,
        title="Apple iPhone 15 128GB",
        price=1000,
        currency="$",
        product_url=http_url("https://example.com/iphone-15"),
        condition=ProductCondition.NEW,
    )
    understanding = understand_query("iphone 15")
    normalized = ProductNormalizer().normalize(item, understanding)
    scored = ProductScorer().score("iphone 15", understanding, item, normalized, [1000])

    repository.save_search_snapshot("iphone 15", "5800", [item], [], [scored])
    second = item.model_copy(update={"price": 800, "product_url": "https://example.com/iphone-15-2"})
    second_scored = ProductScorer().score("iphone 15", understanding, second, normalized, [800])
    repository.save_search_snapshot("iphone 15", "5800", [second], [], [second_scored])

    baselines = repository.get_history_baselines([normalized.canonical_key])

    assert baselines[normalized.canonical_key] == (900, 2)


def test_repository_can_persist_incremental_run() -> None:
    repository = SearchRepository("sqlite+pysqlite:///:memory:")
    repository.init_schema()
    repository.seed_stores({"test": "Test Store"})
    item = Product(
        store_id="test",
        store_name="Test Store",
        position=1,
        title="Apple iPhone 15 128GB",
        price=1000,
        currency="$",
        product_url=http_url("https://example.com/iphone-15"),
        condition=ProductCondition.NEW,
    )
    understanding = understand_query("iphone 15")
    normalized = ProductNormalizer().normalize(item, understanding)
    scored = ProductScorer().score("iphone 15", understanding, item, normalized, [1000])

    run_id = repository.create_scrape_run("iphone 15", "5800")
    repository.append_search_run_results(run_id, "iphone 15", [item], [scored])
    repository.finish_scrape_run(run_id, "completed", [])

    with repository.engine.connect() as conn:
        run = conn.execute(scrape_runs.select()).mappings().one()
        observations = conn.execute(product_observations.select()).all()
        transformed = conn.execute(transformed_product_observations.select()).all()

    assert run["status"] == "completed"
    assert run["finished_at"] is not None
    assert len(observations) == 1
    assert len(transformed) == 1


def test_repository_saves_adapter_metrics() -> None:
    repository = SearchRepository("sqlite+pysqlite:///:memory:")
    repository.init_schema()
    repository.seed_stores({"test": "Test Store"})
    run_id = repository.create_scrape_run("iphone 15", "5800")

    repository.save_adapter_metrics(
        run_id,
        [
            AdapterMetric(
                store_id="test",
                store_name="Test Store",
                query="iphone 15",
                mode=SearchMode.INTERACTIVE,
                strategy="html",
                elapsed_ms=123,
                status="ok",
                products_count=2,
            )
        ],
    )

    with repository.engine.connect() as conn:
        row = conn.execute(scrape_adapter_metrics.select()).mappings().one()

    assert row["scrape_run_id"] == run_id
    assert row["mode"] == "interactive"
    assert row["elapsed_ms"] == 123
    assert row["products_count"] == 2


def test_repository_saves_match_candidates_and_labels() -> None:
    repository = SearchRepository("sqlite+pysqlite:///:memory:")
    repository.init_schema()
    repository.seed_stores({"mercado_libre": "Mercado Libre", "fravega": "Fravega"})
    understanding = understand_query("iphone 15")
    normalizer = ProductNormalizer()
    scorer = ProductScorer()
    left = Product(
        store_id="mercado_libre",
        store_name="Mercado Libre",
        position=1,
        title="Apple iPhone 15 128GB",
        price=1000,
        currency="$",
        product_url=http_url("https://example.com/ml/iphone-15"),
        condition=ProductCondition.NEW,
    )
    right = Product(
        store_id="fravega",
        store_name="Fravega",
        position=1,
        title="Celular iPhone 15 128 GB Negro",
        price=1100,
        currency="$",
        product_url=http_url("https://example.com/fravega/iphone-15"),
        condition=ProductCondition.NEW,
    )
    scored_left = scorer.score(
        "iphone 15",
        understanding,
        left,
        normalizer.normalize(left, understanding),
        [1000, 1100],
    )
    scored_right = scorer.score(
        "iphone 15",
        understanding,
        right,
        normalizer.normalize(right, understanding),
        [1000, 1100],
    )
    run_id = repository.save_search_snapshot(
        "iphone 15",
        "5800",
        [left, right],
        [],
        [scored_left, scored_right],
    )

    created = repository.save_match_candidates(run_id, "iphone 15", [scored_left, scored_right])
    candidates = repository.list_match_candidates()
    response = repository.label_match_candidate(
        candidates[0].id,
        ProductMatchLabelRequest(label=ProductMatchLabelValue.SAME, comment="same SKU"),
    )
    summary = repository.get_match_summary()

    with repository.engine.connect() as conn:
        candidate_rows = conn.execute(product_match_candidates.select()).all()
        label_rows = conn.execute(product_match_labels.select()).all()

    assert created == 1
    assert len(candidates) == 1
    assert candidates[0].label is None
    assert response is not None
    assert response.label == ProductMatchLabelValue.SAME
    assert len(candidate_rows) == 1
    assert len(label_rows) == 1
    assert summary.total_candidates == 1
    assert summary.unlabeled_candidates == 0
    assert summary.labels_by_value == {"same": 1}
    assert sum(summary.confidence_buckets.values()) == summary.total_candidates
