from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Tag

from app.models import Product, ProductAvailability, ProductCondition, SearchLocation, SearchMode


class ScraperError(RuntimeError):
    """Raised when a store cannot be fetched or parsed."""


class StoreAdapter(Protocol):
    @property
    def store_id(self) -> str: ...
    @property
    def store_name(self) -> str: ...

    async def search(
        self,
        query: str,
        limit: int,
        location: SearchLocation,
        /,
        mode: SearchMode = SearchMode.INTERACTIVE,
    ) -> list[Product]:
        ...


@dataclass(frozen=True)
class HttpStoreAdapter:
    store_id: str
    store_name: str
    base_url: str
    timeout_seconds: float = 12.0
    client: httpx.AsyncClient | None = field(default=None, compare=False)

    async def _get_html(self, url: str) -> str:
        response = await self._get_response(
            url,
            accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        )
        if "text/html" not in response.headers.get("content-type", ""):
            raise ScraperError(f"{self.store_name} respondió con contenido inesperado.")

        return response.text

    async def _get_response(self, url: str, accept: str) -> httpx.Response:
        headers = {
            "Accept": accept,
            "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
        }

        try:
            if self.client is not None:
                response = await self.client.get(
                    url,
                    headers=headers,
                    follow_redirects=True,
                    timeout=self.timeout_seconds,
                )
            else:
                async with httpx.AsyncClient(
                    headers=headers,
                    follow_redirects=True,
                    timeout=self.timeout_seconds,
                ) as client:
                    response = await client.get(url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ScraperError(f"No se pudo consultar {self.store_name}: {exc}") from exc

        return response

    def _absolute_url(self, url: str | None) -> str | None:
        if not url:
            return None
        if url.startswith("//"):
            return f"https:{url}"
        return urljoin(self.base_url, url)


def tag_attr(tag: Tag, attr: str) -> str | None:
    """Return a Tag attribute as str, or None — avoids _AttributeValue typing issues."""
    value = tag.get(attr)
    if value is None:
        return None
    return str(value) if not isinstance(value, list) else " ".join(value)


def extract_structured_product_data(node: BeautifulSoup | Tag) -> dict[str, str]:
    """Extract normalized product metadata from JSON-LD and common meta tags."""
    structured: dict[str, str] = {}
    for script in node.select('script[type="application/ld+json"]'):
        raw = script.string or script.get_text("", strip=True)
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        product = _find_json_ld_product(payload)
        if product:
            structured.update(_structured_product_fields(product))

    structured.update(_meta_product_fields(node))
    return {key: value for key, value in structured.items() if value}


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def optional_text(node: Tag | None) -> str | None:
    if node is None:
        return None
    text = clean_text(node.get_text(" ", strip=True))
    return text or None


def number_from_text(text: str) -> float | None:
    match = re.search(r"(\d+(?:[.,]\d+)*)", text)
    if not match:
        return None

    raw = match.group(1)
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    else:
        raw = raw.replace(".", "")

    try:
        return float(raw)
    except ValueError:
        return None


def detect_condition(title: str, raw_text: str = "") -> ProductCondition:
    haystack = f"{title} {raw_text}".lower()
    if "reacondicionado" in haystack or "refurbished" in haystack:
        return ProductCondition.REFURBISHED
    if re.search(r"\busad[oa]s?\b", haystack):
        return ProductCondition.USED
    if "nuevo" in haystack or "new" in haystack:
        return ProductCondition.NEW
    return ProductCondition.UNKNOWN


def detect_availability(raw_text: str) -> ProductAvailability:
    haystack = raw_text.lower()
    if any(token in haystack for token in ["sin stock", "agotado", "no disponible"]):
        return ProductAvailability.OUT_OF_STOCK
    if any(token in haystack for token in ["comprar", "agregar al carrito", "en stock"]):
        return ProductAvailability.IN_STOCK
    return ProductAvailability.UNKNOWN


def _find_json_ld_product(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, list):
        for item in payload:
            product = _find_json_ld_product(item)
            if product:
                return product
        return None
    if not isinstance(payload, dict):
        return None

    if _json_ld_type_matches(payload.get("@type"), "Product"):
        return payload

    graph = payload.get("@graph")
    if isinstance(graph, list):
        for item in graph:
            product = _find_json_ld_product(item)
            if product:
                return product
    return None


def _json_ld_type_matches(value: Any, expected: str) -> bool:
    if isinstance(value, str):
        return value.lower() == expected.lower()
    if isinstance(value, list):
        return any(_json_ld_type_matches(item, expected) for item in value)
    return False


def _structured_product_fields(product: dict[str, Any]) -> dict[str, str]:
    fields = {
        "brand": _structured_value(product.get("brand")),
        "model": _structured_value(product.get("model")),
        "mpn": _structured_value(product.get("mpn")),
        "sku": _structured_value(product.get("sku")),
        "category": _structured_value(product.get("category")),
        "gtin": _first_structured_value(
            product.get(key)
            for key in ["gtin", "gtin8", "gtin12", "gtin13", "gtin14"]
        ),
    }
    return {key: clean_text(value) for key, value in fields.items() if value}


def _meta_product_fields(node: BeautifulSoup | Tag) -> dict[str, str]:
    selectors = {
        "brand": ['meta[property="product:brand"]', 'meta[name="brand"]'],
        "model": ['meta[property="product:model"]', 'meta[name="model"]'],
        "mpn": ['meta[property="product:mpn"]', 'meta[name="mpn"]'],
        "gtin": ['meta[property="product:gtin"]', 'meta[name="gtin"]'],
        "sku": ['meta[property="product:retailer_item_id"]', 'meta[name="sku"]'],
        "category": ['meta[property="product:category"]', 'meta[name="category"]'],
    }
    fields: dict[str, str] = {}
    for key, candidates in selectors.items():
        for selector in candidates:
            meta = node.select_one(selector)
            value = tag_attr(meta, "content") if meta else None
            if value:
                fields[key] = clean_text(value)
                break
    return fields


def _structured_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        for key in ["name", "@id", "value"]:
            nested = _structured_value(value.get(key))
            if nested:
                return nested
        return None
    if isinstance(value, list):
        return _first_structured_value(value)
    return str(value).strip() or None


def _first_structured_value(values) -> str | None:
    for value in values:
        resolved = _structured_value(value)
        if resolved:
            return resolved
    return None
