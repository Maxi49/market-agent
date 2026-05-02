from __future__ import annotations

import re
from dataclasses import dataclass, field
from os import getenv
from typing import Any, Protocol

import httpx
from dotenv import load_dotenv

from app.models import Product, ProductAvailability, ProductCondition, SearchLocation, SearchMode
from app.scrapers.base import ScraperError


load_dotenv()


class AsyncRequestClient(Protocol):
    async def request(
        self,
        method: str,
        url: httpx.URL | str,
        *,
        params: Any | None = None,
        timeout: Any | None = None,
    ) -> httpx.Response: ...


@dataclass(frozen=True)
class AmazonSerpApiAdapter:
    """Amazon US search backed by SerpApi's structured Amazon engine."""

    store_id: str = "amazon_us"
    store_name: str = "Amazon US"
    amazon_domain: str = field(default_factory=lambda: getenv("AMAZON_SERPAPI_DOMAIN", "amazon.com"))
    language: str = field(default_factory=lambda: getenv("AMAZON_SERPAPI_LANGUAGE", "en_US"))
    shipping_location: str | None = field(
        default_factory=lambda: getenv("AMAZON_SERPAPI_SHIPPING_LOCATION", "ar")
    )
    timeout_seconds: float = 20.0
    client: AsyncRequestClient | None = field(default=None, compare=False)

    async def search(
        self,
        query: str,
        limit: int = 10,
        location: SearchLocation | None = None,
        mode: SearchMode = SearchMode.INTERACTIVE,
    ) -> list[Product]:
        api_key = _serpapi_key()
        if not api_key:
            raise ScraperError("SERPAPI_API_KEY/SERP_API_KEY no configurado para Amazon SerpApi.")

        params = {
            "engine": "amazon",
            "amazon_domain": self.amazon_domain,
            "k": query,
            "api_key": api_key,
            "language": self.language,
        }
        if self.shipping_location:
            params["shipping_location"] = self.shipping_location

        response = await self._request(params)
        data = response.json()
        raw_results = _as_list(data.get("organic_results")) or _as_list(data.get("results"))

        products: list[Product] = []
        for result in raw_results:
            if len(products) >= limit:
                break
            product = self._product_from_result(result, query, len(products) + 1)
            if product is not None:
                products.append(product)

        return products

    async def _request(self, params: dict[str, str]) -> httpx.Response:
        try:
            if self.client is not None:
                response = await self.client.request(
                    "GET",
                    "https://serpapi.com/search.json",
                    params=params,
                    timeout=self.timeout_seconds,
                )
            else:
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    response = await client.request("GET", "https://serpapi.com/search.json", params=params)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            message = str(exc) or exc.__class__.__name__
            raise ScraperError(f"No se pudo consultar Amazon via SerpApi: {message}") from exc
        return response

    def _product_from_result(self, result: dict[str, Any], query: str, position: int) -> Product | None:
        title = _string(result.get("title") or result.get("name"))
        url = _string(result.get("link") or result.get("product_link"))
        if not title or not url:
            return None

        price, currency, price_source, price_reliability, price_metadata = _price_from_result(result)
        asin = _string(result.get("asin"))
        search_position = _int(result.get("position")) or position
        shipping = _string(result.get("delivery") or result.get("shipping"))

        return Product(
            store_id=self.store_id,
            store_name=self.store_name,
            position=position,
            title=title,
            price=price,
            currency=currency,
            original_price=None,
            discount=_string(result.get("discount")),
            installments=None,
            shipping=shipping,
            seller=_string(result.get("seller")),
            rating=_number(result.get("rating") or result.get("stars")),
            reviews_count=_int(result.get("reviews") or result.get("reviews_count")),
            image_url=_string(result.get("thumbnail") or result.get("image")),  # type: ignore[arg-type]
            product_url=url,  # type: ignore[arg-type]
            condition=ProductCondition.UNKNOWN,
            availability=ProductAvailability.UNKNOWN,
            sponsored=bool(result.get("sponsored", False)),
            raw_metadata={
                "provider": "serpapi",
                "engine": "amazon",
                "asin": asin,
                "amazon_domain": self.amazon_domain,
                "position": search_position,
                "reliability": "medium",
                "price_reliability": price_reliability,
                "price_source": price_source,
                "search_query": query,
                **price_metadata,
            },
        )


def has_amazon_serpapi_credentials() -> bool:
    return bool(_serpapi_key())


def _price_from_result(result: dict[str, Any]) -> tuple[float | None, str | None, str | None, str, dict[str, Any]]:
    extracted = _number(result.get("extracted_price"))
    text_price = _number(result.get("price"))
    text_currency = _currency_from_price_text(_string(result.get("price")))
    if (
        extracted is not None
        and text_price is not None
        and text_currency == "USD"
        and abs(extracted - text_price) > 1
    ):
        return (
            extracted,
            "$",
            "extracted_price_converted_ars",
            "medium",
            {
                "price_ars": extracted,
                "price_usd": text_price,
                "source_currency": "USD",
            },
        )

    if extracted is not None:
        currency = _currency(result)
        return (
            extracted,
            currency,
            "extracted_price",
            "medium",
            _price_metadata(extracted, currency),
        )

    if text_price is not None:
        currency = _currency(result)
        return text_price, currency, "price_text", "low", _price_metadata(text_price, currency)

    return None, None, None, "low", {}


def _currency(result: dict[str, Any]) -> str:
    value = _string(result.get("currency"))
    if value:
        return value
    price = _string(result.get("price")) or ""
    if "$" in price:
        return "USD"
    return "USD"


def _currency_from_price_text(value: str | None) -> str | None:
    if not value:
        return None
    if "US$" in value.upper() or "$" in value:
        return "USD"
    return None


def _price_metadata(price: float, currency: str | None) -> dict[str, Any]:
    if currency in {"$", "ARS"}:
        return {"price_ars": price}
    if currency == "USD":
        return {"price_usd": price, "source_currency": "USD"}
    return {}


def _as_list(value: object) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _string(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = re.sub(r"[^\d,.]", "", value)
        if not cleaned:
            return None
        if "," in cleaned and "." in cleaned:
            cleaned = cleaned.replace(",", "")
        elif "," in cleaned:
            cleaned = cleaned.replace(".", "").replace(",", ".")
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _int(value: object) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _serpapi_key() -> str | None:
    return getenv("SERP_API_KEY") or getenv("SERPAPI_API_KEY")
