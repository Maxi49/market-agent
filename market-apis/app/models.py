from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, HttpUrl


class ProductCondition(StrEnum):
    NEW = "new"
    USED = "used"
    REFURBISHED = "refurbished"
    UNKNOWN = "unknown"


class ProductAvailability(StrEnum):
    IN_STOCK = "in_stock"
    OUT_OF_STOCK = "out_of_stock"
    UNKNOWN = "unknown"


class SearchMode(StrEnum):
    INTERACTIVE = "interactive"
    DEEP = "deep"


class HistoryStatusValue(StrEnum):
    INCLUDED = "included"
    AVAILABLE_ON_DEMAND = "available_on_demand"
    UNAVAILABLE = "unavailable"


class ProductMatchLabelValue(StrEnum):
    SAME = "same"
    DIFFERENT = "different"
    UNSURE = "unsure"


class SearchLocation(BaseModel):
    country: str = "AR"
    postal_code: str = "5800"
    city: str = "Cordoba"


class Product(BaseModel):
    store_id: str
    store_name: str
    position: int = Field(..., ge=1)
    title: str
    price: float | None = None
    currency: str | None = None
    original_price: float | None = None
    discount: str | None = None
    installments: str | None = None
    shipping: str | None = None
    seller: str | None = None
    rating: float | None = None
    reviews_count: int | None = None
    image_url: HttpUrl | None = None
    product_url: HttpUrl
    condition: ProductCondition = ProductCondition.UNKNOWN
    availability: ProductAvailability = ProductAvailability.UNKNOWN
    sponsored: bool = False
    scraped_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    raw_metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def source(self) -> str:
        return self.store_id


class StoreError(BaseModel):
    store_id: str
    store_name: str
    message: str


class AdapterMetric(BaseModel):
    store_id: str
    store_name: str
    query: str
    mode: SearchMode
    strategy: str
    elapsed_ms: int = Field(..., ge=0)
    status: str
    products_count: int = Field(0, ge=0)
    error_type: str | None = None


class SearchResponse(BaseModel):
    query: str
    count: int
    results: list[Product]
    errors: list[StoreError] = Field(default_factory=list)


class TrackedQuery(BaseModel):
    query: str
    enabled: bool = True
    limit: int = Field(default=50, ge=1, le=100)


class ScrapeRunSummary(BaseModel):
    run_id: int | None
    query: str
    results_count: int
    errors: list[StoreError] = Field(default_factory=list)


class HistoryStatus(BaseModel):
    status: HistoryStatusValue
    lookup_url: str | None = None
    reason: str | None = None


class AgentHistoryItem(BaseModel):
    store_id: str
    product_url: str
    canonical_key: str
    normalized_title: str
    price: float | None = None
    historical_signal: str | None = None
    average_price: float | None = None
    price_count: int = 0


class AgentHistoryResponse(BaseModel):
    run_id: int
    count: int
    items: list[AgentHistoryItem]
    errors: list[StoreError] = Field(default_factory=list)


class ProductPairFeatures(BaseModel):
    token_overlap: float = 0
    rare_token_overlap: float = 0
    numeric_token_agreement: float = 0
    title_similarity: float = 0
    brand_agreement: float = 0
    category_agreement: float = 0
    accessory_mismatch: bool = False
    model_suffix_conflict: bool = False
    storage_conflict: bool = False
    screen_size_conflict: bool = False
    bundle_conflict: bool = False
    canonical_key_match: bool = False
    price_ratio: float | None = None
    title_embedding_similarity: float | None = None
    normalized_title_embedding_similarity: float | None = None
    canonical_text_embedding_similarity: float | None = None
    brand_model_text_embedding_similarity: float | None = None
    reranker_score_raw_avg: float | None = None
    reranker_score_same_query_avg: float | None = None


class ProductMatchCandidate(BaseModel):
    id: int
    run_id: int
    query: str
    left_store_id: str
    left_title: str
    left_product_url: str
    left_canonical_key: str
    left_price: float | None = None
    right_store_id: str
    right_title: str
    right_product_url: str
    right_canonical_key: str
    right_price: float | None = None
    features: ProductPairFeatures
    match_confidence: float
    label: ProductMatchLabelValue | None = None
    model_match_probability: float | None = None
    model_decision: ProductMatchLabelValue | None = None
    model_version: str | None = None


class ProductMatchLabelRequest(BaseModel):
    label: ProductMatchLabelValue
    comment: str | None = Field(None, max_length=1000)


class ProductMatchLabelResponse(BaseModel):
    candidate_id: int
    label: ProductMatchLabelValue
    comment: str | None = None


class ProductMatchSummary(BaseModel):
    total_candidates: int = 0
    unlabeled_candidates: int = 0
    labels_by_value: dict[str, int] = Field(default_factory=dict)
    confidence_buckets: dict[str, int] = Field(default_factory=dict)
    active_model_version: str | None = None
    model_predictions_count: int = 0
    latest_model_metrics: dict[str, Any] | None = None


class QueryUnderstanding(BaseModel):
    original_query: str
    normalized_query: str
    detected_brands: list[str] = Field(default_factory=list)
    detected_category: str | None = None
    attributes: dict[str, str] = Field(default_factory=dict)


class RoutingDecision(BaseModel):
    selected_store_ids: list[str]
    excluded_store_ids: list[str] = Field(default_factory=list)
    reasons: dict[str, str] = Field(default_factory=dict)
    query_understanding: QueryUnderstanding


class NormalizedProduct(BaseModel):
    canonical_key: str
    normalized_title: str
    brand: str | None = None
    model: str | None = None
    category: str | None = None
    attributes: dict[str, str] = Field(default_factory=dict)
    is_accessory: bool = False
    condition: ProductCondition = ProductCondition.UNKNOWN
    raw_compact: dict[str, Any] = Field(default_factory=dict)


class TrustSignals(BaseModel):
    official_store: bool = False
    has_rating: bool = False
    has_reviews: bool = False
    free_shipping: bool = False
    in_stock: bool = False
    seller_known: bool = False
    history_signal: str | None = None


class SemanticMatch(BaseModel):
    canonical_key: str
    score: float
    reason: str


class ScoreBreakdown(BaseModel):
    relevance: float = 0
    price: float = 0
    condition: float = 0
    availability: float = 0
    seller_trust: float = 0
    shipping: float = 0
    reviews: float = 0
    penalties: float = 0

    @property
    def total(self) -> float:
        positive = (
            self.relevance
            + self.price
            + self.condition
            + self.availability
            + self.seller_trust
            + self.shipping
            + self.reviews
        )
        return max(0, min(100, positive - self.penalties))


class ScoredProduct(BaseModel):
    product: Product
    normalized: NormalizedProduct
    score: float
    score_breakdown: ScoreBreakdown
    trust_signals: TrustSignals
    warnings: list[str] = Field(default_factory=list)
    explanation: str


class AgentMatch(BaseModel):
    normalized_name: str
    store_id: str
    store_name: str
    title: str
    price: float | None
    currency: str | None
    price_ars: float | None = None
    price_usd: float | None = None
    product_url: HttpUrl
    image_url: HttpUrl | None = None
    score: float
    score_breakdown: ScoreBreakdown
    explanation: str
    risks: list[str] = Field(default_factory=list)
    trust_signals: TrustSignals
    historical_signal: str | None = None
    semantic_match: SemanticMatch | None = None


class AgentSearchResponse(BaseModel):
    query: str
    debug_ref: int | None = None
    routing: RoutingDecision
    query_understanding: QueryUnderstanding
    best_matches: list[AgentMatch]
    history_status: HistoryStatus
    warnings: list[str] = Field(default_factory=list)
    errors: list[StoreError] = Field(default_factory=list)


class EmbeddingBackfillResponse(BaseModel):
    processed: int = 0
    skipped: int = 0
    estimated_tokens: int = 0
    estimated_cost_usd: float = 0
    budget_remaining_tokens: int | None = None
    errors: list[str] = Field(default_factory=list)
    dry_run: bool = True
