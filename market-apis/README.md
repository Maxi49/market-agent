# Multi Store ETL API

API en FastAPI para buscar productos en tiendas online de Argentina, normalizar resultados y guardar historico de precios.

## Tiendas activas

- Mercado Libre
- Fravega
- Carrefour Argentina
- Samsung Argentina
- Cetrogar
- Easy Argentina
- BGH
- Sony Store Argentina
- Amazon US (opcional, via SerpApi)

Nota Mercado Libre: el codigo actual usa `MercadoLibreSearchIndexAdapter`
via SerpApi (`SERPAPI_API_KEY` o `SERP_API_KEY`). Es una fuente de baja
confiabilidad porque usa resultados indexados, no stock/precio live de ML.
El HTML directo y Apify quedaron como investigacion historica, no como adapters
cableados. Ver `docs/context/scraping-adapters.md`.

Amazon US es opcional y usa SerpApi `engine=amazon` con la misma key global (`SERP_API_KEY` o `SERPAPI_API_KEY`). Debe tratarse como referencia internacional: envio, impuestos, garantia y precio final pueden diferir del listado.

## Instalacion

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Para correr Matching V5 local con BGE-M3 + reranker:

```bash
pip install -e ".[dev,ml-text]"
```

## Base de datos

La app usa PostgreSQL por defecto:

```bash
docker compose up -d postgres
```

La URL default es:

```bash
postgresql+psycopg://postgres:postgres@localhost:5432/mercado_libre_etl
```

Se puede cambiar con `DATABASE_URL`.

### Supabase Postgres

No hace falta instalar el SDK de Supabase si solo se usa la base PostgreSQL.
Copiá el connection string de Supabase y setealo como `DATABASE_URL`; la app
acepta URLs `postgresql://` / `postgres://` y las normaliza al driver
`postgresql+psycopg://` usado por SQLAlchemy.

Ejemplo:

```bash
DATABASE_URL="postgresql://postgres:PASSWORD@HOST:5432/postgres?sslmode=require"
```

Si la password tiene caracteres especiales como `#`, dejá el valor entre
comillas en `.env` o escapá el caracter en URL encoding. Ejemplo:

```bash
DATABASE_URL="postgresql://postgres:PASS%23WORD@HOST:5432/postgres?sslmode=require"
```

## Correr la API

```bash
uvicorn app.main:app --reload
```

Endpoints expuestos actualmente en `app/main.py`:

- `GET /health`
- `GET /agent/search?query=iphone%2015&limit=3`
- `GET /agent/search/{run_id}/history`
- `GET /internal/matching/candidates`
- `POST /internal/matching/candidates/{candidate_id}/label`
- `GET /internal/matching/summary`
- `GET /agent/analyze-url?url=...`

Hay logica interna para `/search`, `/best`, streaming SSE, jobs y backfill de
embeddings, pero esos endpoints no estan cableados en FastAPI actualmente.

## Busqueda para agentes

`/agent/search` devuelve una respuesta opinada para tool-calling:

- `best_matches`: mejores candidatos ya filtrados y scoreados.
- `routing`: tiendas elegidas/excluidas y razones.
- `query_understanding`: marca, categoria y atributos detectados.
- `warnings`: riesgos globales.
- `debug_ref`: id de corrida guardada.

Los endpoints de busqueda aceptan `mode=interactive|deep`.

- `interactive`: default para API y stream, prioriza latencia y evita fallbacks lentos.
- `deep`: usado por jobs, prioriza completitud y permite fallbacks mas profundos.

`SearchService.agent_search_events` implementa un stream interno por eventos
`routing`, `store_started`, `store_done`, `match`, `error`, `warning` y `final`,
pero no hay endpoint HTTP `/agent/search/stream` expuesto actualmente.

## Matching V5 async

El modelo local de matching esta implementado para correr fuera del path
sincrono de search, pero el worker V5 no esta conectado en `app/main.py`
actualmente:

- `/agent/search` devuelve `debug_ref`.
- Se guardan candidatos de matching en background.
- `MatchingPredictionWorker` puede predecir con `artifacts/matching/model-v5.joblib`
  si se instancia y se conecta al worker de persistencia.
- El agente/UI consulta `/internal/matching/candidates?run_id=<debug_ref>&status=all`.

Activacion recomendada:

```bash
MATCHING_PREDICTIONS_ENABLED=true
MATCHING_MODEL_PREWARM_ENABLED=true
MATCHING_PREDICTION_LIMIT=1000
```

El prewarm carga BGE-M3 y el reranker al arrancar para evitar latencia fria,
pero hoy requiere cablear `MatchingPredictionWorker` en el lifespan de la app.

El flujo interno es ETL hibrido:

1. Extract: adapters por tienda.
2. Transform: routing, normalizacion, canonical key, deteccion de accesorios/condicion.
3. Load: observaciones crudas-normalizadas, canonical products, transformaciones y raw compacto.
4. Serve: resumen para agente con score, riesgos y senales de confianza.

## Jobs programados

El modulo de jobs existe en `app/jobs.py`, pero el scheduler no esta conectado
en `app/main.py` actualmente. `SCHEDULER_ENABLED` esta definido en config, pero
hoy no activa nada por si solo; para usar jobs hay que cablear `ScrapeJobRunner`
y `build_scheduler` en el lifespan de FastAPI.

Queries monitoreadas por defecto:

- `iphone 15`
- `notebook i5`
- `smart tv 55`

Podés reemplazarlas con:

```bash
TRACKED_QUERIES="iphone 15,notebook i5,smart tv 55"
```

## Vector search y costos

La capa semantica esta apagada por defecto:

```bash
EMBEDDINGS_ENABLED=false
```

Para activarla:

```bash
EMBEDDINGS_ENABLED=true
OPENAI_API_KEY=...
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSIONS=1536
EMBEDDING_MAX_ITEMS_PER_RUN=500
EMBEDDING_MONTHLY_TOKEN_BUDGET=1000000
EMBEDDING_ESTIMATED_COST_PER_1M_TOKENS=0.02
```

El servicio de backfill existe en `app/semantic.py`, pero no hay endpoint HTTP
expuesto actualmente en `app/main.py`. Se puede cablear un endpoint interno
cuando haga falta.

Reglas de costo:

- No se generan embeddings en startup.
- No se generan embeddings dentro de `/agent/search`.
- Solo se vectorizan `canonical_products`.
- Se saltea un producto si el `embedding_text_hash` no cambio.
- `dry_run=true` estima tokens/costo sin llamar OpenAI.
- Si falta `OPENAI_API_KEY`, la API sigue funcionando y `semantic_match` queda `null`.

## Configuracion util

```bash
ACTIVE_STORES="mercado_libre,fravega,samsung_ar,carrefour_ar,cetrogar_ar,easy_ar,bgh_ar,sony_ar"
DEFAULT_POSTAL_CODE=5800
DEFAULT_CITY=Cordoba
SERPAPI_API_KEY=...
# o:
SERP_API_KEY=...

# Amazon US opcional via SerpApi
AMAZON_PROVIDER=serpapi
AMAZON_SERPAPI_DOMAIN=amazon.com
AMAZON_SERPAPI_LANGUAGE=en_US
AMAZON_SERPAPI_SHIPPING_LOCATION=ar
ACTIVE_STORES="mercado_libre,fravega,cetrogar_ar,amazon_us"
```

## Tests

```bash
.venv/bin/python -m pytest -q
```
