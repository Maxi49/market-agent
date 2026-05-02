from app.models import Product, ProductAvailability, ProductCondition
from app.normalization import ProductNormalizer
from app.routing import understand_query
from app.scoring import ProductScorer
from tests.typing_helpers import http_url


def product(
    title: str,
    price: float,
    condition: ProductCondition,
    availability: ProductAvailability = ProductAvailability.IN_STOCK,
    store_id: str = "fravega",
) -> Product:
    return Product(
        store_id=store_id,
        store_name="Fravega",
        position=1,
        title=title,
        price=price,
        currency="$",
        product_url=http_url(f"https://example.com/{title.replace(' ', '-')}"),
        condition=condition,
        availability=availability,
        shipping="Envio gratis",
    )


def score(item: Product, query: str):
    understanding = understand_query(query)
    normalized = ProductNormalizer().normalize(item, understanding)
    return ProductScorer().score(query, understanding, item, normalized, [item.price or 0])


def test_new_available_product_beats_cheaper_refurbished_for_regular_query() -> None:
    new_item = product("Apple iPhone 15 128GB", 1500000, ProductCondition.NEW)
    refurbished = product("Apple iPhone 15 reacondicionado", 900000, ProductCondition.REFURBISHED)

    new_score = score(new_item, "iphone 15")
    refurbished_score = score(refurbished, "iphone 15")

    assert new_score.score > refurbished_score.score
    assert refurbished_score.warnings == ["Condicion no nueva: refurbished."]


def test_history_signal_is_informative_not_ranking_input() -> None:
    item = product("Apple iPhone 15 128GB", 1500000, ProductCondition.NEW)
    understanding = understand_query("iphone 15")
    normalized = ProductNormalizer().normalize(item, understanding)

    scored = ProductScorer().score(
        "iphone 15",
        understanding,
        item,
        normalized,
        [1500000],
        history_signal="below_recent_average",
    )

    assert scored.trust_signals.history_signal == "below_recent_average"
    assert scored.score_breakdown.price == 25
