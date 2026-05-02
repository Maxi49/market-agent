# Operations Latency

## Scheduler Y Jobs

Archivo: `app/jobs.py`.

Por default:

```bash
SCHEDULER_ENABLED=false
```

Configuracion disponible, pero actualmente no cableada en `app/main.py`:

```bash
SCHEDULER_ENABLED=true JOB_INTERVAL_HOURS=6 uvicorn app.main:app --reload
```

Queries default:

- `iphone 15`
- `notebook i5`
- `smart tv 55`

Se pueden reemplazar con:

```bash
TRACKED_QUERIES="iphone 15,notebook i5,smart tv 55"
```

El job usa `ScrapeJobRunner` y `SearchService.run_tracked_query`.
Actualmente `app/main.py` no instancia el scheduler ni expone `POST /jobs/run-once`;
para usarlo hay que cablearlo en el lifespan o ejecutarlo desde codigo/CLI.

Nota actual:

- `run_tracked_query` usa modo `deep`.
- En prueba manual historica, `POST /jobs/run-once` no termino antes de un timeout cliente de 90s cuando ese endpoint estaba expuesto.
- No rompe la API general, pero necesita presupuesto global, limites menores o procesamiento en background si se expone para uso frecuente.

## Bugs / Gaps Conocidos

### Historico lazy en modo interactive

`get_history_baselines` ya no corre en el path critico de `/agent/search` cuando `mode=interactive`.
La variante streaming existe como metodo interno (`SearchService.agent_search_events`), pero no esta expuesta por HTTP actualmente.

Comportamiento actual:

- `interactive`: el ranking sale sin historico; `history_status.status=available_on_demand` si hay `debug_ref`.
- `deep`: el ranking conserva historico incluido; `history_status.status=included`.
- El agente puede pedir historico despues con `GET /agent/search/{run_id}/history`.
- El endpoint calcula baselines en batch por `canonical_key` excluyendo la corrida actual.

Pendiente opcional:

- Pre-cachear baselines en memoria con TTL corto para queries repetidas.

### Semantic matcher incompleto

- Hay backfill de embeddings.
- Hay guardado de embeddings.
- Hay fallback cosine search.
- Pero `/agent/search` no adjunta embedding del canonical product actual, entonces normalmente no puede encontrar semantic matches.

Fix sugerido:

- Agregar metodo repository para obtener embedding por `canonical_key`.
- Hacer que `SemanticMatcher.match` use esa embedding guardada.
- No llamar OpenAI dentro de `/agent/search`.

### Alembic baseline configurado

- Existe `alembic/versions/0001_initial_schema.py`.
- Existe `alembic/versions/0002_matching_model_predictions.py`.
- Para DB nueva usar `alembic upgrade head`.
- Para Supabase existente alineado usar `alembic stamp head`.
- Todo cambio futuro de schema debe agregarse como migration incremental.

### No hay CRUD de tracked queries

- Solo seed desde env/config.
- Falta endpoint interno para crear/activar/desactivar queries monitoreadas.

### Enrichment real pendiente

- El seam existe, pero todavia no entra a paginas de detalle.
- Hay que hacerlo por tienda y con limites de latencia.

### Store health pendiente

- Falta diagnostico por tienda:
  - ultimo exito;
  - ultimo error;
  - result count;
  - estrategia usada;
  - tiempo de respuesta.

Ya existe la tabla `scrape_adapter_metrics`, asi que el siguiente paso natural es exponer un endpoint de health/metrics por tienda.

### Mercado Libre bloqueado / proveedor pendiente

Mercado Libre dejo de ser confiable via HTML directo:

- HTML search responde `HTTP 200`, pero con challenge anti-bot y header `x-is-search-bot: true`.
- El parser queda sin cards y retorna 0 productos.
- API oficial con OAuth fue probada y `/sites/MLA/search?q=...` devuelve `403 forbidden` incluso con `APP_USR-...`.
- Apify fue probado con varios actors; ninguno quedo apto para interactive:
  - algunos fallan por antibot;
  - otros devuelven 0;
  - uno devuelve resultados con latencia ~2m24s y mala relevancia.

Impacto:

- `/agent/search` puede responder sin Mercado Libre aunque el router lo haya seleccionado.
- Si no se expone error por tienda, el agente puede concluir falsamente que "no hay resultados" en ML.

Acciones recomendadas:

1. Exponer diagnosticos por tienda en `AgentSearchResponse` o en endpoint interno:
   - tienda consultada;
   - cantidad de productos;
   - error;
   - estrategia/proveedor;
   - latencia.
2. Tratar 0 resultados de ML con challenge detectado como `StoreError`, no como lista vacia.
3. Evaluar proveedor externo serio para ML:
   - Apify solo si se encuentra actor estable;
   - Oxylabs/ZenRows/DataForSEO/Bright Data u otro proveedor con SLA;
   - fuente partner/oficial si existe.
4. Si se mantiene Apify, moverlo a background/cache:
   - no bloquear `mode=interactive`;
   - usar `mode=deep` o jobs;
   - cachear por query normalizada.

Fallback implementado actualmente:

- `MercadoLibreSearchIndexAdapter` permite consultar indice de buscador en vez de ML directo.
- Latencia esperada: similar a una llamada API externa normal, apta para interactive si el proveedor responde rapido.
- Confiabilidad:
  - `reliability=low`;
  - `price_reliability=low`;
  - stock no verificado;
  - precio/snippet potencialmente cacheado.
- Provider soportado en codigo: SerpApi (`SERPAPI_API_KEY` o `SERP_API_KEY`).
- No hay selector `auto`, Apify ni HTML directo cableados en el registry actual.

### Relevancia todavia mejorable

- Accesorios pueden pasar si el vocabulario no los contempla.
- Falta matching mas fuerte de atributos:
  - `iphone 15 128gb`;
  - `smart tv 55`;
  - `notebook i5 16gb`;
  - generacion/capacidad/pantalla/RAM.

## Matching V5 Runtime

Flags:

```bash
MATCHING_PREDICTIONS_ENABLED=true
MATCHING_MODEL_PREWARM_ENABLED=true
MATCHING_PREDICTION_LIMIT=1000
```

Comportamiento:

- `MATCHING_MODEL_PREWARM_ENABLED=true` carga el artefacto activo, BGE-M3 y reranker al arrancar.
- `MATCHING_PREDICTIONS_ENABLED=true` crea un worker separado que predice candidatos nuevos despues de `GenerateMatchCandidatesJob`.
- `/agent/search` no espera BGE-M3 ni reranker; el usuario recibe la respuesta normal y las predicciones llegan por `debug_ref`.

Mediciones locales:

- Cache existente desde disco, 20 pares: `0.405s` total.
- Cache en memoria, 20 pares: `0.008s` total.
- Textos nuevos + carga de modelos, 10 pares: `16.487s` total.
- Modelos calientes + textos nuevos:
  - BGE-M3 solo: `0.076s/par`.
  - reranker solo: `0.071s/par`.
  - BGE-M3 + reranker: `0.133s/par`.

Prueba E2E real:

```text
GET /agent/search?query=iphone%2015&limit=3&mode=interactive
status=200
latencia search=3.114s
debug_ref=82
candidatos generados=12
predicciones V5 guardadas=12
modelo=match-20260428024114
```

Interpretacion:

- La latencia de search fue scraping real; V5 corrio despues.
- En ese run, las predicciones V5 estuvieron listas unos segundos despues del response.
- El costo frio existe y por eso prewarm es obligatorio para prod.

Gaps operativos pendientes:

- Priorizar en el worker los candidatos del `run_id` recien creado en vez de batch global de unlabeled.
- Exponer estado de prediccion por run para UI (`pending/running/completed`, conteos, modelo).
- Persistir snapshot de features V5 para auditoria y debugging de falsos positivos.
