from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote_plus

import httpx

from app.models import Product, ProductAvailability, SearchLocation, SearchMode
from app.scrapers.base import HttpStoreAdapter, ScraperError, clean_text, detect_condition


_DOOFINDER_URL = "https://us1-search.doofinder.com/6/7d78864dfd68192d967ce98f7af00970/_search"
_MEGATONE_ORIGIN = "https://www.megatone.net"


@dataclass(frozen=True)
class MegatoneAdapter(HttpStoreAdapter):
    store_id: str = "megatone_ar"
    store_name: str = "Megatone"
    base_url: str = "https://www.megatone.net"

    async def search(
        self,
        query: str,
        limit: int,
        location: SearchLocation,
        /,
        mode: SearchMode = SearchMode.INTERACTIVE,
    ) -> list[Product]:
        encoded = quote_plus(query.strip())
        url = f"{_DOOFINDER_URL}?page=1&rpp={min(limit, 50)}&query={encoded}"

        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "es-AR,es;q=0.9",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Referer": f"{_MEGATONE_ORIGIN}/",
            "Origin": _MEGATONE_ORIGIN,
        }

        try:
            if self.client is not None:
                response = await self.client.get(url, headers=headers, timeout=self.timeout_seconds)
            else:
                async with httpx.AsyncClient(follow_redirects=True, timeout=self.timeout_seconds) as client:
                    response = await client.get(url, headers=headers)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ScraperError(f"No se pudo consultar {self.store_name}: {exc}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise ScraperError(f"{self.store_name} respondió con JSON inválido.") from exc

        if not isinstance(data, dict):
            raise ScraperError(f"{self.store_name} no devolvió resultados parseables.")

        results = data.get("results", [])
        if not isinstance(results, list) or not results:
            raise ScraperError(f"{self.store_name} no devolvió productos para la búsqueda.")

        products: list[Product] = []
        for position, item in enumerate(results[:limit], start=1):
            parsed = self._parse_item(item, position)
            if parsed is not None:
                products.append(parsed)

        if not products:
            raise ScraperError(f"{self.store_name} no devolvió productos parseables para la búsqueda.")

        return products

    def _parse_item(self, item: dict[str, Any], position: int) -> Product | None:
        title = item.get("title") or item.get("slug")
        link = item.get("link")
        if not title or not link:
            return None

        price = _as_float(item.get("best_price") or item.get("sale_price"))
        original_price = _as_float(item.get("price"))
        if original_price is not None and original_price == price:
            original_price = None

        discount_pct = item.get("calculated_discount")
        discount: str | None = None
        if discount_pct and price and original_price:
            discount = f"{round(float(discount_pct))}% OFF"

        brand = item.get("brand")
        raw_metadata: dict[str, Any] = {
            "product_id": item.get("id") or item.get("gtin"),
            "structured": {k: v for k, v in {
                "brand": brand.strip().lower() if isinstance(brand, str) and brand.strip() else None,
                "sku": item.get("id"),
                "category": item.get("category_name"),
                "gtin": item.get("gtin"),
            }.items() if v},
        }

        availability_str = str(item.get("availability", "")).lower()
        if "in stock" in availability_str:
            availability = ProductAvailability.IN_STOCK
        elif availability_str:
            availability = ProductAvailability.OUT_OF_STOCK
        else:
            availability = ProductAvailability.UNKNOWN

        raw_text = json.dumps(item, ensure_ascii=False)

        return Product(
            store_id=self.store_id,
            store_name=self.store_name,
            position=position,
            title=clean_text(str(title)),
            price=price,
            currency="$" if price is not None else None,
            original_price=original_price,
            discount=discount,
            installments=item.get("highlight_installments"),
            image_url=item.get("image_link"),
            product_url=self._absolute_url(str(link)),  # type: ignore[arg-type]
            condition=detect_condition(str(title), raw_text),
            availability=availability,
            raw_metadata=raw_metadata,
        )


def _as_float(value: Any) -> float | None:
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    if isinstance(value, str):
        try:
            parsed = float(value.replace(",", "."))
            return parsed if parsed > 0 else None
        except ValueError:
            pass
    return None
