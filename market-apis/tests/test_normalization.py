from typing import Any

from app.models import Product
from app.normalization import ProductNormalizer
from tests.typing_helpers import http_url


def product(title: str, raw_metadata: dict[str, Any] | None = None) -> Product:
    return Product(
        store_id="test",
        store_name="Test",
        position=1,
        title=title,
        price=100,
        currency="$",
        product_url=http_url("https://example.com/product"),
        raw_metadata=raw_metadata or {},
    )


def test_equivalent_iphone_titles_share_canonical_key() -> None:
    normalizer = ProductNormalizer()

    first = normalizer.normalize(product("Apple iPhone 15 128 GB Negro"))
    second = normalizer.normalize(product("Celular iPhone 15 128gb negro"))

    assert first.canonical_key == second.canonical_key
    assert first.canonical_key == "apple-iphone-15-128gb"


def test_accessories_are_marked() -> None:
    normalized = ProductNormalizer().normalize(
        product("Funda Spigen Ultra Hybrid Magfit para iPhone 15 Pro")
    )

    assert normalized.is_accessory is True


def test_samsung_s_pen_replacement_is_marked_as_accessory() -> None:
    normalized = ProductNormalizer().normalize(
        product("S Pen Repuesto SAMSUNG Galaxy S24 Ultra")
    )

    assert normalized.is_accessory is True


def test_structured_metadata_has_priority_for_brand_model_and_category() -> None:
    normalized = ProductNormalizer().normalize(
        product(
            "Celular FooBar X 128GB",
            raw_metadata={
                "structured": {
                    "brand": "Samsung",
                    "model": "Galaxy S24 FE",
                    "category": "Smartphones Samsung",
                }
            },
        )
    )

    assert normalized.brand == "samsung"
    assert normalized.model == "galaxy s24 fe"
    assert normalized.category == "smartphones_samsung"
    assert normalized.canonical_key == "samsung-galaxy-s24-fe-128gb"


def test_extracts_screen_size_ram_storage_cpu_gpu_and_bundle_flags() -> None:
    notebook = ProductNormalizer().normalize(
        product("Notebook Lenovo IdeaPad i5 16GB RAM 512GB SSD RTX 4050")
    )
    tv = ProductNormalizer().normalize(product('Smart TV Samsung 55" QLED 4K'))
    combo = ProductNormalizer().normalize(product("Combo Samsung S24 FE 256GB + Galaxy Buds"))

    assert notebook.attributes["storage"] == "512GB"
    assert notebook.attributes["ram"] == "16GB"
    assert notebook.attributes["cpu"] == "i5"
    assert notebook.attributes["gpu"] == "rtx 4050"
    assert tv.attributes["screen_size"] == '55"'
    assert combo.attributes["bundle"] == "true"
    assert combo.raw_compact["is_bundle"] is True
