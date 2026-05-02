import asyncio

from app.database import (
    OptionalRepository,
    SearchRepository,
    canonical_products,
    embedding_usage_log,
)
from app.embeddings import (
    EmbeddingBudgetGuard,
    EmbeddingCandidate,
    EmbeddingSettings,
    OpenAIEmbeddingProvider,
    build_embedding_text,
    embedding_hash,
)
from app.models import Product, ProductCondition
from app.normalization import ProductNormalizer
from app.routing import understand_query
from app.scoring import ProductScorer
from app.semantic import EmbeddingBackfillService
from tests.typing_helpers import http_url


class FakeProvider:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        return [[1.0, 0.0, 0.0] for _ in texts]


def product() -> Product:
    return Product(
        store_id="test",
        store_name="Test Store",
        position=1,
        title="Apple iPhone 15 128GB",
        price=1000,
        currency="$",
        product_url=http_url("https://example.com/iphone-15"),
        condition=ProductCondition.NEW,
    )


def repository_with_canonical() -> tuple[SearchRepository, str]:
    repository = SearchRepository("sqlite+pysqlite:///:memory:")
    repository.init_schema()
    repository.seed_stores({"test": "Test Store"})
    item = product()
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
    return repository, normalized.canonical_key


def test_embedding_text_is_stable_and_short() -> None:
    text = build_embedding_text(
        normalized_title="Apple Iphone 15 128GB",
        brand="apple",
        model="iphone 15",
        category="smartphones",
        attributes={"storage": "128GB", "color": "negro"},
    )

    assert "Apple Iphone 15 128GB" in text
    assert "price" not in text.lower()
    assert "http" not in text.lower()


def test_budget_guard_blocks_monthly_budget() -> None:
    settings = EmbeddingSettings(
        enabled=True,
        api_key="key",
        monthly_token_budget=10,
        max_items_per_run=10,
    )
    guard = EmbeddingBudgetGuard(settings)
    candidates = [
        EmbeddingCandidate("a", "x" * 200, "hash"),
    ]

    allowed, errors, _, _, _ = guard.validate(candidates, used_tokens_this_month=0)

    assert allowed == []
    assert "monthly_token_budget_exceeded" in errors


def test_dry_run_estimates_without_calling_provider() -> None:
    repository, _ = repository_with_canonical()
    provider = FakeProvider()
    settings = EmbeddingSettings(enabled=True, api_key="key", dimensions=3)
    service = EmbeddingBackfillService(
        repository=OptionalRepository(repository),
        settings=settings,
        provider=provider,
        budget_guard=EmbeddingBudgetGuard(settings),
    )

    response = asyncio.run(service.run(dry_run=True))

    assert response.processed == 0
    assert response.estimated_tokens > 0
    assert provider.calls == []
    with repository.engine.connect() as conn:
        rows = conn.execute(embedding_usage_log.select()).all()
    assert len(rows) == 1
    assert rows[0].dry_run is True


def test_backfill_saves_embedding_and_skips_same_hash() -> None:
    repository, canonical_key = repository_with_canonical()
    provider = FakeProvider()
    settings = EmbeddingSettings(enabled=True, api_key="key", dimensions=3)
    service = EmbeddingBackfillService(
        repository=OptionalRepository(repository),
        settings=settings,
        provider=provider,
        budget_guard=EmbeddingBudgetGuard(settings),
    )

    response = asyncio.run(service.run(dry_run=False))
    second = asyncio.run(service.run(dry_run=False))

    assert response.processed == 1
    assert second.processed == 0
    assert len(provider.calls) == 1
    with repository.engine.connect() as conn:
        row = conn.execute(
            canonical_products.select().where(canonical_products.c.canonical_key == canonical_key)
        ).mappings().one()
    assert row["embedding"] == [1.0, 0.0, 0.0]
    assert row["embedding_text_hash"]
    assert row["embedding_model"] == "text-embedding-3-small"


def test_openai_provider_uses_configured_model_and_dimensions(monkeypatch) -> None:
    captured = {}

    class FakeEmbeddings:
        async def create(self, **kwargs):
            captured.update(kwargs)

            class Item:
                embedding = [0.1, 0.2, 0.3]

            class Response:
                data = [Item()]

            return Response()

    class FakeClient:
        def __init__(self, api_key: str) -> None:
            captured["api_key"] = api_key
            self.embeddings = FakeEmbeddings()

    import types
    import sys

    fake_openai = types.SimpleNamespace(AsyncOpenAI=FakeClient)
    monkeypatch.setitem(sys.modules, "openai", fake_openai)

    provider = OpenAIEmbeddingProvider(
        EmbeddingSettings(
            enabled=True,
            api_key="secret",
            model="text-embedding-3-small",
            dimensions=3,
        )
    )

    vectors = asyncio.run(provider.embed_texts(["hello"]))

    assert vectors == [[0.1, 0.2, 0.3]]
    assert captured["api_key"] == "secret"
    assert captured["model"] == "text-embedding-3-small"
    assert captured["dimensions"] == 3
    assert captured["input"] == ["hello"]
