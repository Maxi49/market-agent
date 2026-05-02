import asyncio

import httpx

from app.link_guard import ProductLinkGuard
from app.models import (
    NormalizedProduct,
    Product,
    ProductAvailability,
    ProductCondition,
    ScoredProduct,
    ScoreBreakdown,
    TrustSignals,
)
from tests.typing_helpers import http_url


def test_product_link_guard_replaces_404_with_store_search_url() -> None:
    product = _product(
        store_id="fravega",
        store_name="Fravega",
        title="Smart TV Samsung 55 Pulgadas 4K",
        product_url="https://www.fravega.com/p/producto-caido-123/",
    )
    scored = _scored(product)

    class FakeClient:
        async def get(self, url: str, **kwargs):
            return httpx.Response(404, request=httpx.Request("GET", url))

    guarded = asyncio.run(ProductLinkGuard(client=FakeClient()).guard([scored]))  # type: ignore[arg-type]

    guarded_product = guarded[0].product
    assert str(guarded_product.product_url) == "https://www.fravega.com/l/?keyword=Smart+TV+Samsung+55+Pulgadas+4K"
    assert guarded_product.raw_metadata["original_product_url"] == "https://www.fravega.com/p/producto-caido-123/"
    assert guarded_product.raw_metadata["link_status"] == 404
    assert guarded_product.raw_metadata["link_reliability"] == "search_fallback_dead_link"
    assert "link_dead_fallback:fravega" in guarded[0].warnings


def test_product_link_guard_keeps_403_links_because_stores_can_block_bots() -> None:
    product = _product(
        store_id="mercado_libre",
        store_name="Mercado Libre",
        title="Smart TV 55",
        product_url="https://listado.mercadolibre.com.ar/smart-tv-55",
    )
    scored = _scored(product)

    class FakeClient:
        async def get(self, url: str, **kwargs):
            return httpx.Response(403, request=httpx.Request("GET", url))

    guarded = asyncio.run(ProductLinkGuard(client=FakeClient()).guard([scored]))  # type: ignore[arg-type]

    assert guarded[0].product.product_url == product.product_url
    assert guarded[0].warnings == []


def _product(store_id: str, store_name: str, title: str, product_url: str) -> Product:
    return Product(
        store_id=store_id,
        store_name=store_name,
        position=1,
        title=title,
        price=1000,
        currency="$",
        product_url=http_url(product_url),
        condition=ProductCondition.NEW,
        availability=ProductAvailability.IN_STOCK,
    )


def _scored(product: Product) -> ScoredProduct:
    return ScoredProduct(
        product=product,
        normalized=NormalizedProduct(
            canonical_key="smart-tv-samsung-55",
            normalized_title=product.title,
        ),
        score=80,
        score_breakdown=ScoreBreakdown(),
        trust_signals=TrustSignals(in_stock=True),
        explanation="ok",
    )
