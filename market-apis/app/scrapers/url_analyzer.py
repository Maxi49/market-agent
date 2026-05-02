from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict

from app.scrapers.base import (
    clean_text,
    detect_availability,
    detect_condition,
    number_from_text,
    tag_attr,
)
from app.models import ProductAvailability, ProductCondition

_TIMEOUT = 15.0
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
}

_STORE_NAMES: dict[str, str] = {
    "fravega.com": "Frávega",
    "mercadolibre.com.ar": "Mercado Libre",
    "mercadolibre.com": "Mercado Libre",
    "carrefour.com.ar": "Carrefour",
    "cetrogar.com.ar": "Cetrogar",
    "easy.com.ar": "Easy",
    "samsung.com": "Samsung",
    "sony.com.ar": "Sony",
    "bgh.com.ar": "BGH",
    "tiendamia.com": "Tiendamia",
    "tiendamia.com.ar": "Tiendamia",
    "musimundo.com": "Musimundo",
    "amazon.com": "Amazon US",
}


class ProductAnalysis(BaseModel):
    model_config = ConfigDict(frozen=False)

    url: str
    store: str | None = None
    title: str | None = None
    price: float | None = None
    currency: str | None = None
    original_price: float | None = None
    discount: str | None = None
    brand: str | None = None
    description: str | None = None
    condition: str | None = None
    availability: str | None = None
    installments: str | None = None
    image_url: str | None = None
    error: str | None = None


async def analyze_url(url: str) -> ProductAnalysis:
    result = ProductAnalysis(url=url)
    result.store = _detect_store(url)

    try:
        async with httpx.AsyncClient(headers=_HEADERS, follow_redirects=True, timeout=_TIMEOUT) as client:
            response = await client.get(url)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        result.error = f"No se pudo acceder a la URL: {exc}"
        return result

    soup = BeautifulSoup(response.text, "html.parser")

    # 1. Try LD+JSON Product schema (most reliable)
    _extract_from_ld_json(soup, result)

    # 2. Fill gaps with OpenGraph tags
    _extract_from_og(soup, result)

    # 3. Fill remaining gaps from meta/title
    _extract_from_meta(soup, result)

    # 4. Normalize condition / availability strings
    if result.condition is None and result.title:
        cond = detect_condition(result.title, result.description or "")
        result.condition = cond.value if cond != ProductCondition.UNKNOWN else None

    if result.availability is None and result.title:
        raw = soup.get_text(" ", strip=True)[:2000]
        avail = detect_availability(raw)
        result.availability = avail.value if avail != ProductAvailability.UNKNOWN else None

    return result


def _detect_store(url: str) -> str | None:
    host = urlparse(url).hostname or ""
    for domain, name in _STORE_NAMES.items():
        if domain in host:
            return name
    return host or None


def _extract_from_ld_json(soup: BeautifulSoup, result: ProductAnalysis) -> None:
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue

        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            graph = item.get("@graph", [])
            candidates = [item] + (graph if isinstance(graph, list) else [])
            for node in candidates:
                if not isinstance(node, dict):
                    continue
                type_ = node.get("@type", "")
                if "Product" not in (type_ if isinstance(type_, str) else " ".join(type_)):
                    continue
                _fill_from_product_node(node, result)
                return


def _fill_from_product_node(node: dict[str, Any], result: ProductAnalysis) -> None:
    if result.title is None:
        result.title = clean_text(str(node.get("name", ""))) or None

    if result.brand is None:
        brand = node.get("brand") or node.get("manufacturer")
        if isinstance(brand, dict):
            result.brand = clean_text(str(brand.get("name", ""))) or None
        elif isinstance(brand, str):
            result.brand = clean_text(brand) or None

    if result.description is None:
        desc = node.get("description", "")
        if desc:
            result.description = clean_text(str(desc))[:300] or None

    if result.image_url is None:
        img = node.get("image")
        if isinstance(img, list) and img:
            img = img[0]
        if isinstance(img, dict):
            img = img.get("url", "")
        if isinstance(img, str) and img.startswith("http"):
            result.image_url = img

    offers = node.get("offers") or node.get("Offers")
    if not offers:
        return
    if isinstance(offers, list):
        offers = offers[0]
    if not isinstance(offers, dict):
        return

    if result.price is None:
        raw_price = offers.get("price") or offers.get("lowPrice")
        if raw_price is not None:
            result.price = number_from_text(str(raw_price))

    if result.currency is None:
        currency_code = offers.get("priceCurrency", "")
        result.currency = "USD" if currency_code == "USD" else "$" if currency_code in ("ARS", "") else currency_code or None

    if result.availability is None:
        avail_url = str(offers.get("availability", ""))
        if "InStock" in avail_url:
            result.availability = "in_stock"
        elif "OutOfStock" in avail_url or "Discontinued" in avail_url:
            result.availability = "out_of_stock"

    if result.condition is None:
        cond_url = str(offers.get("itemCondition", ""))
        if "NewCondition" in cond_url:
            result.condition = "new"
        elif "UsedCondition" in cond_url or "RefurbishedCondition" in cond_url:
            result.condition = "used"


def _extract_from_og(soup: BeautifulSoup, result: ProductAnalysis) -> None:
    def og(prop: str) -> str | None:
        tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
        return tag_attr(tag, "content") if tag else None  # type: ignore[arg-type]

    if result.title is None:
        result.title = og("og:title") or og("twitter:title")
    if result.description is None:
        raw = og("og:description") or og("twitter:description") or og("description")
        result.description = clean_text(raw)[:300] if raw else None
    if result.image_url is None:
        result.image_url = og("og:image") or og("twitter:image")
    if result.price is None:
        raw_price = og("product:price:amount") or og("og:price:amount")
        if raw_price:
            result.price = number_from_text(raw_price)
    if result.currency is None:
        cur = og("product:price:currency") or og("og:price:currency")
        if cur:
            result.currency = "USD" if cur == "USD" else "$"


def _extract_from_meta(soup: BeautifulSoup, result: ProductAnalysis) -> None:
    if result.title is None:
        title_tag = soup.find("title")
        if title_tag:
            result.title = clean_text(title_tag.get_text())[:120] or None

    # Try to extract price from visible text if still missing
    if result.price is None and result.title:
        raw = soup.get_text(" ", strip=True)
        prices = []
        for part in raw.split("$")[1:8]:
            p = number_from_text(part)
            if p and 100 < p < 100_000_000:
                prices.append(p)
        if prices:
            result.price = min(prices)
            result.currency = "$"

    # Installments — look for cuotas pattern
    if result.installments is None:
        raw = soup.get_text(" ", strip=True)[:3000]
        match = re.search(r"\d+\s*cuotas?\s*(?:sin\s*interés|(?:de\s*\$[\d.,]+))?", raw, re.IGNORECASE)
        if match:
            result.installments = clean_text(match.group(0))

    # Discount
    if result.discount is None:
        raw = soup.get_text(" ", strip=True)[:2000]
        match = re.search(r"(\d{1,2})\s*%\s*(?:OFF|off|descuento)", raw)
        if match:
            result.discount = f"{match.group(1)}% OFF"
