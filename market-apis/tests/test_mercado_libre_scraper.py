import asyncio

import pytest

from app.models import SearchLocation
from app.scrapers.search_index_mercado_libre import MercadoLibreSearchIndexAdapter


def test_search_index_adapter_uses_google_shopping_and_filters_mercado_libre_source(monkeypatch) -> None:
    monkeypatch.setenv("SERP_API_KEY", "token")

    class FakeClient:
        def __init__(self) -> None:
            self.requests = []

        async def request(self, method: str, url: str, timeout: float, **kwargs):
            self.requests.append((method, url, kwargs))

            class Response:
                def raise_for_status(self) -> None:
                    return None

                def json(self) -> dict:
                    return {
                        "shopping_results": [
                            {
                                "position": 1,
                                "title": "Apple iPhone 15 Pro (128 GB)",
                                "source": "Mercadolibre.com.ar",
                                "product_link": "https://www.google.com.ar/search?ibp=oshop&q=iphone+15+pro&prds=pid:123",
                                "extracted_price": 1090000,
                                "price": "$1.090.000",
                                "second_hand_condition": "de segunda mano",
                                "delivery": "Envio gratis",
                                "thumbnail": "https://example.com/iphone.jpg",
                            },
                            {
                                "position": 2,
                                "title": "Apple iPhone 15 Pro (128 GB)",
                                "source": "Otra tienda",
                                "product_link": "https://example.com/iphone",
                                "extracted_price": 999999,
                            },
                        ]
                    }

            return Response()

    client = FakeClient()
    adapter = MercadoLibreSearchIndexAdapter(client=client)  # type: ignore[arg-type]
    products = asyncio.run(adapter.search("iphone 15 pro", 5, SearchLocation()))

    assert len(products) == 1
    assert products[0].title == "Apple iPhone 15 Pro (128 GB)"
    assert products[0].price == 1090000
    assert products[0].condition == "used"
    assert products[0].shipping == "Envio gratis"
    assert products[0].raw_metadata["provider"] == "serpapi"
    assert products[0].raw_metadata["provider_family"] == "search_index"
    assert products[0].raw_metadata["engine"] == "google_shopping"
    assert products[0].raw_metadata["price_source"] == "shopping_extracted"
    assert str(products[0].product_url) == "https://www.google.com.ar/search?ibp=oshop&q=iphone+15+pro&prds=pid:123"
    assert products[0].raw_metadata["link_reliability"] == "google_product"
    assert "google_product_link" not in products[0].raw_metadata
    params = client.requests[0][2]["params"]
    assert params["engine"] == "google_shopping"
    assert params["google_domain"] == "google.com.ar"
    assert params["gl"] == "ar"
    assert params["hl"] == "es"
    assert "location" not in params


def test_search_index_adapter_does_not_match_prices_from_other_shopping_titles(monkeypatch) -> None:
    monkeypatch.setenv("SERP_API_KEY", "token")

    class FakeClient:
        async def request(self, method: str, url: str, timeout: float, **kwargs):
            class Response:
                def raise_for_status(self) -> None:
                    return None

                def json(self) -> dict:
                    return {
                        "shopping_results": [
                            {
                                "position": 1,
                                "title": "iPhone 15 Pro Max 256GB",
                                "source": "MercadoLibre",
                                "product_link": "https://www.mercadolibre.com.ar/iphone-15-pro-max/p/MLA456",
                            },
                            {
                                "position": 2,
                                "title": "iPhone 15 Pro 128GB",
                                "source": "Otra tienda",
                                "product_link": "https://example.com/iphone-15-pro",
                                "extracted_price": 89999,
                            },
                        ]
                    }

            return Response()

    adapter = MercadoLibreSearchIndexAdapter(client=FakeClient())  # type: ignore[arg-type]
    products = asyncio.run(adapter.search("iphone 15 pro", 1, SearchLocation()))

    assert len(products) == 1
    assert products[0].price is None
    assert products[0].raw_metadata["price_source"] is None
    assert products[0].raw_metadata["price_reliability"] == "unknown"


def test_search_index_adapter_preserves_direct_mercado_libre_links(monkeypatch) -> None:
    monkeypatch.setenv("SERP_API_KEY", "token")

    class FakeClient:
        async def request(self, method: str, url: str, timeout: float, **kwargs):
            class Response:
                def raise_for_status(self) -> None:
                    return None

                def json(self) -> dict:
                    return {
                        "shopping_results": [
                            {
                                "position": 1,
                                "title": "Samsung Smart TV 55 4K",
                                "source": "MercadoLibre",
                                "product_link": "https://www.mercadolibre.com.ar/samsung-smart-tv-55-4k/p/MLA789",
                                "price": "$750.000",
                            },
                        ]
                    }

            return Response()

    adapter = MercadoLibreSearchIndexAdapter(client=FakeClient())  # type: ignore[arg-type]
    products = asyncio.run(adapter.search("smart tv 55 4k", 1, SearchLocation()))

    assert len(products) == 1
    assert str(products[0].product_url) == "https://www.mercadolibre.com.ar/samsung-smart-tv-55-4k/p/MLA789"
    assert products[0].raw_metadata["link_reliability"] == "direct"
