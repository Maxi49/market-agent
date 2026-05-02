# Persistence

## Persistencia

Archivo: `app/database.py`.

Motor:

- SQLAlchemy Core.
- PostgreSQL por default.
- SQLite usado para tests/dev si se overridea `DATABASE_URL`.
- Alembic configurado para migraciones versionadas.
- `metadata.create_all` sigue activo en `SearchRepository.init_schema()` por compatibilidad temporal.

Tablas:

### `stores`

- Tiendas conocidas.
- Se seedearon desde adapters activos al startup.

### `tracked_queries`

- Queries monitoreadas manualmente.
- Se seedearon desde `TRACKED_QUERIES`.
- Hoy no hay CRUD HTTP.

### `scrape_runs`

- Una corrida de scraping/busqueda.
- Guarda query, ubicacion, timestamps, status y errores.

### `product_observations`

- Observaciones crudas normalizadas minimamente.
- Una fila por producto/tienda/url/run.
- Unique constraint: `(scrape_run_id, store_id, product_url)`.

### `canonical_products`

- Producto canonico deducido por normalizacion.
- PK: `canonical_key`.
- Guarda `normalized_title`, `brand`, `model`, `category`, `attributes`.
- Tambien guarda metadata de embedding:
  - `embedding_text`
  - `embedding_text_hash`
  - `embedding_model`
  - `embedding_dimensions`
  - `embedding`
  - `token_count`
  - `estimated_cost_usd`
  - `embedded_at`

### `transformed_product_observations`

- Resultado transformado y scoreado por corrida.
- Guarda canonical key, score, warnings, trust signals y raw compact.
- Unique constraint: `(scrape_run_id, store_id, product_url)`.

### `embedding_usage_log`

- Auditoria de uso de embeddings.
- Guarda modelo, items, tokens, costo estimado, dry-run, errores y timestamp.

### `scrape_adapter_metrics`

- Metricas por adapter y estrategia.
- Campos principales:
  - `scrape_run_id`
  - `store_id`
  - `store_name`
  - `query`
  - `mode`
  - `strategy`
  - `elapsed_ms`
  - `status`
  - `products_count`
  - `error_type`
- Por ahora se usan para observabilidad, no para routing automatico.

### `product_match_candidates`

- Pares de productos generados para active learning de matching.
- Se generan en background despues de persistir `/agent/search`.
- Guardan:
  - `scrape_run_id`
  - `query`
  - producto left/right: tienda, titulo, URL, canonical key y precio
  - `features` JSON
  - `match_confidence`
  - `label` actual, si ya fue etiquetado
- Unique constraint por corrida y par de URLs/tiendas.
- Matching funciona como prediccion asincronica: no altera ranking ni scoring publico.

### `product_match_labels`

- Historial de labels humanos para candidatos de matching.
- Guarda `candidate_id`, `label`, comentario opcional y timestamp.
- Labels validos: `same`, `different`, `unsure`.

### `product_match_models`

- Metadata de modelos locales entrenados para product matching.
- Guarda:
  - `version`
  - `algorithm`
  - `features_version`
  - `artifact_path`
  - `trained_at`
  - conteos de labels positivos/negativos
  - `metrics` JSON
  - `active`
- Solo un modelo deberia quedar activo para prediccion por vez.

### `product_match_predictions`

- Predicciones del modelo local sobre candidatos de matching.
- Guarda `candidate_id`, `model_version`, `match_probability`, `decision` y `predicted_at`.
- No reemplaza labels humanos.
- V5 se guarda aca cuando `MATCHING_PREDICTIONS_ENABLED=true`.
- Sigue sin alterar ranking ni scoring publico.

### `matching_dataset_campaigns`

- Campañas separadas para construir datasets nuevos de matching.
- Guarda nombre, status, queries, categorias, targets train/test y timestamps.
- Permite auditar una corrida de scraping/seleccion sin mezclarla automaticamente con el dataset activo.

### `matching_dataset_items`

- Pares seleccionados para una campaña.
- Referencia `product_match_candidates.id`, pero guarda split propio (`pool`, `train`, `test`) y label propio.
- Los labels de campaña no escriben en `product_match_candidates.label`, para evitar contaminar training activo o frozen test.
- Guarda bucket de seleccion (`uncertainty`, `high_risk`, `random`, `deliberate`) y probabilidad/decision del modelo activo al momento de incorporar el candidato.

## Alembic

Archivos:

- `alembic.ini`
- `alembic/env.py`
- `alembic/versions/0001_initial_schema.py`
- `alembic/versions/0002_matching_model_predictions.py`
- `alembic/versions/0003_matching_dataset_campaigns.py`

Comandos:

```bash
alembic upgrade head
alembic current
alembic revision -m "descripcion_del_cambio"
```

Politica:

- Para una DB nueva, correr `alembic upgrade head`.
- Para Supabase ya existente y alineada con el schema actual, correr `alembic stamp head` para baselinar sin recrear tablas.
- No se ejecutan migraciones automaticamente en startup.
- Todo cambio futuro de schema debe tener una migration incremental.

## PostgreSQL / pgvector

Estado actual:

- `SearchRepository.init_schema()` intenta `CREATE EXTENSION IF NOT EXISTS vector` si el dialecto es PostgreSQL.
- Si falla pgvector, la app sigue funcionando.
- El embedding se guarda actualmente en columna JSON para compatibilidad con SQLite/tests.
- `find_semantic_match` calcula similitud coseno en Python sobre embeddings JSON.

Pendiente importante:

- Migrar a columna `vector(1536)` real en PostgreSQL.
- Agregar indice vectorial apropiado.
- Mantener fallback SQLite para tests.
