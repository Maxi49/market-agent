from pathlib import Path
import asyncio
import json

import httpx
import pytest

from app.models import ProductAvailability, SearchLocation, SearchMode
from app.scrapers.base import ScraperError, extract_structured_product_data
from app.scrapers.amazon_serpapi import AmazonSerpApiAdapter
from app.scrapers.fravega import FravegaAdapter
from app.scrapers.vtex import CarrefourAdapter


def test_fravega_adapter_parses_search_card() -> None:
    html = Path("tests/fixtures/fravega_search.html").read_text()

    product = list(FravegaAdapter()._parse_products(html))[0]

    assert product.store_id == "fravega"
    assert product.title == "iPhone 15 128GB 6.1 Pulgadas"
    assert product.price == 1679000
    assert product.original_price == 2199999
    assert product.seller == "Affari"
    assert str(product.product_url) == "https://www.fravega.com/p/iphone-15-128gb-6-1-pulgadas-22101583/"


def test_vtex_adapter_resolves_normalized_product_state() -> None:
    html = Path("tests/fixtures/vtex_search.html").read_text()

    product = list(CarrefourAdapter()._parse_products(html))[0]

    assert product.store_id == "carrefour_ar"
    assert product.title == "Apple iPhone 15 128GB"
    assert product.price == 1599999
    assert product.original_price == 1899999
    assert product.discount == "16% OFF"
    assert product.installments == "12 cuotas de $ 133.333,25"
    assert product.availability == ProductAvailability.IN_STOCK
    assert product.raw_metadata["structured"]["sku"] == "1"


def test_vtex_adapter_parses_catalog_json_first_shape() -> None:
    data = json.loads(Path("tests/fixtures/vtex_catalog_search.json").read_text())

    product = CarrefourAdapter()._parse_catalog_products(data)[0]

    assert product.store_id == "carrefour_ar"
    assert product.title == "Celular iPhone 15 128gb"
    assert product.price == 1763076
    assert product.original_price == 1999999
    assert product.installments == "12 cuotas de $ 146.923,00"
    assert product.availability == ProductAvailability.IN_STOCK
    assert product.raw_metadata["structured"]["brand"] == "Apple"
    assert str(product.product_url) == "https://www.carrefour.com.ar/iphone-15-128gb-1751753/p"


def test_extract_structured_product_data_from_json_ld_and_meta() -> None:
    from bs4 import BeautifulSoup

    html = """
    <html>
      <head>
        <meta property="product:category" content="Smartphones" />
        <script type="application/ld+json">
          {
            "@context": "https://schema.org",
            "@type": "Product",
            "brand": {"@type": "Brand", "name": "Samsung"},
            "model": "Galaxy S24 FE",
            "mpn": "SM-S721B",
            "sku": "S24FE-256",
            "gtin13": "8806090000000"
          }
        </script>
      </head>
    </html>
    """

    structured = extract_structured_product_data(BeautifulSoup(html, "html.parser"))

    assert structured == {
        "brand": "Samsung",
        "model": "Galaxy S24 FE",
        "mpn": "SM-S721B",
        "sku": "S24FE-256",
        "gtin": "8806090000000",
        "category": "Smartphones",
    }


class FakeVtexAdapter(CarrefourAdapter):
    html_calls: int

    def __init__(self) -> None:
        super().__init__()
        object.__setattr__(self, "html_calls", 0)

    async def _search_json(self, encoded_query: str):
        return []

    async def _search_intelligent_json(self, encoded_query: str):
        return []

    async def _get_html(self, url: str) -> str:
        object.__setattr__(self, "html_calls", self.html_calls + 1)
        return Path("tests/fixtures/vtex_search.html").read_text()


def test_vtex_interactive_skips_html_fallback_when_json_is_empty() -> None:
    adapter = FakeVtexAdapter()

    with pytest.raises(ScraperError):
        asyncio.run(
            adapter.search("iphone 15", 3, SearchLocation(), mode=SearchMode.INTERACTIVE)
        )

    assert adapter.html_calls == 0


def test_vtex_deep_uses_html_fallback_when_json_is_empty() -> None:
    adapter = FakeVtexAdapter()

    products = asyncio.run(
        adapter.search("iphone 15", 3, SearchLocation(), mode=SearchMode.DEEP)
    )

    assert len(products) == 1
    assert adapter.html_calls == 1


class FakeSerpApiClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.requests = []

    async def request(self, method: str, url: httpx.URL | str, **kwargs):
        self.requests.append((method, url, kwargs))
        return httpx.Response(
            200,
            json=self.payload,
            request=httpx.Request(method, url),
        )


def test_amazon_serpapi_adapter_parses_organic_results(monkeypatch) -> None:
    monkeypatch.setenv("SERP_API_KEY", "token")
    client = FakeSerpApiClient({
        "organic_results": [
            {
                "position": 1,
                "title": "Sony WH-1000XM5 Wireless Noise Canceling Headphones",
                "asin": "B09XS7JWHH",
                "link": "https://www.amazon.com/dp/B09XS7JWHH",
                "thumbnail": "https://example.com/xm5.jpg",
                "extracted_price": 298.0,
                "price": "$298.00",
                "rating": 4.5,
                "reviews": 15523,
            }
        ]
    })
    adapter = AmazonSerpApiAdapter(client=client)

    products = asyncio.run(adapter.search("sony wh-1000xm5", 3, SearchLocation()))

    assert len(products) == 1
    product = products[0]
    assert product.store_id == "amazon_us"
    assert product.store_name == "Amazon US"
    assert product.position == 1
    assert product.title == "Sony WH-1000XM5 Wireless Noise Canceling Headphones"
    assert product.price == 298.0
    assert product.currency == "USD"
    assert product.rating == 4.5
    assert product.reviews_count == 15523
    assert str(product.product_url) == "https://www.amazon.com/dp/B09XS7JWHH"
    assert str(product.image_url) == "https://example.com/xm5.jpg"
    assert product.raw_metadata["provider"] == "serpapi"
    assert product.raw_metadata["engine"] == "amazon"
    assert product.raw_metadata["asin"] == "B09XS7JWHH"
    assert product.raw_metadata["amazon_domain"] == "amazon.com"
    assert product.raw_metadata["price_reliability"] == "medium"
    assert client.requests[0][2]["params"]["engine"] == "amazon"
    assert client.requests[0][2]["params"]["api_key"] == "token"
    assert client.requests[0][2]["params"]["k"] == "sony wh-1000xm5"


def test_amazon_serpapi_adapter_marks_text_price_low_reliability(monkeypatch) -> None:
    monkeypatch.setenv("SERPAPI_API_KEY", "token")
    monkeypatch.delenv("SERP_API_KEY", raising=False)
    client = FakeSerpApiClient({
        "results": [
            {
                "title": "Kindle Paperwhite",
                "asin": "B0CFPN47NY",
                "link": "https://www.amazon.com/dp/B0CFPN47NY",
                "price": "$159.99",
            }
        ]
    })
    adapter = AmazonSerpApiAdapter(client=client)

    products = asyncio.run(adapter.search("kindle paperwhite", 1, SearchLocation()))

    assert products[0].price == 159.99
    assert products[0].currency == "USD"
    assert products[0].raw_metadata["price_reliability"] == "low"
    assert products[0].raw_metadata["price_source"] == "price_text"


def test_amazon_serpapi_adapter_keeps_usd_source_when_extracted_price_is_converted(monkeypatch) -> None:
    monkeypatch.setenv("SERPAPI_API_KEY", "token")
    client = FakeSerpApiClient({
        "organic_results": [
            {
                "title": "96W Charger for MacBook Pro",
                "asin": "B0TEST",
                "link": "https://www.amazon.com/dp/B0TEST",
                "extracted_price": 18247.7,
                "price": "$12.99",
                "currency": "USD",
            }
        ]
    })
    adapter = AmazonSerpApiAdapter(client=client)

    products = asyncio.run(adapter.search("macbook charger", 1, SearchLocation()))

    assert products[0].price == 18247.7
    assert products[0].currency == "$"
    assert products[0].raw_metadata["price_ars"] == 18247.7
    assert products[0].raw_metadata["price_usd"] == 12.99
    assert products[0].raw_metadata["source_currency"] == "USD"
    assert products[0].raw_metadata["price_source"] == "extracted_price_converted_ars"


def test_amazon_serpapi_adapter_requires_serpapi_key(monkeypatch) -> None:
    monkeypatch.delenv("SERP_API_KEY", raising=False)
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    adapter = AmazonSerpApiAdapter(client=FakeSerpApiClient({"organic_results": []}))

    with pytest.raises(ScraperError, match="SERPAPI_API_KEY/SERP_API_KEY"):
        asyncio.run(adapter.search("iphone 15", 1, SearchLocation()))
