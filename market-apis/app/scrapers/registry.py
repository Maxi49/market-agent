from os import getenv

import httpx
from dotenv import load_dotenv

from app.scrapers.base import StoreAdapter
from app.scrapers.amazon_serpapi import AmazonSerpApiAdapter, has_amazon_serpapi_credentials
from app.scrapers.fravega import FravegaAdapter
from app.scrapers.megatone import MegatoneAdapter
from app.scrapers.search_index_mercado_libre import MercadoLibreSearchIndexAdapter
from app.scrapers.vtex import (
    BGHAdapter,
    CarrefourAdapter,
    CetrogarAdapter,
    EasyAdapter,
    SamsungAdapter,
    SonyAdapter,
    FarmacityAdapter,
)

load_dotenv()


def build_store_registry(client: httpx.AsyncClient | None = None) -> dict[str, StoreAdapter]:
    amazon_adapter = _build_amazon_adapter(client)

    adapters: list[StoreAdapter] = [
        MercadoLibreSearchIndexAdapter(client=client),
        FravegaAdapter(client=client, timeout_seconds=20.0),
        MegatoneAdapter(client=client),
        SamsungAdapter(client=client),
        CarrefourAdapter(client=client),
        CetrogarAdapter(client=client),
        EasyAdapter(client=client),
        BGHAdapter(client=client),
        SonyAdapter(client=client),
        FarmacityAdapter(client=client),
    ]
    if amazon_adapter is not None:
        adapters.append(amazon_adapter)
    return {adapter.store_id: adapter for adapter in adapters}


def _build_amazon_adapter(client: httpx.AsyncClient | None = None) -> StoreAdapter | None:
    provider = getenv("AMAZON_PROVIDER", "disabled").lower()
    if provider == "serpapi" and has_amazon_serpapi_credentials():
        return AmazonSerpApiAdapter(client=client)
    return None
