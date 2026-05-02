# Project Context

Este archivo es el indice corto de contexto del proyecto. Para evitar cargar un documento gigante en cada sesion, el contexto detallado vive en `docs/context/`.

## Lectura recomendada

- Panorama general: `docs/context/project-overview.md`
- Setup/configuracion/estructura: `docs/context/setup-config.md`
- Endpoints y contratos: `docs/context/api-contracts.md`
- Flujos `/search`, `/agent/search` y streaming: `docs/context/architecture-flows.md`
- Scrapers/adapters y notas por tienda: `docs/context/scraping-adapters.md`
- Routing, normalizacion, ranking y scoring: `docs/context/routing-normalization-scoring.md`
- Persistencia, tablas, worker e historico lazy: `docs/context/persistence.md`
- Embeddings y semantic search: `docs/context/embeddings-semantic.md`
- Operacion, latencia, jobs y gaps conocidos: `docs/context/operations-latency.md`
- Tests: `docs/context/tests.md`
- Roadmap: `docs/context/roadmap.md`
- Changelog/snapshots de sesiones: `docs/context/changelog.md`

## Estado rapido

- Proyecto: API FastAPI ETL multi-tienda para busqueda de productos argentinos agent-friendly.
- Carpeta: `/Users/maxigimenez/Desktop/dev/market-agent/market-apis`.
- Stack: Python, FastAPI, SQLAlchemy Core, httpx, BeautifulSoup, APScheduler, PostgreSQL/Supabase.
- Tiendas activas: Mercado Libre, Fravega, Carrefour Argentina y Samsung Argentina.
- Endpoint principal para agente: `GET /agent/search`.
- Streaming SSE: implementado como `SearchService.agent_search_events`, pero no expuesto en `app/main.py` actualmente.
- Historico en `interactive`: lazy via `GET /agent/search/{run_id}/history`.
- Matching active learning: candidatos/labels internos en shadow mode via `/internal/matching/*`.
- CLI de labeling: `python -m app.matching_labeler stats` y `python -m app.matching_labeler review --limit 100`.
- Modelo local de matching V5: `app.matching_model` con scikit-learn/joblib opcional, features de conflicto, features semánticas HF/reranker opcionales y predicciones en shadow mode si se cablea `MatchingPredictionWorker`.
- Dataset V3 de matching: `app.matching_dataset` crea campañas separadas, splits train/test congelados y evaluación sobre frozen test.
- Migraciones: Alembic baseline + `0002_matching_model_predictions`; DB nueva usa `alembic upgrade head`, Supabase existente alineado usa `alembic stamp head`.
- Embeddings: apagados por default; backfill manual para canonical products y experimento offline local HF para matching.
- Ultimo test conocido: `.venv/bin/python -m pytest -q` -> `79 passed`.

## Como pedir contexto a futuros agentes

Ejemplos:

- "Leete `CONTEXT.md` y `docs/context/persistence.md` para trabajar en DB/latencia."
- "Leete `docs/context/scraping-adapters.md` para agregar una tienda."
- "Leete `docs/context/api-contracts.md` y `docs/context/architecture-flows.md` para cambiar `/agent/search`."
