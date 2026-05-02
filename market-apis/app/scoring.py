from app.models import (
    NormalizedProduct,
    Product,
    ProductAvailability,
    ProductCondition,
    QueryUnderstanding,
    ScoreBreakdown,
    ScoredProduct,
    TrustSignals,
)
from app.ranking import is_relevant


TRUSTED_STORE_IDS = {"mercado_libre", "fravega", "carrefour_ar", "samsung_ar"}


class ProductScorer:
    def score(
        self,
        query: str,
        query_understanding: QueryUnderstanding,
        product: Product,
        normalized: NormalizedProduct,
        comparable_prices: list[float],
        history_signal: str | None = None,
    ) -> ScoredProduct:
        warnings: list[str] = []
        trust = TrustSignals(
            official_store=product.store_id in TRUSTED_STORE_IDS and product.seller in {None, "", product.store_name},
            has_rating=product.rating is not None,
            has_reviews=bool(product.reviews_count),
            free_shipping=bool(product.shipping and "gratis" in product.shipping.lower()),
            in_stock=product.availability == ProductAvailability.IN_STOCK,
            seller_known=bool(product.seller) or product.store_id in TRUSTED_STORE_IDS,
            history_signal=history_signal,
        )

        accessory_requested = query_understanding.detected_category == "accessories"

        if normalized.is_accessory and not accessory_requested:
            warnings.append("Parece ser un accesorio, no el producto principal.")
        if normalized.condition in {ProductCondition.USED, ProductCondition.REFURBISHED}:
            warnings.append(f"Condicion no nueva: {normalized.condition.value}.")
        if product.availability == ProductAvailability.OUT_OF_STOCK:
            warnings.append("Producto sin stock.")
        if product.price is None:
            warnings.append("No se pudo detectar precio.")

        breakdown = ScoreBreakdown(
            relevance=25 if is_relevant(query, product) and (not normalized.is_accessory or accessory_requested) else 5,
            price=_price_score(product.price, comparable_prices),
            condition=_condition_score(query_understanding, normalized.condition),
            availability=10 if product.availability != ProductAvailability.OUT_OF_STOCK else 0,
            seller_trust=10 if trust.seller_known else 3,
            shipping=5 if trust.free_shipping else 2,
            reviews=_review_score(product),
            penalties=25 if normalized.is_accessory and not accessory_requested else 0,
        )
        if product.sponsored:
            breakdown.penalties += 3

        return ScoredProduct(
            product=product,
            normalized=normalized,
            score=round(breakdown.total, 2),
            score_breakdown=breakdown,
            trust_signals=trust,
            warnings=warnings,
            explanation=_explanation(product, normalized, warnings),
        )


def _price_score(price: float | None, comparable_prices: list[float]) -> float:
    if price is None:
        return 0
    if not comparable_prices:
        return 20
    min_price = min(comparable_prices)
    if price <= min_price:
        return 25
    ratio = min_price / price
    return max(5, round(25 * ratio, 2))


def _condition_score(query_understanding: QueryUnderstanding, condition: ProductCondition) -> float:
    query_accepts_used = any(
        token in query_understanding.normalized_query
        for token in ["usado", "usada", "reacondicionado", "reacondicionada"]
    )
    if condition == ProductCondition.NEW:
        return 15
    if condition == ProductCondition.UNKNOWN:
        return 10
    return 12 if query_accepts_used else 2


def _review_score(product: Product) -> float:
    if product.rating is None:
        return 0
    score = min(5, product.rating)
    if product.reviews_count:
        score += min(5, product.reviews_count / 20)
    return round(min(10, score), 2)


def _explanation(product: Product, normalized: NormalizedProduct, warnings: list[str]) -> str:
    if warnings:
        return f"{product.store_name}: buen candidato con advertencias ({'; '.join(warnings[:2])})."
    if product.price is not None:
        return f"{product.store_name}: {normalized.normalized_title} con precio competitivo y señales suficientes."
    return f"{product.store_name}: coincide con la busqueda, pero requiere revisar precio."
