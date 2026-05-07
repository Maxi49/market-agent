import asyncio
import httpx
import os
from dotenv import load_dotenv

load_dotenv()

async def compare():
    api_key = os.getenv("SERPAPI_API_KEY") or os.getenv("SERP_API_KEY")

    base_params = {
        "engine": "google_shopping",
        "gl": "ar",
        "hl": "es",
        "google_domain": "google.com.ar",
        "api_key": api_key
    }

    tests = [
        # Como lo hace search-everywhere actualmente
        "notebook lenovo.com/ar/es",
        # Como lo hice yo directamente (funcionó con 40 resultados)
        "notebook lenovo",
        # Otra variante posible
        "notebook lenovo lenovo.com.ar",
    ]

    async with httpx.AsyncClient(timeout=20) as client:
        for q in tests:
            params = {**base_params, "q": q}
            res = await client.get("https://serpapi.com/search", params=params)
            data = res.json()
            results = data.get("shopping_results", [])
            print(f"\nQuery: '{q}'")
            print(f"  Count: {len(results)}")
            for item in results[:3]:
                print(f"  [{item.get('source')}] {item.get('title')} - {item.get('price')}")

asyncio.run(compare())
