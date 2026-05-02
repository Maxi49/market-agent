from app.matching import build_pair_features, estimate_match_confidence
from app.models import Product, ProductAvailability, ProductCondition
from app.normalization import ProductNormalizer
from app.routing import understand_query
from app.scoring import ProductScorer
from tests.typing_helpers import http_url


def make_scored(title: str, price: float = 1000, store_id: str = "test"):
    product = Product(
        store_id=store_id,
        store_name=store_id.title(),
        position=1,
        title=title,
        price=price,
        currency="$",
        product_url=http_url(f"https://example.com/{store_id}/{abs(hash(title))}"),
        condition=ProductCondition.NEW,
        availability=ProductAvailability.IN_STOCK,
    )
    understanding = understand_query("iphone 15 128gb")
    normalized = ProductNormalizer().normalize(product, understanding)
    return ProductScorer().score(
        "iphone 15 128gb",
        understanding,
        product,
        normalized,
        [price],
    )


def test_pair_features_match_reordered_product_title() -> None:
    left = make_scored("Apple iPhone 15 128GB Negro", store_id="mercado_libre")
    right = make_scored("Celular iPhone 15 Negro 128 GB Apple", store_id="fravega")

    features = build_pair_features(left, right)
    confidence = estimate_match_confidence(features)

    assert features.token_overlap >= 0.5
    assert features.numeric_token_agreement == 1
    assert features.accessory_mismatch is False
    assert confidence > 0.6


def test_pair_features_penalize_variant_and_numeric_mismatch() -> None:
    left = make_scored("Apple iPhone 15 128GB", store_id="mercado_libre")
    right = make_scored("Apple iPhone 15 Pro Max 256GB", store_id="fravega")

    features = build_pair_features(left, right)
    confidence = estimate_match_confidence(features)

    assert features.numeric_token_agreement < 1
    assert features.model_suffix_conflict is True
    assert features.storage_conflict is True
    assert confidence < 0.75


def test_pair_features_detect_screen_size_conflict() -> None:
    left = make_scored('Smart TV Samsung 50" Crystal UHD', store_id="mercado_libre")
    right = make_scored("Smart TV Samsung 55 Pulgadas Crystal UHD", store_id="fravega")

    features = build_pair_features(left, right)
    confidence = estimate_match_confidence(features)

    assert features.screen_size_conflict is True
    assert confidence < 0.55


def test_pair_features_detect_bundle_conflict() -> None:
    left = make_scored("Samsung Galaxy S24 FE 256GB", store_id="mercado_libre")
    right = make_scored("Samsung Galaxy S24 FE 256GB + Galaxy Buds4", store_id="fravega")

    features = build_pair_features(left, right)
    confidence = estimate_match_confidence(features)

    assert features.bundle_conflict is True
    assert confidence < 0.65


def test_pair_features_handle_accessory_and_missing_prices() -> None:
    left = make_scored("Apple iPhone 15 128GB", price=1000, store_id="mercado_libre")
    right = make_scored("Funda Spigen iPhone 15", price=100, store_id="fravega")
    right = right.model_copy(update={"product": right.product.model_copy(update={"price": None})})

    features = build_pair_features(left, right)
    confidence = estimate_match_confidence(features)

    assert features.accessory_mismatch is True
    assert features.price_ratio is None
    assert confidence < 0.5
