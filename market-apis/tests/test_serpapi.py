import asyncio
import os
from dotenv import load_dotenv
from app.services import SearchService
from app.scrapers.registry import ScraperRegistry

load_dotenv()

async def main():
    service = SearchService(ScraperRegistry())
    
    # Test 1: Apple.com.ar
    res = await service.search_everywhere("iPhone 15", "apple.com.ar", limit=10, strict=True)
    print("Apple.com.ar (strict):", len(res.shopping_results) if res.shopping_results else res.error)
    
    res2 = await service.search_everywhere("iPhone 15", "apple.com.ar", limit=10, strict=False)
    print("Apple.com.ar (no strict):", len(res2.shopping_results) if res2.shopping_results else res2.error)

    # Test 2: Apple.com
    res3 = await service.search_everywhere("iPhone 15", "apple.com", limit=10, strict=True)
    print("Apple.com (strict):", len(res3.shopping_results) if res3.shopping_results else res3.error)

    # Test 3: Mercado Libre (to test strict mode on a real store)
    res4 = await service.search_everywhere("iPhone 15", "mercadolibre.com.ar", limit=10, strict=True)
    print("MercadoLibre (strict):", len(res4.shopping_results) if res4.shopping_results else res4.error)

if __name__ == "__main__":
    asyncio.run(main())
