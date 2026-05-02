from app.config import Settings, normalize_database_url
from app.scrapers.registry import build_store_registry


def test_default_active_stores_exclude_musimundo(monkeypatch) -> None:
    monkeypatch.delenv("ACTIVE_STORES", raising=False)
    monkeypatch.delenv("AMAZON_PROVIDER", raising=False)

    settings = Settings()
    registry = build_store_registry()

    assert "musimundo" not in settings.active_store_ids
    assert "musimundo" not in registry
    assert "amazon_us" not in settings.active_store_ids
    assert "amazon_us" not in registry
    assert "naldo_ar" not in settings.active_store_ids
    assert "naldo_ar" not in registry
    assert settings.active_store_ids == [
        "mercado_libre",
        "fravega",
        "samsung_ar",
        "carrefour_ar",
        "cetrogar_ar",
        "easy_ar",
        "bgh_ar",
        "sony_ar",
    ]


def test_amazon_registry_is_opt_in_with_existing_serpapi_key(monkeypatch) -> None:
    monkeypatch.setenv("AMAZON_PROVIDER", "serpapi")
    monkeypatch.setenv("SERP_API_KEY", "token")

    registry = build_store_registry()

    assert registry["amazon_us"].store_name == "Amazon US"


def test_embeddings_are_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("EMBEDDINGS_ENABLED", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    settings = Settings()

    assert settings.embeddings_enabled is False
    assert settings.openai_api_key is None
    assert settings.embedding_model == "text-embedding-3-small"
    assert settings.embedding_dimensions == 1536
    assert settings.embedding_max_items_per_run == 500
    assert settings.embedding_monthly_token_budget == 1000000


def test_database_url_accepts_supabase_postgres_urls() -> None:
    assert normalize_database_url(
        "postgresql://postgres:secret@db.example.supabase.co:5432/postgres?sslmode=require"
    ) == "postgresql+psycopg://postgres:secret@db.example.supabase.co:5432/postgres?sslmode=require"
    assert normalize_database_url(
        "postgres://postgres:secret@db.example.supabase.co:5432/postgres"
    ) == "postgresql+psycopg://postgres:secret@db.example.supabase.co:5432/postgres"


def test_database_url_preserves_encoded_special_characters() -> None:
    assert normalize_database_url(
        "postgresql://postgres:pass%23word@db.example.supabase.co:5432/postgres"
    ) == "postgresql+psycopg://postgres:pass%23word@db.example.supabase.co:5432/postgres"
