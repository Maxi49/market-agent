from __future__ import annotations

import json
import asyncio
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from app.models import Product, ProductAvailability, SearchLocation, SearchMode
from app.scrapers.base import HttpStoreAdapter, ScraperError, clean_text, detect_condition, number_from_text, tag_attr


@dataclass(frozen=True)
class VtexAdapter(HttpStoreAdapter):
    search_path_template: str = "/{query}?_q={query}&map=ft"

    async def search(
        self,
        query: str,
        limit: int,
        location: SearchLocation,
        mode: SearchMode = SearchMode.INTERACTIVE,
    ) -> list[Product]:
        encoded = quote_plus(query.strip())
        if mode == SearchMode.INTERACTIVE:
            products = await self._search_interactive_json(encoded)
        else:
            products = await self._search_json(encoded)
            if not products:
                products = await self._search_intelligent_json(encoded)
        if not products and mode == SearchMode.DEEP:
            path = self.search_path_template.format(query=encoded)
            html = await self._get_html(f"{self.base_url}{path}")
            products = list(self._parse_products(html))
        if not products:
            raise ScraperError(f"{self.store_name} no devolvio productos parseables para la busqueda.")
        return products[:limit]

    async def _search_interactive_json(self, encoded_query: str) -> list[Product]:
        tasks = [
            asyncio.create_task(self._search_json(encoded_query)),
            asyncio.create_task(self._search_intelligent_json(encoded_query)),
        ]
        try:
            for completed in asyncio.as_completed(tasks):
                result = await completed
                if result:
                    return result
            return []
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()

    async def _search_json(self, encoded_query: str) -> list[Product]:
        url = (
            f"{self.base_url}/api/catalog_system/pub/products/search"
            f"?ft={encoded_query}&_from=0&_to=49"
        )
        data = await self._get_json(url)
        if not isinstance(data, list):
            return []
        return self._parse_catalog_products(data)

    async def _search_intelligent_json(self, encoded_query: str) -> list[Product]:
        url = (
            f"{self.base_url}/api/io/_v/api/intelligent-search/product_search/search"
            f"?query={encoded_query}&page=1&count=50"
        )
        data = await self._get_json(url)
        if not isinstance(data, dict):
            return []
        products = data.get("products")
        if not isinstance(products, list):
            return []
        return self._parse_catalog_products(products)

    async def _get_json(self, url: str) -> Any:
        try:
            response = await self._get_response(url, accept="application/json,text/plain,*/*")
        except ScraperError:
            return None

        content_type = response.headers.get("content-type", "")
        if "json" not in content_type:
            return None
        try:
            return response.json()
        except ValueError:
            return None

    def _parse_catalog_products(self, products: list[dict[str, Any]]) -> list[Product]:
        parsed: list[Product] = []
        seen_urls: set[str] = set()
        for position, product in enumerate(products, start=1):
            normalized = self._parse_product(
                product_id=str(product.get("productId") or position),
                product=product,
                position=position,
            )
            if normalized is None or str(normalized.product_url) in seen_urls:
                continue
            seen_urls.add(str(normalized.product_url))
            parsed.append(normalized)
        return parsed

    def _parse_products(self, html: str) -> Iterable[Product]:
        soup = BeautifulSoup(html, "html.parser")
        state = self._extract_state(soup)
        product_entries = self._extract_product_entries(state)
        seen_ids: set[str] = set()
        position = 1

        for product_id, product in product_entries:
            if product_id in seen_ids:
                continue
            parsed = self._parse_product(product_id, product, position)
            if parsed is None:
                continue
            seen_ids.add(product_id)
            yield parsed
            position += 1

    def _extract_state(self, soup: BeautifulSoup) -> dict[str, Any]:
        state: dict[str, Any] = {}
        for script in soup.find_all("script"):
            text = script.get_text("", strip=True)
            if not text.startswith("{") or "Product:" not in text:
                continue
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue
            state.update(data)
        return state

    def _extract_product_entries(self, state: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        entries: list[tuple[str, dict[str, Any]]] = []
        for key, value in state.items():
            if key.startswith("Product:") and isinstance(value, dict):
                entries.append((key.removeprefix("Product:"), self._resolve(value, state)))
        return entries

    def _resolve(self, value: Any, state: dict[str, Any]) -> Any:
        if isinstance(value, dict):
            ref_id = value.get("id")
            if set(value.keys()) <= {"type", "generated", "id", "typename"} and ref_id in state:
                return self._resolve(state[ref_id], state)
            return {key: self._resolve(child, state) for key, child in value.items()}
        if isinstance(value, list):
            return [self._resolve(item, state) for item in value]
        return value

    def _parse_product(
        self,
        product_id: str,
        product: dict[str, Any],
        position: int,
    ) -> Product | None:
        title = product.get("productName") or product.get("productTitle") or product.get("name")
        link = product.get("link") or product.get("linkText")
        if not title or not link:
            return None
        if isinstance(link, str) and not link.startswith(("http://", "https://", "/")):
            link = f"/{link}/p"

        raw_text = json.dumps(product, ensure_ascii=False)
        offer = self._first_offer(product)
        price = self._money_value(
            offer,
            ["Price", "price", "spotPrice", "sellingPrice", "ListPrice"],
        )
        original_price = self._money_value(offer, ["ListPrice", "listPrice"])
        if original_price == price:
            original_price = None
        raw_metadata: dict[str, Any] = {"product_id": product_id, **self._brand_metadata(product)}
        structured = self._structured_metadata(product)
        if structured:
            raw_metadata["structured"] = structured

        return Product(
            store_id=self.store_id,
            store_name=self.store_name,
            position=position,
            title=clean_text(str(title)),
            price=price,
            currency="$" if price is not None else None,
            original_price=original_price,
            discount=self._discount(price, original_price),
            installments=self._installments(offer),
            image_url=self._image(product),  # type: ignore[arg-type]
            product_url=self._absolute_url(str(link)),  # type: ignore[arg-type]
            condition=detect_condition(str(title), raw_text),
            availability=self._availability(offer),
            raw_metadata=raw_metadata,
        )

    def _brand_metadata(self, product: dict[str, Any]) -> dict[str, str]:
        brand = product.get("brand") or product.get("Brand")
        if brand and isinstance(brand, str) and brand.strip():
            return {"brand": brand.strip().lower()}
        return {}

    def _structured_metadata(self, product: dict[str, Any]) -> dict[str, str]:
        item = self._first_item(product)
        fields = {
            "brand": _clean_metadata_value(product.get("brand") or product.get("Brand") or product.get("brandName")),
            "model": _clean_metadata_value(product.get("model") or product.get("Model")),
            "mpn": _clean_metadata_value(product.get("mpn") or product.get("MPN")),
            "sku": _clean_metadata_value(
                product.get("productReference")
                or product.get("productId")
                or (item.get("itemId") if isinstance(item, dict) else None)
            ),
            "category": _clean_metadata_value(_last_category(product)),
            "gtin": _clean_metadata_value(item.get("ean") if isinstance(item, dict) else None),
        }
        return {key: value for key, value in fields.items() if value}

    def _first_item(self, product: dict[str, Any]) -> dict[str, Any] | None:
        items = self._first_value_for_prefix(product, "items")
        if isinstance(items, list) and items and isinstance(items[0], dict):
            return items[0]
        return None

    def _first_offer(self, product: dict[str, Any]) -> dict[str, Any]:
        items = self._first_value_for_prefix(product, "items")
        if isinstance(items, list) and items:
            sellers = items[0].get("sellers") if isinstance(items[0], dict) else None
            if isinstance(sellers, list) and sellers:
                for seller in sellers:
                    commercial_offer = seller.get("commertialOffer") or seller.get("commercialOffer")
                    if isinstance(commercial_offer, dict):
                        price = self._money_value(
                            commercial_offer,
                            ["Price", "price", "spotPrice", "sellingPrice", "ListPrice"],
                        )
                        if price:
                            return commercial_offer
        return product

    def _money_value(self, data: dict[str, Any], keys: list[str]) -> float | None:
        for key in keys:
            value = data.get(key)
            if isinstance(value, (int, float)) and value > 0:
                return float(value)
            if isinstance(value, str):
                parsed = number_from_text(value)
                if parsed is not None:
                    return parsed
        return None

    def _installments(self, offer: dict[str, Any]) -> str | None:
        installments = offer.get("Installments") or offer.get("installments")
        if not installments:
            installments = self._first_value_for_prefix(offer, "Installments")
        if not isinstance(installments, list) or not installments:
            return None
        best = max(installments, key=lambda item: item.get("NumberOfInstallments", 0))
        count = best.get("NumberOfInstallments")
        value = best.get("Value")
        if not count or not value:
            return None
        return f"{count} cuotas de $ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    def _image(self, product: dict[str, Any]) -> str | None:
        items = self._first_value_for_prefix(product, "items")
        if not isinstance(items, list) or not items:
            return None
        images = items[0].get("images") if isinstance(items[0], dict) else None
        if not isinstance(images, list) or not images:
            return None
        return images[0].get("imageUrl") or images[0].get("imageTag")

    def _availability(self, offer: dict[str, Any]) -> ProductAvailability:
        available_quantity = offer.get("AvailableQuantity") or offer.get("availableQuantity")
        if isinstance(available_quantity, (int, float)):
            return ProductAvailability.IN_STOCK if available_quantity > 0 else ProductAvailability.OUT_OF_STOCK
        return ProductAvailability.UNKNOWN

    def _discount(self, price: float | None, original_price: float | None) -> str | None:
        if not price or not original_price or original_price <= price:
            return None
        percent = round((1 - price / original_price) * 100)
        return f"{percent}% OFF"

    def _first_value_for_prefix(self, data: dict[str, Any], prefix: str) -> Any:
        if prefix in data:
            return data[prefix]
        for key, value in data.items():
            if key.startswith(prefix):
                return value
        return None


def _clean_metadata_value(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return clean_text(value)
    if isinstance(value, (int, float)):
        return str(value)
    return None


def _last_category(product: dict[str, Any]) -> Any:
    categories = product.get("categories") or product.get("Categories")
    if isinstance(categories, list) and categories:
        category = categories[-1]
        if isinstance(category, str):
            return category.strip("/").split("/")[-1]
    return product.get("category") or product.get("categoryName")


@dataclass(frozen=True)
class CarrefourAdapter(VtexAdapter):
    store_id: str = "carrefour_ar"
    store_name: str = "Carrefour Argentina"
    base_url: str = "https://www.carrefour.com.ar"


@dataclass(frozen=True)
class SamsungAdapter(VtexAdapter):
    store_id: str = "samsung_ar"
    store_name: str = "Samsung Argentina"
    base_url: str = "https://shop.samsung.com/ar"
    search_path_template: str = "/search?q={query}"


@dataclass(frozen=True)
class CetrogarAdapter(VtexAdapter):
    store_id: str = "cetrogar_ar"
    store_name: str = "Cetrogar"
    base_url: str = "https://www.cetrogar.com.ar"


@dataclass(frozen=True)
class EasyAdapter(VtexAdapter):
    store_id: str = "easy_ar"
    store_name: str = "Easy Argentina"
    base_url: str = "https://www.easy.com.ar"


@dataclass(frozen=True)
class BGHAdapter(VtexAdapter):
    store_id: str = "bgh_ar"
    store_name: str = "BGH"
    base_url: str = "https://www.bgh.com.ar"


@dataclass(frozen=True)
class SonyAdapter(VtexAdapter):
    store_id: str = "sony_ar"
    store_name: str = "Sony Store Argentina"
    base_url: str = "https://store.sony.com.ar"

