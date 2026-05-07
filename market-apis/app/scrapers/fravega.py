from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import quote_plus

from bs4 import BeautifulSoup, Tag
from pydantic import HttpUrl, TypeAdapter, ValidationError

from app.models import Product, ProductAvailability, ProductCondition, SearchLocation, SearchMode
from app.scrapers.url_analyzer import analyze_url
from app.scrapers.base import (
    HttpStoreAdapter,
    clean_text,
    detect_availability,
    detect_condition,
    extract_structured_product_data,
    number_from_text,
    optional_text,
    tag_attr,
)

_HTTP_URL_ADAPTER = TypeAdapter(HttpUrl)


@dataclass(frozen=True)
class FravegaAdapter(HttpStoreAdapter):
    store_id: str = "fravega"
    store_name: str = "Fravega"
    base_url: str = "https://www.fravega.com"

    async def search(
        self,
        query: str,
        limit: int,
        location: SearchLocation,
        mode: SearchMode = SearchMode.INTERACTIVE,
    ) -> list[Product]:
        url = f"{self.base_url}/l/?keyword={quote_plus(query.strip())}"
        html = await self._get_html(url)
        products = list(self._parse_products(html))[:limit]
        if not products:
            products = await self._fallback_search(query, limit)
        return products

    async def _fallback_search(self, query: str, limit: int) -> list[Product]:
        api_key = os.environ.get("SERPAPI_API_KEY") or os.environ.get("SERP_API_KEY")
        if not api_key:
            return []

        # Usar SerpApi para buscar en Google site:fravega.com
        params = {
            "engine": "google",
            "q": f"site:fravega.com {query}",
            "api_key": api_key,
            "gl": "ar",
            "hl": "es",
            "num": str(limit + 5),  # Pedimos más por si algunos links fallan o no son de productos
        }
        try:
            if self.client is not None:
                response = await self.client.request("GET", "https://serpapi.com/search.json", params=params, timeout=10.0)
            else:
                import httpx
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.request("GET", "https://serpapi.com/search.json", params=params)
            response.raise_for_status()
        except Exception:
            return []

        data = response.json()
        organic = data.get("organic_results", [])
        product_urls = []
        for result in organic:
            link = result.get("link", "")
            if "fravega.com/p/" in link and link not in product_urls:
                product_urls.append(link)

        # Analizar URLs en paralelo para obtener precios frescos
        tasks = [analyze_url(url) for url in product_urls[:limit]]
        analyses = await asyncio.gather(*tasks, return_exceptions=True)

        products = []
        for i, analysis in enumerate(analyses):
            if isinstance(analysis, BaseException) or analysis.error or not analysis.title or not analysis.price:
                continue

            product_url = _http_url(analysis.url)
            if product_url is None:
                continue

            products.append(
                Product(
                    store_id=self.store_id,
                    store_name=self.store_name,
                    position=i + 1,
                    title=analysis.title,
                    price=analysis.price,
                    currency=analysis.currency or "$",
                    original_price=analysis.original_price,
                    discount=analysis.discount,
                    installments=analysis.installments,
                    shipping=None,
                    seller=None,
                    image_url=_http_url(analysis.image_url),
                    product_url=product_url,
                    condition=ProductCondition.NEW if analysis.condition == "new" else ProductCondition.USED if analysis.condition == "used" else ProductCondition.UNKNOWN,
                    availability=ProductAvailability.IN_STOCK if analysis.availability == "in_stock" else ProductAvailability.UNKNOWN,
                    raw_metadata={"fallback_used": True},
                )
            )
        return products[:limit]

    def _parse_products(self, html: str) -> Iterable[Product]:
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select("article")
        if not cards:
            cards = [anchor.parent for anchor in soup.select('a[href*="/p/"]') if anchor.parent]

        seen_urls: set[str] = set()
        position = 1
        for card in cards:
            product = self._parse_card(card, position)
            if product is None or str(product.product_url) in seen_urls:
                continue
            seen_urls.add(str(product.product_url))
            yield product
            position += 1

    def _parse_card(self, card: Tag, position: int) -> Product | None:
        anchor = card.select_one('a[href*="/p/"]')
        if not anchor:
            return None

        raw_text = card.get_text(" ", strip=True)
        product_url = self._absolute_url(tag_attr(anchor, "href"))
        title = self._extract_title(card, anchor)
        if not title or not product_url:
            return None

        prices = self._extract_prices(raw_text)
        current_price = prices[-1] if prices else None
        previous_price = prices[0] if len(prices) > 1 else None
        raw_metadata: dict[str, Any] = {"raw_position": position}
        structured = extract_structured_product_data(card)
        if structured:
            raw_metadata["structured"] = structured

        return Product(
            store_id=self.store_id,
            store_name=self.store_name,
            position=position,
            title=title,
            price=current_price,
            currency="$" if current_price is not None else None,
            original_price=previous_price,
            discount=self._extract_discount(raw_text),
            installments=optional_text(card.select_one('[data-test-id*="installment"], [class*="installment"]')),
            shipping=optional_text(card.select_one('[data-test-id*="shipping"], [class*="shipping"]')),
            seller=self._extract_seller(raw_text),
            image_url=self._extract_image(card),  # type: ignore[arg-type]
            product_url=product_url,  # type: ignore[arg-type]
            condition=detect_condition(title, raw_text),
            availability=detect_availability(raw_text),
            raw_metadata=raw_metadata,
        )

    def _extract_title(self, card: Tag, anchor: Tag) -> str | None:
        for selector in ["h2", "h3", '[data-test-id*="product-title"]']:
            node = card.select_one(selector)
            if node:
                text = clean_text(node.get_text(" ", strip=True))
                if text:
                    return text

        text = clean_text(anchor.get_text(" ", strip=True))
        if "$" in text:
            text = text.split("$", 1)[0]
        if "Vendido por" in text:
            text = text.split("Vendido por", 1)[0]
        return text or None

    def _extract_prices(self, raw_text: str) -> list[float]:
        raw_text = raw_text.split("Precio s/imp", 1)[0]
        prices = []
        for part in raw_text.split("$")[1:]:
            price = number_from_text(part)
            if price is not None:
                prices.append(price)
        return prices

    def _extract_discount(self, raw_text: str) -> str | None:
        import re

        match = re.search(r"\b(\d{1,2})\s*% ?OFF|\b(\d{1,2})\s*$", raw_text)
        if not match:
            return None
        value = match.group(1) or match.group(2)
        return f"{value}% OFF"

    def _extract_seller(self, raw_text: str) -> str | None:
        if "Vendido por" not in raw_text:
            return None
        seller = raw_text.split("Vendido por", 1)[1].split("$", 1)[0]
        return clean_text(seller) or None

    def _extract_image(self, card: Tag) -> str | None:
        image = card.select_one("img")
        if not image:
            return None
        return tag_attr(image, "src") or tag_attr(image, "data-src")


def _http_url(value: str | None) -> HttpUrl | None:
    if value is None:
        return None
    try:
        return _HTTP_URL_ADAPTER.validate_python(value)
    except ValidationError:
        return None
