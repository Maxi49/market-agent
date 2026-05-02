from app.routing import StoreRouter


AVAILABLE = ["mercado_libre", "fravega", "samsung_ar", "carrefour_ar", "cetrogar_ar"]
AVAILABLE_WITH_AMAZON = [*AVAILABLE, "amazon_us"]


def test_iphone_query_excludes_samsung() -> None:
    decision = StoreRouter(AVAILABLE).route("iphone 15")

    assert "samsung_ar" not in decision.selected_store_ids
    assert "carrefour_ar" not in decision.selected_store_ids
    assert "samsung_ar" in decision.excluded_store_ids
    assert "carrefour_ar" in decision.excluded_store_ids
    assert decision.reasons["carrefour_ar"] == "blocked_by_store_profile"
    assert decision.query_understanding.detected_brands == ["apple"]


def test_galaxy_query_includes_samsung() -> None:
    decision = StoreRouter(AVAILABLE).route("galaxy s24")

    assert "samsung_ar" in decision.selected_store_ids
    assert "samsung" in decision.query_understanding.detected_brands


def test_generic_smart_tv_uses_electro_retailers() -> None:
    decision = StoreRouter(AVAILABLE).route("smart tv 55")

    assert set(decision.selected_store_ids) == {
        "mercado_libre",
        "fravega",
        "carrefour_ar",
        "samsung_ar",
        "cetrogar_ar",
    }
    assert decision.query_understanding.detected_category == "tv"


def test_amazon_is_strong_for_international_tech() -> None:
    decision = StoreRouter(AVAILABLE_WITH_AMAZON).route("sony wh-1000xm5 headphones")

    assert "amazon_us" in decision.selected_store_ids
    assert decision.reasons["amazon_us"] == "selected_by_strong_profile"


def test_amazon_is_blocked_for_supermarket_queries() -> None:
    decision = StoreRouter(AVAILABLE_WITH_AMAZON).route("yerba mate")

    assert "amazon_us" not in decision.selected_store_ids
    assert "amazon_us" in decision.excluded_store_ids
    assert decision.reasons["amazon_us"] == "blocked_by_store_profile"


def test_accessory_query_with_device_name_is_classified_as_accessory() -> None:
    decision = StoreRouter(AVAILABLE_WITH_AMAZON).route(
        "adaptador Apple 70W USB-C cargador MacBook Pro original"
    )

    assert decision.query_understanding.detected_category == "accessories"
