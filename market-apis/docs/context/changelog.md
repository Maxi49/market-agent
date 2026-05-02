# Changelog

Nota: este archivo conserva snapshots historicos de sesiones. Puede mencionar
endpoints/adapters que existieron o fueron probados, pero el estado operativo
actual esta en `README.md`, `CONTEXT.md` y los docs tematicos; el codigo es la
fuente de verdad.

## Snapshot De Sesion 2026-04-29 — Amazon US via SerpApi

Se agrego Amazon como tienda opcional para comparacion internacional:

- Nuevo adapter `app/scrapers/amazon_serpapi.py`.
- Usa SerpApi `engine=amazon`.
- Reutiliza la key global `SERP_API_KEY` o `SERPAPI_API_KEY`; no hay key separada para Amazon.
- `amazon_us` no entra en `ACTIVE_STORES` default.
- Activacion manual:

```bash
AMAZON_PROVIDER=serpapi
ACTIVE_STORES="mercado_libre,fravega,cetrogar_ar,amazon_us"
```

- Metadata de productos:
  - `raw_metadata["provider"]="serpapi"`;
  - `raw_metadata["engine"]="amazon"`;
  - `raw_metadata["asin"]`;
  - `raw_metadata["reliability"]="medium"`;
  - `raw_metadata["price_reliability"]="medium"` si viene `extracted_price`, `"low"` si el precio sale de texto.
- Router:
  - fuerte para smartphones, notebooks, audio, gaming, accesorios y libros/Kindle;
  - bloqueado para supermercado;
  - weak para electrodomesticos.

Decision:

- Amazon US se trata como referencia internacional, no como compra local directa: el agente debe advertir sobre envio, impuestos, garantia, disponibilidad y precio final.

Tests:

```text
.venv/bin/python -m pytest tests/test_store_adapters.py tests/test_config.py tests/test_routing.py -q
19 passed
```

## Snapshot De Sesion 2026-04-29 — Mercado Libre bloqueado y prueba Apify

Se investigo por que Mercado Libre empezo a devolver 0 resultados "de la nada".

Hallazgos:

- El router si seleccionaba `mercado_libre` para queries Apple/iPhone.
- El adapter HTML devolvia 0 porque Mercado Libre respondia una micro-landing anti-bot, no un listado:
  - `HTTP 200`;
  - `x-is-search-bot: true`;
  - body ~5.5 KB;
  - JS challenge con `verifyChallenge`, `_bmstate`, `_bmc`;
  - sin cards `ui-search-layout__item` ni `poly-component__title`.
- La API oficial OAuth fue probada:
  - callback OAuth funciona;
  - se obtiene `access_token` `APP_USR-...`;
  - `/sites/MLA/search?q=...` devuelve `403 forbidden` incluso con bearer token.
- Se concluyo que la API oficial disponible para la app actual no sirve para busqueda general marketplace; esta orientada a integraciones de negocio/seller.

Cambios aplicados:

- Se agrego callback OAuth dev:
  - `GET /auth/mercadolibre/login`
  - `GET /auth/mercadolibre/callback`
- Se agrego `ApifyMercadoLibreAdapter`:
  - archivo `app/scrapers/apify_mercado_libre.py`;
  - se activa con `APIFY_TOKEN`;
  - usa `Authorization: Bearer`, no token en query string;
  - actor default configurable con `APIFY_MERCADO_LIBRE_ACTOR_ID`.
- `app/scrapers/registry.py` elige Apify para Mercado Libre si existe `APIFY_TOKEN`.
- Tests actualizados:

```text
.venv/bin/pytest tests/test_mercado_libre_scraper.py tests/test_store_adapters.py tests/test_agent_search.py -q
18 passed
```

Pruebas reales con Apify:

- `crawlerbros/mercadolibre-scraper`: fallo por bloqueo en todos los intentos; status message reporto residential proxy pool/bloqueo target country.
- `sourabhbgp/mercadolibre-scraper`: `SUCCEEDED` pero 0 items; logs `ANTIBOT_JS_SHIM` y `ANTIBOT_CAPTCHA_PAGE`.
- `easyapi/mercadolibre-search-results-scraper`: 0 items.
- `saswave/mercadolibre-product-scraper`: fallo parseando HTML bloqueado.
- `ecomscrape/mercadolibre-product-search-scraper`: requiere rentar actor pago.
- `duvan517x/mercadolibre-scraper-product-scraper`: devolvio datos pero tardo ~2m24s y la relevancia fue pobre para `iphone 15 pro 128gb`.

Decision:

- No usar Apify/Mercado Libre como dependencia sincrona de `/agent/search` interactive por ahora.
- Mantener el adapter Apify como experimento/background/deep.
- Proximo trabajo recomendado: store diagnostics + detectar anti-bot como error explicito + evaluar proveedor externo con SLA/costo/latencia.

Nota de seguridad:

- Rotar secretos expuestos durante debugging:
  - Mercado Libre client secret/tokens;
  - `APIFY_TOKEN`.

### Addendum — fallback por indice de buscador

Se implemento una alternativa de baja confiabilidad para Mercado Libre:

- Nuevo adapter `app/scrapers/search_index_mercado_libre.py`.
- No consulta Mercado Libre directo.
- Consulta un provider de search con query `site:mercadolibre.com.ar <query>`.
- Providers:
  - Serper (`SERPER_API_KEY`).
  - SerpApi (`SERPAPI_API_KEY`).
  - Google Programmable Search (`GOOGLE_CSE_API_KEY` + `GOOGLE_CSE_CX`).
- `registry.py` en `MERCADO_LIBRE_PROVIDER=auto` prioriza:
  1. search index si hay credenciales;
  2. Apify si hay `APIFY_TOKEN`;
  3. scraper HTML.
- Los productos devueltos quedan marcados:
  - `raw_metadata["provider_family"]="search_index"`;
  - `raw_metadata["reliability"]="low"`;
  - `raw_metadata["price_reliability"]="low"`.
- Precio:
  - SerpApi puede traer `rich_snippet.detected_extensions.price`.
  - Google CSE puede traer `pagemap`/offer/meta.
  - Serper suele depender de snippet.
  - En todos los casos se considera aproximado/no verificado en vivo.

Tests:

```text
.venv/bin/pytest tests/test_mercado_libre_scraper.py tests/test_store_adapters.py tests/test_agent_search.py -q
20 passed
```

## Snapshot De Sesion 2026-04-28 — Matching V5 en prod-mode async

Se implemento y valido el rollout operativo de Matching V5 con embeddings locales Hugging Face y reranker:

- `pair_features_v5` agrega:
  - `title_embedding_similarity`
  - `normalized_title_embedding_similarity`
  - `canonical_text_embedding_similarity`
  - `brand_model_text_embedding_similarity`
  - `reranker_score_raw_avg`
  - `reranker_score_same_query_avg`
- Modelos:
  - `BAAI/bge-m3`
  - `BAAI/bge-reranker-v2-m3`
- Artefacto activo:
  - version `match-20260428024114`
  - path `artifacts/matching/model-v5.joblib`
  - `features_version=pair_features_v5`
- CLI extendidas:
  - `python -m app.matching_dataset evaluate --name matching-v3 --semantic-model bge-m3 --reranker-model bge-reranker-v2-m3`
  - `python -m app.matching_model train --semantic-model bge-m3 --reranker-model bge-reranker-v2-m3 --artifact-path artifacts/matching/model-v5.joblib`
- Runtime:
  - `MatchingPredictor`
  - `MatchingPredictionWorker`
  - prewarm opcional con `MATCHING_MODEL_PREWARM_ENABLED=true`
  - prediccion async con `MATCHING_PREDICTIONS_ENABLED=true`
  - batch configurable con `MATCHING_PREDICTION_LIMIT`

Resultados `matching-v3`:

```text
threshold 0.5:
accuracy=0.8469
precision=0.9149
recall=0.7963
f1=0.8515
brier=0.121416

threshold 0.8:
precision=0.9333
recall=0.5185
f1=0.6667
```

Latencia medida:

```text
cache memoria, 20 pares: 0.008s total
modelos calientes + textos nuevos: ~0.133s/par
carga fria + textos nuevos, 10 pares: 16.487s total
```

Validacion E2E real:

```text
GET /agent/search?query=iphone%2015&limit=3&mode=interactive
status=200
search_seconds=3.114
debug_ref=82
candidatos=12
predicciones V5=12
modelo=match-20260428024114
```

Decision:

- V5 queda listo para prod como background async.
- No se llama BGE/reranker sincronicamente dentro de `/agent/search`.
- El agente/UI debe consumir por `debug_ref` consultando `/internal/matching/candidates?run_id=...`.
- Threshold prod queda en `same >= 0.60`, aunque frozen test maximiza F1 en 0.50.
- Politica MVP: V5 es señal auxiliar. El LLM del agente decide equivalencia final hasta que el volumen de tiendas/pares justifique automatizar dedupe con el modelo.

Normalizacion estructurada:

- Helper JSON-LD/meta para `Product`.
- Adapters guardan `raw_metadata["structured"]` cuando existe.
- VTEX extrae structured desde JSON de catalogo/product state.
- `ProductNormalizer` prioriza structured `brand/model/category`.
- Reglas nuevas: `screen_size`, RAM/storage, CPU/GPU y bundle.

Tests:

```text
.venv/bin/python -m pytest -q
67 passed
```

## Snapshot De Sesion 2026-04-27 — Dataset V3 para frozen test de matching

Se agregó infraestructura para crear un dataset nuevo de matching separado del dataset activo:

- Nuevo CLI `python -m app.matching_dataset`.
- Nuevas tablas:
  - `matching_dataset_campaigns`
  - `matching_dataset_items`
- Nueva migración Alembic: `0003_matching_dataset_campaigns`.
- `build-campaign` ejecuta queries variadas con scrapers reales via `SearchService.agent_search` y guarda items de campaña.
- `sample-campaign` selecciona pares hacia `train`/`test` con buckets `uncertainty`, `high_risk`, `random` y `deliberate`.
- `review` etiqueta items de campaña con razón breve, sin escribir en `product_match_candidates.label`.
- `freeze` bloquea congelar si hay filas train/test sin label, salvo `--allow-incomplete`.
- `evaluate` entrena sobre split `train` y mide sobre split `test`, reportando thresholds 0.5/0.8/0.9/0.95 y calibration buckets.

Decisión importante:

- Los labels de campaña viven en `matching_dataset_items.label`.
- Esto evita que el frozen test contamine el training activo por accidente.
- El modelo sigue en shadow mode.

Ejecución real inicial de `matching-v3`:

```text
build-campaign:
- Primera pasada interactive interrumpida luego de superar el objetivo por query lenta.
- Segunda pasada complementaria con queries Samsung/TV/notebooks/electro tambien interrumpida luego de mejorar balance.
- Pool final: 659 pares unicos.

sample-campaign --target-train 200 --target-test 100:
- train=200
- test=100
- buckets: uncertainty=130, random=98, high_risk=40, deliberate=32
- categorias seleccionadas: smartphones_apple=89, tv=60, smartphones_samsung=41, home_appliances=33, notebooks=28, accessories_bundles=27, smartphones_other=22

labels:
- Se aplico etiquetado asistido por Codex con politica conservadora.
- Labels finales seleccionados: same=143, different=151, unsure=6.
- Los labels viven solo en matching_dataset_items.label.

evaluate:
- train binario: 196 (same=89 / different=107)
- frozen test binario: 98 (same=54 / different=44)
- threshold 0.5: accuracy=0.7041, precision=0.8378, recall=0.5741, f1=0.6813, brier=0.184612
- threshold 0.8: same precision=1.0, same recall=0.1852
- threshold 0.9/0.95: no predice same; recall=0
```

Lectura:

- El F1 alto del split interno anterior estaba inflado.
- En frozen test nuevo, el modelo sigue siendo util como detector conservador de `same` a threshold 0.8, pero pierde muchos `same`.
- No esta listo para afectar ranking/dedupe publico.
- Proximo trabajo recomendado: mejorar normalizacion/canonical keys para accesorios Samsung Buds, TVs Samsung por modelo y categorias de electro/notebooks; luego reentrenar y reevaluar contra este frozen test.

## Snapshot De Sesion 2026-04-27 — Matching V2 con features de conflicto

Se implementó `pair_features_v2` para corregir la debilidad estructural del modelo de matching: las features V1 solo medían similitud y no expresaban conflictos explícitos.

Cambios principales:

- Se agregaron `model_suffix_conflict`, `storage_conflict`, `screen_size_conflict` y `bundle_conflict` a `ProductPairFeatures`.
- `build_pair_features` ahora penaliza conflictos claros en la heurística de `match_confidence`.
- El training/predicción recalcula o enriquece features V2 desde `left_title`, `right_title`, `left_canonical_key`, `right_canonical_key` y precios para reutilizar candidatos guardados con JSON V1.
- `FEATURES_VERSION` pasó a `pair_features_v2`.
- `matching_labeler` muestra las features nuevas al revisar candidatos.
- No cambió el contrato público de `/agent/search`; el modelo sigue en shadow mode y no altera `best_matches`, score ni ranking.

Reentreno real:

```text
python -m app.matching_model train
version=match-20260427185814
algorithm=logistic_regression_calibrated_sigmoid
labels=200 (71 same / 129 different)
metrics held-out 20%: accuracy=0.975, precision=0.9333, recall=1.0, f1=0.9655, brier=0.034338

python -m app.matching_model predict-unlabeled --limit 1000
predictions_saved=95

python -m app.matching_model evaluate
metrics all labels: accuracy=0.98, precision=0.9718, recall=0.9718, f1=0.9718, brier=0.031272
```

Sanity checks manuales post-entrenamiento:

- `iPhone 15 128GB` vs `iPhone 15 Pro 128GB` → `different` (probabilidad same ~0.0054).
- `Samsung Galaxy S24 FE 256GB` vs `S24 FE 256GB + Galaxy Buds4` → `different` (probabilidad same ~0.0123).
- `Smart TV Samsung 50"` vs `Smart TV Samsung 55"` → `different` (probabilidad same ~0.0001).

Pendiente recomendado:

- Revisar las 95 predicciones unlabeled persistidas y etiquetar errores/casos `unsure`.
- Si aparecen nuevos falsos positivos, priorizar labels de variantes difíciles antes de probar modelos más expresivos.

## Snapshot De Sesion 2026-04-27 — Primer entrenamiento del modelo de matching

### Etiquetado automatico y entrenamiento

Se realizó el primer ciclo completo de etiquetado + entrenamiento del modelo de matching ML:

- Se leyeron 200 candidatos sin label de la DB (`list_match_candidates(status="unlabeled", limit=200)`).
- Claude analizó cada par manualmente (títulos, features de `ProductPairFeatures`, precios) y asignó labels directamente via `label_match_candidate()`.
- Labels aplicados: **71 same** / **129 different** / 0 unsure.
- Criterios usados:
  - Mismo modelo + mismo almacenamiento + marca = `same` (sin importar condición nuevo/reacondicionado o diferencia de color cuando uno de los lados no la especifica).
  - Diferente modelo dentro de la misma línea (iPhone 15 vs Pro, S24 vs Ultra, S24 vs FE) = `different`.
  - Diferente almacenamiento (128GB vs 256GB) = `different`.
  - Diferente tamaño de pantalla (50" vs 55" vs 65") = `different`.
  - Diferente modelo de TV (UN55DU7000 vs UN55U8000F vs Q7F QLED) = `different`.
  - Bundle (TV + barra de sonido, S24 FE + Galaxy Buds) = `different` respecto al producto solo.
  - Marcas distintas = `different`.

- Modelo entrenado con `python -m app.matching_model train`:
  - Versión: `match-20260427162133`
  - Algoritmo: `logistic_regression_calibrated_sigmoid`
  - Artifact: `artifacts/matching/model.joblib`
  - Dataset: 200 labels (71 same, 129 different)
  - Métricas (training set): accuracy=0.78, precision=0.73, recall=0.61, f1=0.66, brier=0.14

### Resultados de pruebas reales (17 pares de test)

16/17 correctos (94%). Fallos conocidos:

1. `iPhone 15 128GB` vs `iPhone 15 Pro 128GB` → predice `same` (0.85). Las features heurísticas no distinguen "Pro" como token diferenciador porque brand, category, token_overlap son similares. Necesita más ejemplos Pro/no-Pro etiquetados.
2. `Samsung Galaxy S24 FE 256GB` vs `S24 FE 256GB + Galaxy Buds4` → predice `unsure` (0.60). Bundle difícil de separar solo por tokens.

### Observaciones sobre el modelo

- El umbral de decisión es: prob ≥ 0.80 → `same`, prob ≤ 0.20 → `different`, else → `unsure`.
- El modelo supera la heurística en la mayoría de casos claros (prob >0.85 para pares same obvios, <0.15 para different obvios).
- La heurística (`estimate_match_confidence`) sigue siendo complementaria para los casos `unsure`.
- El desbalance de clases (2x más `different` que `same`) es esperado dado el tipo de datos; el modelo lo maneja con calibración sigmoid.

---

## Snapshot De Sesion 2026-04-26 (segunda parte)

Cambios importantes realizados en la sesion:

- Se integro `.env` con `python-dotenv`.
- Se agrego `.env` a `.gitignore`.
- Se valido Supabase Postgres con `DATABASE_URL` local.
- Se corrigio `OptionalRepository.get_history_signal`, que no delegaba al repository real.
- Se elimino codigo muerto/unreachable relacionado con `find_semantic_match`.
- Se agrego batch de historico por `canonical_key` para evitar N+1 en scoring.
- Se movieron llamadas sync de DB fuera del event loop usando `asyncio.to_thread` en los caminos async principales.
- Se agrego `GET /agent/search/stream` con Server-Sent Events.
- Se agregaron eventos SSE: `routing`, `store_started`, `store_done`, `match`, `warning`, `error`, `final`.
- Se agregaron runs incrementales para streaming: crear run, persistir resultados por tienda y cerrar run.
- Se agrego `SearchMode`: `interactive` y `deep`.
- `/search`, `/best`, `/agent/search` y `/agent/search/stream` aceptan `mode=interactive|deep`, default `interactive`.
- Jobs/tracked queries usan `deep`.
- Se agregaron perfiles por tienda/categoria/marca para routing.
- Se agrego tabla `scrape_adapter_metrics`.
- Se persisten metricas por adapter: `store_id`, `query`, `mode`, `strategy`, `elapsed_ms`, `status`, `products_count`, `error_type`.
- Se emiten metricas en SSE dentro de `store_done` y `error`.
- Se centralizo un `httpx.AsyncClient` compartido por app con connection pooling.
- Se configuro timeout granular de HTTPX: connect/read/write/pool.
- VTEX en `interactive` corre endpoints JSON en paralelo y toma el primer resultado util.
- VTEX en `deep` mantiene fallback HTML.
- Embeddings apagados no bloquean `/agent/search` ni agregan latencia de OpenAI.
- README y tests fueron actualizados.

Pruebas reales de endpoints hechas al final de la sesion:

```text
GET /health -> 200, ~0.001s
GET /stores -> 200, ~0.001s
GET /tracked-queries -> 200, ~1.0s
GET /search?query=iphone%2015&limit=2 -> 200, ~11.4s, 2 results
GET /best?query=smart%20tv%2055&limit=2 -> 200, ~10.1s, 2 results
GET /agent/search?query=iphone%2015&limit=2 -> 200, ~13.7s, 2 best_matches
GET /agent/search?query=galaxy%20s24&limit=2 -> 200, ~15.3s, 2 best_matches, 1 store error
GET /agent/search/stream?query=iphone%2015&limit=2 -> 200, SSE correcto
POST /jobs/backfill-embeddings?dry_run=true&limit=2 -> 200, embeddings_disabled
POST /jobs/run-once -> timeout cliente a 90s
```

Hallazgo importante de latencia (resuelto en sesion posterior):

- En SSE para `iphone 15`, adapters reportaron metricas bajas pero el `final` llegaba a ~18.7s.
- Causa: la persistencia a Supabase bloqueaba la emision de eventos SSE (2 round-trips por tienda antes de emitir `store_done`), mas N+1 queries en `_save_transformed_products`, mas `create_scrape_run` bloqueando el arranque de los adapters.
- Resuelto con `PersistenceWorker` — ver seccion correspondiente.

## Snapshot De Sesion 2026-04-26 (tercera parte — optimizacion de latencia)

Cambios realizados para desacoplar persistencia del path de respuesta SSE:

- Se agrego `PersistenceWorker` en `app/database.py`: un `asyncio.Queue` con un worker background que drena jobs de persistencia sin bloquear el event loop principal.
- Se agregaron 4 tipos de job: `AppendResultsJob`, `SaveMetricsJob`, `FinishRunJob`, `SaveSnapshotJob`.
- El worker se arranca y detiene en el lifespan de FastAPI (`app/main.py`), junto con el scheduler existente.
- En `_produce_agent_search_events` (`app/services.py`): los eventos `store_done` y `match` ahora se emiten **antes** que cualquier write a DB. La persistencia se encola en background via `worker.enqueue(...)`.
- El evento `final` tambien se emite antes de `finish_scrape_run`.
- `create_scrape_run` se lanza como `asyncio.Task` en paralelo con los scrapers (ya no bloquea el arranque). Se awaita recien cuando el primer adapter termina, momento en que ya tiene su respuesta (~500ms vs ~2s de scraping).
- Fix N+1 en `_save_transformed_products`: reemplazado por 1 SELECT batch + 2 INSERTs batch (antes: N SELECTs + N INSERTs individuales).
- Fix row-by-row en `_save_product_observations`: reemplazado por 1 INSERT batch.
- `SearchService` recibe `worker: PersistenceWorker | None` como parametro opcional. Si es `None` (tests), usa el path sincrono anterior — todos los tests siguen pasando sin cambios.
- `get_history_baselines` permanece en el path critico (necesario para scoring). Su latencia (~1s) es pura latencia de red a Supabase y no tiene solucion simple sin cambiar la arquitectura del scoring.

Resultado medido post-optimizacion (query `iphone 15`, 2 tiendas):

```text
[   30ms] routing    → selected=['mercado_libre', 'fravega']
[ 1814ms] store_done → fravega        | scrape=775ms  | 9 productos
[ 1814ms] match      → [fravega]       $1,679,000 | score=82.00
[ 4688ms] store_done → mercado_libre  | scrape=2181ms | 9 productos
[ 4688ms] match      → [mercado_libre] $1,171,000 | score=71.24
[ 4688ms] FINAL      → debug_ref=26   | errors=0
```

Latencia del `final`: de ~18s a ~4.7s. Los ~1s extra sobre el scrape puro son `get_history_baselines`.

Tests: 37 passed (sin cambios en tests existentes).

## 2026-05-01 — MegatoneAdapter

- Nuevo adapter `app/scrapers/megatone.py` (`megatone_ar`).
- Plataforma: Doofinder (no VTEX). Endpoint JSON directo sin auth.
- Agregado a `registry.py` y al default de `ACTIVE_STORES` en `config.py` y `.env`.
- Perfil de routing en `routing.py`: STRONG en tv/home_appliances, OK en smartphones/notebooks, BLOCKED en supermarket/books.
- Instrucciones del agente actualizadas: `megatone` → `megatone_ar`.
- Fix `.env`: `ACTIVE_STORES` no incluía `megatone_ar`, lo que dejaba el adapter registrado pero inactivo.
