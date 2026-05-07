from __future__ import annotations

import re
from dataclasses import dataclass, field
from os import getenv
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from dotenv import load_dotenv

from app.models import Product, ProductAvailability, ProductCondition, SearchLocation, SearchMode
from app.scrapers.base import ScraperError


load_dotenv()

MERCADO_LIBRE_PRODUCT_HOSTS = frozenset({
    "mercadolibre.com.ar",
    "www.mercadolibre.com.ar",
    "articulo.mercadolibre.com.ar",
    "listado.mercadolibre.com.ar",
})


@dataclass(frozen=True)
class MercadoLibreSearchIndexAdapter:
    store_id: str = "mercado_libre"
    store_name: str = "Mercado Libre"
    timeout_seconds: float = 20.0
    client: httpx.AsyncClient | None = field(default=None, compare=False)

    async def search(
        self,
        query: str,
        limit: int = 10,
        location: SearchLocation | None = None,
        mode: SearchMode = SearchMode.INTERACTIVE,
    ) -> list[Product]:
        num = min(max(limit * 2, 5), 10)
        raw_results = await self._search_serpapi(query.strip(), num)

        products: list[Product] = []
        for result in raw_results:
            if len(products) >= limit:
                break
            product = self._product_from_result(result, query, len(products) + 1)
            if product is not None:
                products.append(product)

        return products

    async def _search_serpapi(
        self,
        search_query: str,
        num: int,
    ) -> list[dict[str, Any]]:
        api_key = _serpapi_key()
        if not api_key:
            raise ScraperError("SERPAPI_API_KEY/SERP_API_KEY no configurado para Mercado Libre.")

        # Agregar "mercadolibre" fuerza a Google Shopping a priorizar resultados
        # de ML sin importar la especificidad de la query. Sin esto, queries
        # específicas como "aire acondicionado split inverter" no devuelven ML.
        serpapi_query = f"{search_query} mercadolibre"

        params = {
            "engine": "google_shopping",
            "q": serpapi_query,
            "api_key": api_key,
            "google_domain": "google.com.ar",
            "gl": "ar",
            "hl": "es",
            "num": str(num),
        }

        response = await self._request(
            "GET",
            "https://serpapi.com/search.json",
            params=params,
        )
        return _as_list(response.json().get("shopping_results"))

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        try:
            if self.client is not None:
                response = await self.client.request(method, url, timeout=self.timeout_seconds, **kwargs)
            else:
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    response = await client.request(method, url, **kwargs)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            message = str(exc) or exc.__class__.__name__
            raise ScraperError(f"No se pudo consultar Mercado Libre: {message}") from exc
        return response

    def _product_from_result(self, result: dict[str, Any], query: str, position: int) -> Product | None:
        title = _string(result.get("title"))
        url = _string(result.get("product_link") or result.get("link"))
        if not title or not url:
            return None
        if not _is_mercado_libre_result(result, url):
            return None

        snippet = _string(result.get("snippet")) or ""
        price, currency, source = _price_from_result(result)
        text = f"{title} {snippet}"
        price_reliability = "medium" if source == "shopping_extracted" else "low" if source else "unknown"
        product_url, link_reliability = _product_url_from_result(title, url)

        return Product(
            store_id=self.store_id,
            store_name=self.store_name,
            position=position,
            title=_clean_title(title),
            price=price,
            currency=currency,
            original_price=None,
            discount=None,
            installments=None,
            shipping=_string(result.get("delivery")) or _shipping_hint(text),
            seller=None,
            rating=_number(result.get("rating")),
            reviews_count=_int(result.get("reviews")),
            image_url=_string(result.get("thumbnail")),  # type: ignore[arg-type]
            product_url=product_url,  # type: ignore[arg-type]
            condition=_condition(f"{text} {result.get('second_hand_condition') or ''}"),
            availability=ProductAvailability.UNKNOWN,
            sponsored=False,
            raw_metadata={
                "provider": "serpapi",
                "provider_family": "search_index",
                "engine": "google_shopping",
                "reliability": "low",
                "price_reliability": price_reliability,
                "price_source": source,
                "link_reliability": link_reliability,
                "source": result.get("source"),
                "product_id": result.get("product_id"),
                "serpapi_product_api": result.get("serpapi_product_api"),
                "immersive_product_page_token": result.get("immersive_product_page_token"),
                "serpapi_immersive_product_api": result.get("serpapi_immersive_product_api"),
                "search_query": query,
                "snippet": snippet,
                "search_position": result.get("position"),
            },
        )


def _serpapi_key() -> str | None:
    return getenv("SERPAPI_API_KEY") or getenv("SERP_API_KEY")


def _is_product_url(url: str) -> bool:
    hostname = urlparse(url).hostname or ""
    return hostname in MERCADO_LIBRE_PRODUCT_HOSTS


def _is_mercado_libre_result(result: dict[str, Any], url: str) -> bool:
    source = re.sub(r"\s+", "", str(result.get("source", "")).lower())
    return "mercadolibre" in source or _is_product_url(url)


def _product_url_from_result(title: str, url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    hostname = parsed.hostname or ""

    # Unwrap google.com redirect URLs (google.com/url?q= or ?url=)
    if hostname in ("www.google.com", "google.com", "www.google.com.ar", "google.com.ar") and parsed.path == "/url":
        qs = parse_qs(parsed.query)
        candidate = (qs.get("url") or qs.get("q") or [None])[0]
        if candidate and urlparse(candidate).hostname:
            url = candidate

    if _is_product_url(url):
        return url, "direct"

    # Use whatever link SerpAPI returned rather than constructing a fake URL.
    # product_link from google_shopping points to the Google Shopping product page,
    # which is still a valid, real link to the product.
    return url, "google_product"


def _price_from_result(result: dict[str, Any]) -> tuple[float | None, str | None, str | None]:
    extracted = _number(result.get("extracted_price"))
    if extracted is not None:
        currency = _string(result.get("currency")) or "$"
        return extracted, currency, "shopping_extracted"

    price = _price_from_text(_string(result.get("price")) or "")
    return (price, "$", "snippet") if price is not None else (None, None, None)


def _price_from_text(text: str) -> float | None:
    match = re.search(r"\$\s*([0-9]{1,3}(?:[.\s][0-9]{3})*(?:,[0-9]{1,2})?|[0-9]+)", text)
    if not match:
        return None
    raw = match.group(1).replace(" ", "").replace(".", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def _clean_title(title: str) -> str:
    return re.sub(r"\s*\|\s*MercadoLibre.*$", "", title, flags=re.IGNORECASE).strip()


def _condition(text: str) -> ProductCondition:
    normalized = text.lower()
    if "reacondicionado" in normalized or "renovado" in normalized:
        return ProductCondition.REFURBISHED
    if "usado" in normalized or "segunda mano" in normalized:
        return ProductCondition.USED
    if "nuevo" in normalized:
        return ProductCondition.NEW
    return ProductCondition.UNKNOWN


def _shipping_hint(text: str) -> str | None:
    return "Envio gratis" if re.search(r"env[ií]o gratis|llega gratis", text, flags=re.IGNORECASE) else None


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
        cleaned = re.sub(r"[^\d,.]", "", value).replace(".", "").replace(",", ".")
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _int(value: object) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None
