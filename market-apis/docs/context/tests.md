# Tests

## Tests

Tests actuales:

- `tests/test_alembic.py`
  - Archivos baseline de Alembic.
  - Migration inicial con `upgrade` y `downgrade`.

- `tests/test_config.py`
  - Musimundo excluido.
  - Embeddings apagados por default.

- `tests/test_database.py`
  - Snapshot/dedupe.
  - Productos transformados.
  - Historical signal.
  - Metricas por adapter.
  - Candidatos y labels de matching.

- `tests/test_agent_search.py`
  - Respuesta `/agent/search`.
  - Routing excluye Samsung y Carrefour para iPhone.
  - Streaming SSE incremental.
  - Timeout por tienda.
  - Persistencia incremental.
  - Matching asincronico no altera `best_matches`.
  - Endpoints internos de matching.
  - `semantic_match` null/warning.
  - Persistencia de transformaciones.

- `tests/test_embeddings.py`
  - Texto de embedding corto/estable.
  - Budget guard.
  - Dry-run sin provider call.
  - Backfill guarda embedding y saltea mismo hash.
  - Provider OpenAI usa modelo/dimensiones configurados.

- `tests/test_semantic_matcher.py`
  - Similaridad coseno fallback JSON.

- `tests/test_matching.py`
  - Features genericas de pares.
  - Conflictos V2 de sufijo/modelo, storage, pulgadas y bundle.
  - Confidence heuristico.
  - Variantes/numeros/accesorios/precios faltantes.

- `tests/test_matching_model.py`
  - Vectorizacion estable de features.
  - `pair_features_v5`.
  - Presets BGE-M3 y reranker.
  - Features semanticas/reranker faltantes vectorizan a `0`.
  - Enriquecimiento/recalculo de features desde filas historicas con JSON V1.
  - Training con labels insuficientes.
  - `unsure` fuera del training set.
  - Persistencia de modelo y predicciones.
  - Evaluacion del modelo activo.

- `tests/test_matching_labeler.py`
  - Comandos cortos `s/d/u/k/o/q`.
  - Readiness para training.
  - Filtros por query/run.
  - Review simulado sin input interactivo real.

- `tests/test_matching_dataset.py`
  - Campañas separadas de dataset V3.
  - Sampling de items hacia train/test.
  - Labels de campaña sin contaminar labels globales.
  - Evaluación de frozen test con threshold report.
  - Evaluación con semantic builder y reranker fake.
  - Artefacto V5 guarda metadata semantica/reranker.
  - Freeze bloquea filas seleccionadas sin label.

- `tests/test_matching_runtime.py`
  - `MatchingPredictionWorker.enqueue` es thread-safe desde callbacks del worker de persistencia.

- `tests/test_normalization.py`
  - Canonical key compatible para iPhone.
  - Accesorios marcados.
  - Structured metadata prioriza brand/model/category.
  - Extraccion de pulgadas, RAM/storage, CPU/GPU y bundle.

- `tests/test_routing.py`
  - `iphone 15` no consulta Samsung ni Carrefour.
  - `galaxy s24` consulta Samsung.
  - `smart tv 55` usa retailers electro.

- `tests/test_scoring.py`
  - Nuevo disponible gana contra reacondicionado salvo query explicita.
  - Historico es informativo.

- `tests/test_store_adapters.py`
  - Fravega fixture.
  - VTEX embedded fixture.
  - VTEX catalog JSON fixture.
  - Extraccion structured product data desde JSON-LD/meta.
  - VTEX interactive evita HTML fallback cuando JSON no da resultado.
  - VTEX deep permite fallback HTML.

- `tests/test_mercado_libre_scraper.py`
  - Fixture Mercado Libre.

Comando:

```bash
.venv/bin/python -m pytest -q
# 79 passed
```
