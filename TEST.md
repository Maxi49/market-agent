# Testing

Esta guía documenta cómo escribir tests de calidad en este repositorio. No es genérica — refleja los patrones concretos del codebase y las prácticas recomendadas para cada stack.

---

## Filosofía

**Testea comportamiento, no implementación.** Un test debe verificar qué hace el código, no cómo lo hace internamente. Si un refactor sin cambio de comportamiento rompe un test, el test estaba mal escrito.

**Un test = un invariante.** Cada test documenta una sola regla de negocio o contrato. Si necesitás un nombre largo para describirlo, está bien — el nombre es la documentación.

**AAA: Arrange → Act → Assert.** Primero construís el estado inicial, después ejecutás la acción, después verificás el resultado. Sin mezclar.

**Calidad sobre cobertura.** El objetivo no es 100% de cobertura sino no tener bugs silenciosos en la lógica de dominio. Prioridad: transformación de datos, scoring, parsers de scrapers. No vale la pena: getters triviales, config loading, código de framework.

**Unit tests vs. evals (para el agente LLM).** Los unit tests son deterministas, corren en milisegundos y no hacen API calls reales. Las evals miden calidad del output del LLM — son lentas, costosas y no van en el pipeline de CI estándar. `npm test` y `pytest` son exclusivamente unit tests.

---

## market-apis — Python + pytest

### Cómo correr

```bash
cd market-apis

pytest                              # todos los tests
pytest tests/test_normalization.py  # un archivo específico
pytest -k "iphone"                  # filtrar por nombre de test
pytest --tb=short                   # traceback compacto (recomendado)
pytest -x                           # parar en el primer fallo
```

Configuración en `pyproject.toml`:
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
```

---

### Patrón: lógica pura (normalization, scoring, routing, ranking)

La lógica de dominio es el corazón del proyecto y el lugar más valioso para testear. No necesita red, DB ni mocks.

```python
from app.models import Product
from app.normalization import ProductNormalizer
from tests.typing_helpers import http_url


def product(title: str) -> Product:
    return Product(
        store_id="test",
        store_name="Test",
        position=1,
        title=title,
        price=100_000,
        currency="$",
        product_url=http_url("https://example.com/p"),
        raw_metadata={},
    )


def test_iphone_15_128gb_normaliza_canonical_key_consistentemente():
    normalizer = ProductNormalizer()
    a = normalizer.normalize(product("Apple iPhone 15 128 GB Negro"))
    b = normalizer.normalize(product("Celular iPhone 15 128gb negro"))
    assert a.canonical_key == b.canonical_key == "apple-iphone-15-128gb"


def test_accesorio_es_marcado_como_tal():
    normalized = ProductNormalizer().normalize(
        product("Funda Spigen Ultra Hybrid para iPhone 15 Pro")
    )
    assert normalized.is_accessory is True
```

**Reglas:**
- Función factory `product()` o `make_product()` local en cada archivo de test — no en `conftest.py`
- La función factory solo pone los campos mínimos necesarios; los tests rellenan lo específico
- Cada test verifica una sola aserción lógica (puede haber más líneas de `assert`, pero deben cubrir el mismo invariante)

---

### Patrón: scrapers con fixtures HTML/JSON

Los scrapers dependen del HTML real de cada tienda. Capturás una respuesta real y la guardás en `tests/fixtures/`. Cuando el sitio cambia su estructura, el test falla antes que producción.

```python
from pathlib import Path
from app.scrapers.vtex import CarrefourAdapter
from app.models import ProductAvailability


def test_vtex_parsea_precio_y_disponibilidad():
    html = Path("tests/fixtures/vtex_search.html").read_text()
    product = list(CarrefourAdapter()._parse_products(html))[0]
    assert product.price == 1_599_999
    assert product.availability == ProductAvailability.IN_STOCK
    assert product.discount == "16% OFF"
```

Para agregar fixtures de una tienda nueva:
1. Hacé la request real al endpoint (browser o curl)
2. Guardá el HTML/JSON en `tests/fixtures/nombre_tienda_search.html` (o `.json`)
3. Escribí el test apuntando a ese archivo

---

### Patrón: tests async

El proyecto no usa `pytest-asyncio`. `asyncio.run()` es suficiente y explícito.

```python
import asyncio
from app.scrapers.vtex import CarrefourAdapter
from app.models import SearchLocation


class FakeClient:
    async def request(self, method, url, timeout, **kwargs):
        # retorna un objeto con .text, .json(), .status_code, .headers
        ...


def test_adapter_busqueda_devuelve_productos():
    adapter = CarrefourAdapter(client=FakeClient())
    results = asyncio.run(adapter.search("iphone 15", 5, SearchLocation()))
    assert len(results) > 0
    assert all(p.price is not None for p in results)
```

---

### Patrón: servicios con FakeAdapter

Para testear `SearchService` sin tocar la red, inyectá adapters falsos directamente. El `Protocol` de `StoreAdapter` garantiza que el duck typing funcione.

```python
import asyncio
from dataclasses import dataclass
from app.models import Product, SearchLocation, SearchMode
from app.services import SearchService
from app.database import OptionalRepository


@dataclass
class FakeAdapter:
    store_id: str
    store_name: str
    products: list[Product]

    async def search(
        self, query: str, limit: int, location: SearchLocation, /, mode: SearchMode = SearchMode.INTERACTIVE
    ) -> list[Product]:
        return self.products[:limit]


def test_search_service_rankea_por_score():
    adapters = [FakeAdapter("fravega", "Fravega", [producto_nuevo, producto_reacondicionado])]
    service = SearchService(adapters=adapters, repository=OptionalRepository(None), ...)
    result = asyncio.run(service.search("iphone 15", limit=5, ...))
    assert result.best_matches[0].product.condition == ProductCondition.NEW
```

---

### Patrón: env vars con monkeypatch

```python
def test_config_carga_stores_activos_desde_env(monkeypatch):
    monkeypatch.setenv("ACTIVE_STORES", "fravega,carrefour_ar")
    settings = Settings()
    assert "fravega" in settings.active_stores
    assert "carrefour_ar" in settings.active_stores
```

`monkeypatch` de pytest restaura el env automáticamente al terminar el test.

---

### Patrón: endpoints FastAPI

Para testear los endpoints HTTP usá `AsyncClient` con `ASGITransport`. Inyectá dependencias con `app.dependency_overrides`.

```python
import asyncio
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app, get_search_service


def test_search_endpoint_devuelve_best_matches():
    app.dependency_overrides[get_search_service] = lambda: FakeSearchService(...)
    async def run():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/agent/search", params={"query": "iphone 15"})
        return response
    response = asyncio.run(run())
    assert response.status_code == 200
    assert len(response.json()["best_matches"]) > 0
    app.dependency_overrides.clear()
```

Para mockear HTTP externo (ej: SerpApi, dolarapi) usá `respx` (ya incluido en dev deps):

```python
import respx
import httpx

@respx.mock
def test_mercado_libre_scraper_usa_serpapi():
    respx.get("https://serpapi.com/search").mock(return_value=httpx.Response(200, json={...}))
    # ... test
```

---

### Anti-patrones a evitar

| Anti-patrón | Por qué es un problema |
|-------------|------------------------|
| `mock.patch("app.services.ProductNormalizer")` | Si podés inyectar, no parchees. El patch es frágil al refactoring. |
| Testear `_parse_precio()` en aislamiento | Testea el método público. Los privados son detalles de implementación. |
| Un test que verifica 4 comportamientos distintos | Cuando falla, no sabés cuál de los 4 está roto. |
| `conftest.py` con factories para un solo test file | Ponelas local al archivo. La proximidad evita confusión. |
| Mockear todo hasta el último import | Si mockeas la lógica que querés testear, el test no sirve de nada. |

---

## demo-agent — TypeScript + Node.js native runner

### Cómo correr

```bash
cd demo-agent

npm test                                                        # todos
tsx --test --test-name-pattern="think" "src/**/*.test.ts"      # filtrar por nombre
tsx --test --experimental-test-coverage "src/**/*.test.ts"     # con cobertura
```

Stack: Node.js `node:test` + `tsx` para TypeScript. Sin jest, sin vitest. Cero dependencias extra.

---

### Estructura básica

```typescript
import assert from "node:assert/strict";
import { test } from "node:test";
```

`assert/strict` activa comparaciones estrictas por defecto (`===` en lugar de `==`). Siempre usarlo.

Usá `describe()` solo cuando tenés 5+ tests claramente relacionados. Para 2-3 tests relacionados, `test()` plano con nombres descriptivos es más limpio.

---

### Patrón: tool serialization (toModelOutput)

Cada tool tiene un método `toModelOutput` que serializa el output para el LLM. Testear que el resultado es texto legible y contiene los datos esperados.

```typescript
test("search-products serializa título y precio en texto legible", () => {
  const output = {
    query: "iphone 15",
    debugRef: 127,
    routing: { selected_store_ids: ["mercado_libre"] },
    queryUnderstanding: null,
    bestMatches: [{
      storeId: "mercado_libre",
      storeName: "Mercado Libre",
      title: "Apple iPhone 15 128GB",
      price: 1_200_000,
      currency: "$",
      priceARS: 1_200_000,
      priceUSD: null,
      productUrl: "https://example.com",
      score: 85,
      risks: [],
      trustSignals: null,
      historicalSignal: null,
      semanticMatch: null,
      explanation: "",
      imageUrl: null,
      normalizedName: "",
    }],
    historyStatus: null,
    warnings: [],
    errors: [],
  };

  const result = searchProductsTool.toModelOutput?.(output) as { type: string; value: string };

  assert.equal(result.type, "text");
  assert.match(result.value, /Apple iPhone 15 128GB/);
  assert.notEqual(String(result.value), "[object Object]"); // serialización rota
});
```

---

### Patrón: HTTP client con FetchLike (duck typing)

No mockees `global.fetch`. Usá la interfaz `FetchLike` exportada por `marketApiClient.ts`.

```typescript
import { searchProducts, type FetchLike } from "./marketApiClient";

test("searchProducts construye la URL correctamente", async () => {
  const urls: string[] = [];

  const fetcher: FetchLike = async (url) => {
    urls.push(String(url));
    return new Response(
      JSON.stringify({ query: "iphone 15", debug_ref: 82, best_matches: [], warnings: [], errors: [] }),
      { status: 200, headers: { "content-type": "application/json" } }
    );
  };

  await searchProducts(
    { query: "iphone 15", limit: 3, mode: "interactive" },
    { baseUrl: "http://api.test", fetcher }
  );

  assert.equal(urls[0], "http://api.test/agent/search?query=iphone+15&limit=3&mode=interactive");
});
```

El tipado `FetchLike` en el fake previene drifts silenciosos si la interfaz cambia.

---

### Patrón: agent config y regression tests del prompt (CRÍTICO)

Los tests de instrucciones son la red de seguridad contra ediciones accidentales del prompt. Cada regla crítica debe tener su assertion.

```typescript
test("agent prohíbe inventar precios cuando bestMatches está vacío", async () => {
  const raw = await marketShoppingAgent.getInstructions();
  const instructions = Array.isArray(raw) ? raw.join("\n") : String(raw);
  assert.match(instructions, /no inventes precios/i);
});

test("agent fuerza tool use en step 0 y bloquea tools en step final", async () => {
  const options = await marketShoppingAgent.getDefaultOptions();

  // step 0 — obligatorio usar tool
  assert.deepEqual(
    await options.prepareStep?.({ stepNumber: 0, systemMessages: [] } as never),
    { toolChoice: "required" }
  );

  // step intermedio — libre
  assert.equal(
    await options.prepareStep?.({ stepNumber: 1, systemMessages: [] } as never),
    undefined
  );

  // último step — sin tools, solo respuesta final
  const final = await options.prepareStep?.({
    stepNumber: MARKET_AGENT_MAX_STEPS - 1,
    systemMessages: []
  } as never);
  assert.deepEqual(final?.toolChoice, "none");
  assert.deepEqual(final?.activeTools, []);
});
```

Cuándo agregar un assertion de instrucciones:
- Agregas una regla nueva al prompt que no tiene test
- Un output incorrecto del agente reveló que una regla no estaba siendo respetada

---

### Unit tests vs. evals para el agente

| | Unit test | Eval |
|---|---|---|
| **Qué verifica** | Config del agente, tool routing, serialización, guardrails | Calidad de respuesta del LLM |
| **Llama LLM real** | No | Sí |
| **Velocidad** | < 100ms | Segundos a minutos |
| **Corre en CI** | Siempre | Bajo demanda |
| **Determinista** | Sí | No |

`npm test` no debe hacer ningún API call real (ni al LLM ni a market-apis). Si un test necesita una respuesta real, es un eval, no un unit test.

---

### Anti-patrones a evitar

| Anti-patrón | Por qué es un problema |
|-------------|------------------------|
| `assert.match(result, /iPhone 15 Pro Max 256GB/)` sobre output del LLM | El LLM es no-determinista. El test va a flakear. |
| `const fetcher = async (url) => ...` sin el tipo `FetchLike` | Si la interfaz cambia, el fake no falla y el test da falso positivo. |
| `describe > describe > test` para 3 tests | Innecesario. `test()` plano con nombres descriptivos es más claro. |
| `tool.execute()` contra la API real en unit tests | Lento, costoso, flaky con red. Testea `toModelOutput` en cambio. |

---

## Cuándo agregar un test

| Situación | Qué hacer |
|-----------|-----------|
| Agregás una función de transformación de datos | Test obligatorio antes de mergear |
| Encontrás un bug | Escribís el test que lo reproduce, después lo arreglás |
| Editás una regla crítica del prompt del agente | Agregás assertion de instrucciones |
| Agregás un nuevo scraper | HTML fixture en `tests/fixtures/` + test de parser |
| Refactor sin cambio de comportamiento | No hace falta agregar tests |
| Renombrás una variable | No hace falta |
| Cambiás un valor de config | Solo si la lógica de carga es no-trivial |

---

## Referencias

- [pytest best practices](https://pytest-with-eric.com/introduction/python-unit-testing-best-practices/) — Eric Sales De Andrade
- [FastAPI testing strategies](https://blog.greeden.me/en/2025/11/04/fastapi-testing-strategies-to-raise-quality-pytest-testclient-httpx-dependency-overrides-db-rollbacks-mocks-contract-tests-and-load-testing/) — greeden.me
- [Testing LLM applications](https://langfuse.com/blog/2025-10-21-testing-llm-applications) — Langfuse
- [LLM testing strategies](https://www.confident-ai.com/blog/llm-testing-in-2024-top-methods-and-strategies) — Confident AI
- [Node.js native test runner](https://nodejs.org/learn/test-runner/using-test-runner) — nodejs.org
