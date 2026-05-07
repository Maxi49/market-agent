import asyncio
import httpx
import os
from dotenv import load_dotenv

load_dotenv()

async def test_lenovo_shopping():
    api_key = os.getenv("SERPAPI_API_KEY") or os.getenv("SERP_API_KEY")
    print(f"API Key: {'OK' if api_key else 'MISSING'}")
    
    tests = [
        # Sin site filter, con la marca en la query
        ("Solo marca - google_shopping AR", {
            "engine": "google_shopping",
            "q": "notebook lenovo",
            "gl": "ar",
            "hl": "es",
            "google_domain": "google.com.ar",
            "api_key": api_key
        }),
        # Con sitio exacto - google_shopping
        ("Con site:lenovo - google_shopping AR", {
            "engine": "google_shopping",
            "q": "notebook site:lenovo.com/ar",
            "gl": "ar",
            "hl": "es",
            "google_domain": "google.com.ar",
            "api_key": api_key
        }),
        # Busqueda organica con site filter
        ("Con site:lenovo - google organic AR", {
            "engine": "google",
            "q": "notebook site:lenovo.com/ar/es",
            "gl": "ar",
            "hl": "es",
            "google_domain": "google.com.ar",
            "api_key": api_key
        }),
    ]

    async with httpx.AsyncClient(timeout=20) as client:
        for label, params in tests:
            res = await client.get("https://serpapi.com/search", params=params)
            data = res.json()
            engine = params["engine"]
            results = data.get("shopping_results" if engine == "google_shopping" else "organic_results", [])
            print(f"\n=== {label} ===")
            print(f"Count: {len(results)}")
            for item in results[:5]:
                src = item.get("source") or item.get("displayed_link", "")
                title = item.get("title", "")
                price = item.get("price", "")
                link = item.get("product_link") or item.get("link", "")
                print(f"  [{src}] {title} - {price}")
                print(f"  -> {link}")
            if not results and data.get("error"):
                print(f"  Error: {data['error']}")

asyncio.run(test_lenovo_shopping())
