# Setup And Config

## Instalacion Y Ejecucion

Crear entorno:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Para entrenar/predictir con el modelo local tabular de matching:

```bash
pip install -e ".[dev,ml]"
```

Para V5 con embeddings locales Hugging Face y reranker:

```bash
pip install -e ".[dev,ml-text]"
```

Levantar PostgreSQL:

```bash
docker compose up -d postgres
```

Correr API:

```bash
uvicorn app.main:app --reload
```

Para usar SQLite local durante pruebas manuales:

```bash
DATABASE_URL=sqlite:///./local.db uvicorn app.main:app --reload
```

Migraciones Alembic:

```bash
alembic upgrade head
alembic current
```

Para una DB Supabase ya existente creada con `metadata.create_all` y alineada con el schema actual:

```bash
alembic stamp head
```

Modelo local de matching:

```bash
python -m app.matching_labeler stats
python -m app.matching_labeler review --limit 100
python -m app.matching_model train
python -m app.matching_model predict-unlabeled --limit 1000
python -m app.matching_model evaluate
python -m app.matching_dataset build-campaign --name matching-v3
python -m app.matching_dataset sample-campaign --name matching-v3
python -m app.matching_dataset review --name matching-v3 --split train
python -m app.matching_dataset freeze --name matching-v3
python -m app.matching_dataset evaluate --name matching-v3
python -m app.matching_dataset evaluate --name matching-v3 --semantic-model bge-m3 --reranker-model bge-reranker-v2-m3
python -m app.matching_model train --semantic-model bge-m3 --reranker-model bge-reranker-v2-m3 --artifact-path artifacts/matching/model-v5.joblib
```

## Dependencias Importantes

Definidas en `pyproject.toml`:

- Runtime:
  - `fastapi`
  - `uvicorn[standard]`
  - `httpx`
  - `beautifulsoup4`
  - `sqlalchemy`
  - `psycopg[binary]`
  - `apscheduler`
  - `pydantic`
  - `openai`
- Dev/test:
  - `pytest`
  - `respx`
  - `alembic`
- ML opcional:
  - `scikit-learn`
  - `joblib`
- ML texto opcional:
  - `sentence-transformers`
  - `torch`
  - `transformers`

## Configuracion

La configuracion vive en `app/config.py` y se lee desde variables de entorno.

Defaults actuales:

```bash
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/mercado_libre_etl
DEFAULT_POSTAL_CODE=5800
DEFAULT_CITY=Cordoba
ACTIVE_STORES=mercado_libre,fravega,samsung_ar,carrefour_ar,cetrogar_ar,easy_ar,bgh_ar,sony_ar
TRACKED_QUERIES=iphone 15,notebook i5,smart tv 55
JOB_INTERVAL_HOURS=6
SCHEDULER_ENABLED=false
EMBEDDINGS_ENABLED=false
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSIONS=1536
EMBEDDING_MAX_ITEMS_PER_RUN=500
EMBEDDING_MONTHLY_TOKEN_BUDGET=1000000
EMBEDDING_ESTIMATED_COST_PER_1M_TOKENS=0.02
MATCHING_PREDICTIONS_ENABLED=false
MATCHING_MODEL_PREWARM_ENABLED=false
MATCHING_PREDICTION_LIMIT=1000
AMAZON_PROVIDER=disabled
AMAZON_SERPAPI_DOMAIN=amazon.com
AMAZON_SERPAPI_LANGUAGE=en_US
AMAZON_SERPAPI_SHIPPING_LOCATION=ar
```

Embeddings requieren:

```bash
EMBEDDINGS_ENABLED=true
OPENAI_API_KEY=...
```

Importante: embeddings estan apagados por default y no se generan en startup ni dentro de `/agent/search`.

Matching V5 local requiere artefacto activo y dependencias `ml-text`. Para produccion asincronica:

```bash
MATCHING_PREDICTIONS_ENABLED=true
MATCHING_MODEL_PREWARM_ENABLED=true
MATCHING_PREDICTION_LIMIT=1000
```

Comportamiento:

- `MATCHING_MODEL_PREWARM_ENABLED=true` carga el artefacto activo, BGE-M3 y reranker al arrancar.
- `MATCHING_PREDICTIONS_ENABLED=true` activa un worker que predice candidatos nuevos despues de que se guardan, sin bloquear `/agent/search`.
- El agente consume las predicciones por `debug_ref/run_id` via `/internal/matching/candidates`.

Amazon US opcional via SerpApi:

```bash
AMAZON_PROVIDER=serpapi
SERP_API_KEY=...
# o SERPAPI_API_KEY=...
ACTIVE_STORES=mercado_libre,fravega,cetrogar_ar,amazon_us
```

Amazon reutiliza la key global de SerpApi; no existe `AMAZON_SERPAPI_API_KEY`. Los resultados de Amazon deben leerse como referencia internacional, no como precio final local.

Notas de entorno:

- `.env` esta cargado desde `app/config.py` con `python-dotenv`.
- `.env` esta ignorado por git y no debe versionarse.
- Si el password de Postgres/Supabase contiene `#`, debe ir URL-encoded como `%23` dentro de `DATABASE_URL`.
- VS Code/Cursor puede avisar que la inyeccion de `.env` al terminal esta apagada; para la API no es bloqueante si `python-dotenv` esta cargando el archivo.

## Estructura De Carpetas

Archivos principales:

- `app/main.py`: FastAPI app, lifespan, container y endpoints actualmente expuestos.
- `app/config.py`: settings por env vars.
- `app/models.py`: modelos Pydantic y contratos de API.
- `app/services.py`: orquestacion de busqueda, ETL agent-friendly y persistencia.
- `app/database.py`: tablas SQLAlchemy Core, repositorio y fallback opcional.
- `app/jobs.py`: scheduler y corrida manual de tracked queries; modulo disponible, no conectado en `app/main.py`.
- `app/ranking.py`: ranking legacy para `/search` y `/best`.
- `app/routing.py`: seleccion deterministica de tiendas.
- `app/normalization.py`: normalizacion, extraccion de atributos y canonical keys.
- `app/scoring.py`: score explicable orientado a confianza de compra.
- `app/enrichment.py`: seam de enriquecimiento top 5, actualmente liviano.
- `app/embeddings.py`: provider OpenAI, budget guard, hash y texto sintetico.
- `app/semantic.py`: backfill de embeddings y semantic matcher; servicio disponible, no expuesto como endpoint HTTP actualmente.
- `app/matching.py`: features genericas y confidence heuristico para active learning.
- `app/matching_labeler.py`: CLI asistida para etiquetar candidatos de matching.
- `app/matching_model.py`: entrenamiento/prediccion local de product matching.
- `app/matching_dataset.py`: campañas de dataset nuevo, sampling train/test, frozen test y evaluacion.
- `app/matching_semantic.py`: builders locales BGE-M3 y reranker con cache joblib.
- `app/matching_runtime.py`: predictor/worker async para V5 en runtime sin bloquear search; disponible, no conectado en `app/main.py`.
- `app/scrapers/base.py`: protocolo `StoreAdapter`, helpers HTTP/parsing.
- `app/scrapers/registry.py`: registry activo de tiendas.
- `app/scrapers/search_index_mercado_libre.py`: adapter Mercado Libre actual por indice de buscador via SerpApi.
- `app/scrapers/amazon_serpapi.py`: adapter opcional Amazon US via SerpApi.
- `app/scrapers/fravega.py`: adapter Fravega.
- `app/scrapers/vtex.py`: adapter compartido VTEX para Carrefour y Samsung.
- `app/scrapers/vtex.py`: tambien contiene Cetrogar, Easy, BGH, Naldo y Sony Store.
- `app/scrapers/musimundo.py`: adapter viejo/inactivo, solo referencia.
- `app/database.py`: ademas de tablas y repositorio, contiene `PersistenceWorker` y los tipos de job (`AppendResultsJob`, `SaveMetricsJob`, `FinishRunJob`, `SaveSnapshotJob`).
- `tests/`: tests y fixtures HTML/JSON.
- `docker-compose.yml`: PostgreSQL local.
- `README.md`: guia corta de uso. El codigo es la fuente de verdad cuando haya divergencias.
- `docs/context/`: guias largas de continuidad por tema.

## Como Trabajar En Este Proyecto

Antes de tocar codigo:

- Leer `docs/context/README.md` y los docs especificos del area a tocar.
- Leer `README.md`.
- Revisar `app/services.py`, `app/models.py` y el modulo puntual a tocar.
- Correr tests existentes si el cambio afecta comportamiento.

Buenas practicas:

- No agregar scraping agresivo.
- Mantener timeouts.
- Guardar raw compacto, no HTML completo.
- No meter embeddings en request path.
- No hardcodear reglas en el futuro agente si pueden vivir en routing/normalizacion/scoring.
- Si una fuente es fragil, gatearla con tests y no activarla por default.
- Mantener la respuesta global aunque una tienda falle.
