# API Contracts

## API Surface

Endpoints expuestos actualmente por `app/main.py`:

- `GET /health`
  - Devuelve `status`, tiendas activas y errores de startup.
  - Si falla DB, puede quedar en `degraded`.

- `GET /agent/search?query=iphone%2015&limit=3&mode=interactive`
  - Endpoint recomendado para el futuro agente.
  - Ejecuta routing, extract, transform, scoring, enrichment liviano, load y serve.
  - Devuelve `AgentSearchResponse`.

- `GET /agent/search/{run_id}/history`
  - Historico lazy para una corrida persistida.
  - Devuelve `AgentHistoryResponse`.

- `GET /internal/matching/candidates?status=unlabeled&limit=50&query=iphone%2015&run_id=123`
  - Endpoint interno para active learning de matching.
  - Lista pares de productos candidatos, ordenados por incertidumbre.
  - `status`: `unlabeled`, `labeled` o `all`.
  - `query` y `run_id` son filtros opcionales.
  - Es el endpoint que el agente/UI debe usar para consumir predicciones V5 asincronicas despues de recibir `debug_ref`.

- `POST /internal/matching/candidates/{candidate_id}/label`
  - Guarda label humano para un candidato de matching.
  - Labels validos: `same`, `different`, `unsure`.

- `GET /internal/matching/summary`
  - Devuelve conteos de candidatos, labels, buckets de confidence y estado del modelo local activo.

- `GET /agent/analyze-url?url=...`
  - Analiza una URL de producto y extrae datos desde LD+JSON, OpenGraph, meta tags y texto visible.
  - Devuelve `ProductAnalysis`.

Logica implementada pero no expuesta por HTTP actualmente:

- `SearchService.search`: busqueda legacy compatible con `SearchResponse`.
- `SearchService.agent_search_events`: stream interno con eventos `routing`, `store_started`, `store_done`, `match`, `warning`, `error`, `final`.
- `ScrapeJobRunner` / `build_scheduler`: jobs de tracked queries.
- `EmbeddingBackfillService`: backfill de embeddings para `canonical_products`.

CLI internos relevantes:

- `python -m app.matching_dataset build-campaign --name matching-v3`
  - Ejecuta queries variadas con scrapers reales y crea una campaña separada.
- `python -m app.matching_dataset sample-campaign --name matching-v3`
  - Selecciona pool hacia splits `train`/`test`.
- `python -m app.matching_dataset review --name matching-v3 --split train|test`
  - Etiqueta filas de campaña sin tocar labels globales.
- `python -m app.matching_dataset evaluate --name matching-v3`
  - Entrena sobre `train` y evalua sobre frozen `test`.
- `python -m app.matching_dataset evaluate --name matching-v3 --semantic-model bge-m3 --reranker-model bge-reranker-v2-m3`
  - Evalua V5 contra frozen test.
- `python -m app.matching_model train --semantic-model bge-m3 --reranker-model bge-reranker-v2-m3 --artifact-path artifacts/matching/model-v5.joblib`
  - Entrena el artefacto V5 activo.

## Contratos De Datos Principales

### Product

Modelo normalizado minimo que devuelve cualquier adapter:

- `store_id`
- `store_name`
- `position`
- `title`
- `price`
- `currency`
- `original_price`
- `discount`
- `installments`
- `shipping`
- `seller`
- `rating`
- `reviews_count`
- `image_url`
- `product_url`
- `condition`
- `availability`
- `sponsored`
- `scraped_at`
- `raw_metadata`

### SearchResponse

Usado por `/search` y `/best`:

- `query`
- `count`
- `results`
- `errors`

### AgentSearchResponse

Usado por `/agent/search`:

- `query`
- `debug_ref`: id de `scrape_runs`, si pudo persistir.
- `routing`: tiendas incluidas/excluidas y razones.
- `query_understanding`: marca, categoria y atributos detectados.
- `best_matches`: top candidatos filtrados para agente.
- `history_status`: estado del historico (`included`, `available_on_demand` o `unavailable`) y `lookup_url` si aplica.
- `warnings`: riesgos globales.
- `errors`: errores controlados por tienda o persistencia.

Cada `best_match` incluye:

- `normalized_name`
- `store_id`
- `store_name`
- `title`
- `price`
- `currency`
- `product_url`
- `image_url`
- `score`
- `score_breakdown`
- `explanation`
- `risks`
- `trust_signals`
- `historical_signal`
- `semantic_match`

### AgentHistoryResponse

Usado por `GET /agent/search/{run_id}/history`:

- `run_id`
- `count`
- `items`: senales historicas para productos transformados de esa corrida.
- `errors`

Cada item incluye `store_id`, `product_url`, `canonical_key`, `normalized_title`, `price`, `historical_signal`, `average_price` y `price_count`.

### ProductMatchCandidate

Usado por endpoints internos `/internal/matching/*`.

- `id`
- `run_id`
- `query`
- datos left/right: `store_id`, `title`, `product_url`, `canonical_key`, `price`
- `features`: señales genericas de par guardadas al crear el candidato. Las features V5 de BGE/reranker se calculan al predecir y no se persisten en este JSON por ahora.
- `match_confidence`: heuristica inicial usada para seleccionar candidatos.
- `label`: `same`, `different`, `unsure` o `null`.
- `model_match_probability`: probabilidad del modelo local activo, si ya existe prediccion.
- `model_decision`: `same`, `different`, `unsure` o `null`, derivado con umbrales conservadores.
- `model_version`: version del modelo que genero la prediccion.

Importante: en V5 `match_confidence` y `model_match_probability` no modifican `score`, `score_breakdown` ni orden de `best_matches`.

Rol esperado para el agente: `model_match_probability` y `model_decision` son señales auxiliares. No deben tratarse como verdad final en MVP; el LLM puede decidir equivalencia usando el par completo, atributos, condición, precio y contexto de la query. V5 queda preparado para escalar cuando haya más tiendas/volumen.

Consumo recomendado por agente/UI:

1. Llamar `/agent/search`.
2. Tomar `debug_ref`.
3. Hacer polling liviano a `/internal/matching/candidates?run_id={debug_ref}&status=all&limit=100`.
4. Considerar listo cuando los candidatos tengan `model_version` igual al modelo activo o cuando venza timeout UI.

### ProductMatchSummary

Usado por `GET /internal/matching/summary`.

- `total_candidates`
- `unlabeled_candidates`
- `labels_by_value`
- `confidence_buckets`
- `active_model_version`: version activa de modelo local, si existe.
- `model_predictions_count`: cantidad de predicciones persistidas.
- `latest_model_metrics`: metricas guardadas al entrenar el modelo activo.

## Criterios De Aceptacion V1

Para queries como:

- `iphone 15`
- `notebook i5`
- `smart tv 55`

Cada fuente activa deberia:

- devolver al menos titulo, precio, URL y tienda;
- o registrar error controlado sin romper la respuesta global.

Para `/agent/search`:

- debe devolver `best_matches`;
- debe explicar routing;
- debe incluir `query_understanding`;
- debe incluir score breakdown;
- debe incluir warnings cuando aplique;
- no debe exponer ruido innecesario al agente;
- no debe depender de embeddings para funcionar.
