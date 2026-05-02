from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import quote_plus, urlparse

import httpx

from app.models import ScoredProduct


DEAD_LINK_STATUSES = {404, 410}




class AsyncLinkClient(Protocol):
    async def get(
        self,
        url: httpx.URL | str,
        *,
        headers: dict[str, str] | None = None,
        follow_redirects: bool = False,
        timeout: float | None = None,
    ) -> httpx.Response: ...


class LinkGuard(Protocol):
    async def guard(self, scored_products: list[ScoredProduct]) -> list[ScoredProduct]: ...


@dataclass(frozen=True)
class ProductLinkGuard:
    timeout_seconds: float = 6.0
    client: AsyncLinkClient | None = field(default=None, compare=False)

    async def guard(self, scored_products: list[ScoredProduct]) -> list[ScoredProduct]:
        guarded: list[ScoredProduct] = []
        async with _client_context(self.client, self.timeout_seconds) as client:
            for scored in scored_products:
                guarded.append(await self._guard_one(scored, client))
        return guarded

    async def _guard_one(self, scored: ScoredProduct, client: AsyncLinkClient) -> ScoredProduct:
        product = scored.product
        if not _should_validate(product.store_id, str(product.product_url)):
            return scored
        status = await self._status_code(str(product.product_url), client)
        if status not in DEAD_LINK_STATUSES:
            return scored

        fallback_url = fallback_search_url(product.store_id, product.title)
        if fallback_url is None:
            return scored.model_copy(update={
                "warnings": [*scored.warnings, f"link_dead:{product.store_id}"],
            })

        guarded_product = product.model_copy(update={
            "product_url": fallback_url,
            "raw_metadata": {
                **product.raw_metadata,
                "original_product_url": str(product.product_url),
                "link_status": status,
                "link_reliability": "search_fallback_dead_link",
            },
        })
        return scored.model_copy(update={
            "product": guarded_product,
            "warnings": [*scored.warnings, f"link_dead_fallback:{product.store_id}"],
        })

    async def _status_code(self, url: str, client: AsyncLinkClient) -> int | None:
        try:
            response = await client.get(
                url,
                follow_redirects=True,
                timeout=self.timeout_seconds,
                headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                    ),
                },
            )
        except httpx.HTTPError:
            return None
        return response.status_code


def fallback_search_url(store_id: str, title: str) -> str | None:
    encoded = quote_plus(title.strip())
    if not encoded:
        return None
    if store_id == "mercado_libre":
        slug = encoded.replace("+", "-")
        return f"https://listado.mercadolibre.com.ar/{slug}"
    if store_id == "fravega":
        return f"https://www.fravega.com/l/?keyword={encoded}"
    if store_id == "amazon_us":
        return f"https://www.amazon.com/s?k={encoded}"
    if store_id == "samsung_ar":
        return f"https://shop.samsung.com/ar/search?q={encoded}"
    base_url = _vtex_store_base_url(store_id)
    if base_url is not None:
        return f"{base_url}/{encoded}?_q={encoded}&map=ft"
    return None


def _vtex_store_base_url(store_id: str) -> str | None:
    return {
        "carrefour_ar": "https://www.carrefour.com.ar",
        "cetrogar_ar": "https://www.cetrogar.com.ar",
        "easy_ar": "https://www.easy.com.ar",
        "bgh_ar": "https://www.bgh.com.ar",
        "naldo_ar": "https://www.naldo.com.ar",
        "sony_ar": "https://store.sony.com.ar",
    }.get(store_id)


def _should_validate(store_id: str, url: str) -> bool:
    expected_hosts = {
        "mercado_libre": {"mercadolibre.com.ar", "www.mercadolibre.com.ar", "articulo.mercadolibre.com.ar", "listado.mercadolibre.com.ar"},
        "fravega": {"www.fravega.com", "fravega.com"},
        "amazon_us": {"www.amazon.com", "amazon.com"},
        "samsung_ar": {"shop.samsung.com"},
        "carrefour_ar": {"www.carrefour.com.ar", "carrefour.com.ar"},
        "cetrogar_ar": {"www.cetrogar.com.ar", "cetrogar.com.ar"},
        "easy_ar": {"www.easy.com.ar", "easy.com.ar"},
        "bgh_ar": {"www.bgh.com.ar", "bgh.com.ar"},
        "naldo_ar": {"www.naldo.com.ar", "naldo.com.ar"},
        "sony_ar": {"store.sony.com.ar"},
    }.get(store_id)
    if expected_hosts is None:
        return False
    hostname = urlparse(url).hostname or ""
    return hostname in expected_hosts


class _client_context:
    def __init__(self, client: AsyncLinkClient | None, timeout_seconds: float) -> None:
        self._provided_client = client
        self._timeout_seconds = timeout_seconds
        self._owned_client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> AsyncLinkClient:
        if self._provided_client is not None:
            return self._provided_client
        self._owned_client = httpx.AsyncClient(timeout=self._timeout_seconds)
        return self._owned_client

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._owned_client is not None:
            await self._owned_client.aclose()
