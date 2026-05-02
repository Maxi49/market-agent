# Demo Agent Mastra Para Market API

Demo Mastra independiente para probar un agente comprador que consume `market-apis`.

El agente usa la API como fuente de verdad para precios actuales, consulta matching V5 por `debugRef` como senal auxiliar y puede pedir historico de precios bajo demanda. La decision final de equivalencia queda en el LLM.

Si `market-apis` devuelve resultados de `Amazon US`, el agente los trata como referencia internacional: envio, impuestos/importacion, garantia, stock y precio final pueden diferir para Argentina.

Tambien usa memoria local con LibSQL para recordar el producto actual dentro del thread de Studio. Por ejemplo, si el usuario primero pide "auriculares Huawei" y despues dice "mostrame variedades", el agente debe resolver la referencia contra la busqueda anterior.

## Requisitos

- Node.js `>=22.13.0`
- Backend `market-apis` levantado
- API key de Google para el modelo del agente

## Configuracion

```bash
cp .env.example .env
```

Variables principales:

```bash
GOOGLE_GENERATIVE_AI_API_KEY=...
MASTRA_MODEL_ID=google/gemini-2.5-flash-lite
MARKET_API_BASE_URL=http://127.0.0.1:8000
MARKET_API_TIMEOUT_MS=15000
DEFAULT_SEARCH_MODE=interactive
MATCHING_POLL_ATTEMPTS=5
MATCHING_POLL_INTERVAL_MS=1000
```

El default `google/gemini-2.5-flash-lite` es el modelo recomendado para iterar rapido con tool calling. Para comparar otros proveedores, cambia `MASTRA_MODEL_ID` y define la API key correspondiente, por ejemplo `GROQ_API_KEY` u `OPENROUTER_API_KEY`.

La memoria se guarda en `mastra-memory.db` dentro de esta carpeta. Borra ese archivo si queres resetear conversaciones locales.

Las tools tienen timeout propio contra la Market API y le envian al modelo una salida compacta. Studio sigue pudiendo mostrar el resultado completo, pero el LLM no recibe imagenes, routing completo ni metadata pesada en cada tool call.

## Levantar backend con V5

Desde `market-apis`:

```bash
MATCHING_PREDICTIONS_ENABLED=true \
MATCHING_MODEL_PREWARM_ENABLED=true \
MATCHING_PREDICTION_LIMIT=1000 \
.venv/bin/uvicorn app.main:app --reload
```

Chequeo rapido:

```bash
curl "http://127.0.0.1:8000/agent/search?query=iphone%2015&limit=3&mode=interactive"
```

La respuesta deberia traer `debug_ref`.

## Levantar Mastra Studio

```bash
npm install
npm run dev
```

Abrir `http://localhost:4111`.

## Que probar

Agente visible:

- `marketShoppingAgent`

Tools visibles:

- `search-products`
- `get-matching-candidates`
- `get-price-history`

Workflow visible:

- `compare-products-workflow`

Prompts utiles:

- "Buscame iPhone 15 128GB nuevo y decime cual comprarias."
- "Compara Smart TV 55 pulgadas, evitá accesorios."
- "Estos candidatos parecen el mismo producto o son variantes?"
- "Busca aire acondicionado 3000 frigorias y mira si el precio parece bueno."

Workflow demo:

```json
{
  "query": "iphone 15 128gb",
  "limit": 3,
  "includeHistory": false
}
```

Repetir con `includeHistory=true` para ver el historico.

## Verificacion

```bash
npm test
npm run build
```

El test cubre:

- URL y output compacto de `/agent/search`
- polling corto de matching V5
- consumo de `/agent/search/{runId}/history`
