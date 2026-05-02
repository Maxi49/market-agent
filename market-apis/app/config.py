from functools import lru_cache
from os import getenv

from dotenv import load_dotenv

from app.models import SearchLocation, TrackedQuery

load_dotenv()


class Settings:
    database_url: str
    default_location: SearchLocation
    active_store_ids: list[str]
    tracked_queries: list[TrackedQuery]
    job_interval_hours: int
    scheduler_enabled: bool
    embeddings_enabled: bool
    openai_api_key: str | None
    embedding_model: str
    embedding_dimensions: int
    embedding_max_items_per_run: int
    embedding_monthly_token_budget: int
    embedding_estimated_cost_per_1m_tokens: float
    matching_predictions_enabled: bool
    matching_model_prewarm_enabled: bool
    matching_prediction_limit: int
    mercado_libre_client_id: str | None
    mercado_libre_client_secret: str | None
    mercado_libre_redirect_uri: str | None
    amazon_provider: str
    amazon_serpapi_domain: str
    amazon_serpapi_language: str
    amazon_serpapi_shipping_location: str

    def __init__(self) -> None:
        self.database_url = normalize_database_url(
            getenv(
                "DATABASE_URL",
                "postgresql+psycopg://postgres:postgres@localhost:5432/mercado_libre_etl",
            )
        )
        self.default_location = SearchLocation(
            postal_code=getenv("DEFAULT_POSTAL_CODE", "5800"),
            city=getenv("DEFAULT_CITY", "Cordoba"),
        )
        self.active_store_ids = _csv_env(
            "ACTIVE_STORES",
            "mercado_libre,fravega,megatone_ar,samsung_ar,carrefour_ar,cetrogar_ar,easy_ar,bgh_ar,sony_ar",
        )
        self.tracked_queries = [
            TrackedQuery(query=query)
            for query in _csv_env("TRACKED_QUERIES", "iphone 15,notebook i5,smart tv 55")
        ]
        self.job_interval_hours = int(getenv("JOB_INTERVAL_HOURS", "6"))
        self.scheduler_enabled = getenv("SCHEDULER_ENABLED", "false").lower() in {
            "1",
            "true",
            "yes",
        }
        self.embeddings_enabled = getenv("EMBEDDINGS_ENABLED", "false").lower() in {
            "1",
            "true",
            "yes",
        }
        self.openai_api_key = getenv("OPENAI_API_KEY")
        self.embedding_model = getenv("EMBEDDING_MODEL", "text-embedding-3-small")
        self.embedding_dimensions = int(getenv("EMBEDDING_DIMENSIONS", "1536"))
        self.embedding_max_items_per_run = int(getenv("EMBEDDING_MAX_ITEMS_PER_RUN", "500"))
        self.embedding_monthly_token_budget = int(getenv("EMBEDDING_MONTHLY_TOKEN_BUDGET", "1000000"))
        self.embedding_estimated_cost_per_1m_tokens = float(
            getenv("EMBEDDING_ESTIMATED_COST_PER_1M_TOKENS", "0.02")
        )
        self.matching_predictions_enabled = getenv("MATCHING_PREDICTIONS_ENABLED", "false").lower() in {
            "1",
            "true",
            "yes",
        }
        self.matching_model_prewarm_enabled = getenv("MATCHING_MODEL_PREWARM_ENABLED", "false").lower() in {
            "1",
            "true",
            "yes",
        }
        self.matching_prediction_limit = int(getenv("MATCHING_PREDICTION_LIMIT", "1000"))
        self.mercado_libre_client_id = getenv("MERCADO_LIBRE_CLIENT_ID")
        self.mercado_libre_client_secret = getenv("MERCADO_LIBRE_CLIENT_SECRET")
        self.mercado_libre_redirect_uri = getenv("MERCADO_LIBRE_REDIRECT_URI")
        self.amazon_provider = getenv("AMAZON_PROVIDER", "disabled")
        self.amazon_serpapi_domain = getenv("AMAZON_SERPAPI_DOMAIN", "amazon.com")
        self.amazon_serpapi_language = getenv("AMAZON_SERPAPI_LANGUAGE", "en_US")
        self.amazon_serpapi_shipping_location = getenv("AMAZON_SERPAPI_SHIPPING_LOCATION", "ar")


def _csv_env(name: str, default: str) -> list[str]:
    return [value.strip() for value in getenv(name, default).split(",") if value.strip()]


def normalize_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql+psycopg://", 1)
    return database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
