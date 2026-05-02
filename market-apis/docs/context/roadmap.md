# Roadmap

## Roadmap Recomendado

Prioridad alta:

1. ~~Reducir latencia del final interactivo~~ — RESUELTO. `PersistenceWorker` desacopla persistencia del path SSE. Latencia bajo de ~18s a ~4.7s.
2. ~~Sacar historico del path critico interactive~~ — RESUELTO. `history_status` + `GET /agent/search/{run_id}/history` dejan el historico bajo demanda.
3. ~~Agregar infra active learning para matching~~ — RESUELTO. Candidatos/labels internos, sin OpenAI ni cambios de ranking.
4. ~~Agregar Alembic baseline~~ — RESUELTO. Migration inicial + flujo `upgrade head`/`stamp head`.
5. ~~Entrenar modelo local simple para matching con labels recolectados~~ — RESUELTO.
6. ~~Agregar features de conflicto y reentrenar matching V2~~ — RESUELTO.
7. ~~Matching V5 con BGE-M3 + BGE reranker offline/background~~ — RESUELTO. `match-20260428024114`, `pair_features_v5`, frozen test f1=0.8515/brier=0.121416, worker async con prewarm validado E2E.
8. Streaming/polling UI para consumir predicciones V5 por `debug_ref` sin bloquear `/agent/search`.
9. Priorizar predicciones del `run_id` recien generado en el worker V5.
10. Persistir snapshot de features V5 para auditoria.
11. Reglas de variante/condicion: LED vs OLED, Pro/Plus/Ultra/FE/base, nuevo vs reacondicionado/usado.
12. Completar semantic matcher usando embeddings guardados sin llamar OpenAI en request.
13. Agregar store health diagnostics sobre `scrape_adapter_metrics`.
14. Resolver Mercado Libre como proveedor estable:
   - detectar anti-bot como error;
   - diagnosticos por tienda;
   - elegir proveedor externo/cache/background.
15. Agregar presupuesto/timeout global para `POST /jobs/run-once`.
16. Agregar CRUD interno para tracked queries.

Prioridad media:

17. Enrichment real top 5 para Mercado Libre cuando exista proveedor estable.
18. Enrichment real top 5 para VTEX.
19. Dataset local de atributos/spans para normalizacion.
20. Mejorar relevance/model matching por categoria.
21. Evaluar reemplazo de Musimundo: Megatone, Cetrogar u OnCity.
22. Agregar tests live opcionales marcados, separados de unit tests.

Prioridad futura:

23. Columna `vector(1536)` real + indice pgvector.
24. Dedupe canonico cross-store mas robusto.
25. Vertical supermercado separada por ubicacion/stock.
26. Vertical hogar/muebles.
27. Posible cache por query/store para reducir scraping repetido.

## Proximo Plan Recomendado

### Plan A — UI/Agente con V5 asincronico

1. Exponer `SearchService.agent_search_events` como endpoint HTTP `/agent/search/stream` y emitir el `debug_ref` temprano para habilitar polling de matching.
2. Agregar endpoint liviano de estado por run, por ejemplo `/internal/matching/runs/{run_id}/status`, con:
   - candidatos totales;
   - candidatos con prediccion activa;
   - `active_model_version`;
   - `completed=true|false`.
3. En UI, mostrar resultados inmediatos y luego panel de "matches relacionados" que se actualiza por polling o SSE.
4. Darle al LLM del agente candidatos + `model_match_probability` como contexto, no como veredicto final.
5. Timeout UI recomendado: 5-8s; si V5 no termino, dejar estado "analizando matches".

### Plan A.1 — Politica de decision del agente

1. Prompt/tool contract: el agente debe considerar V5 como señal auxiliar.
2. El agente decide "mismo producto" con evidencia textual: modelo, variante, almacenamiento, condición, bundle, precio y tienda.
3. Si V5 contradice atributos duros (`Pro` vs base, LED vs OLED, distinto storage), pedir cautela o marcar `unsure`.
4. Guardar decisiones del agente como labels/correcciones para mejorar el dataset.

### Plan B — Cablear Worker V5 y prioridad por run

1. Instanciar `MatchingPredictor` y `MatchingPredictionWorker` en `app/main.py`.
2. Conectar el callback de `PersistenceWorker` para encolar predicciones cuando se guardan candidatos.
3. Cambiar `GenerateMatchCandidatesJob` para pasar `run_id` al `MatchingPredictionWorker`.
4. Agregar metodo repository `get_unlabeled_match_candidates_for_prediction(limit, run_id=None)`.
5. Cuando el worker recibe `run_id`, priorizar ese run y despues completar backlog global.
6. Agregar test E2E con app lifespan: search -> candidatos -> predicciones V5 para ese run.

### Plan C — Auditoria y calidad de matching

1. Persistir features V5 calculadas por modelo en una tabla o JSON asociado a prediccion.
2. Crear comando de review para casos `0.50 <= prob < 0.60` y cambios `unsure/different`.
3. Etiquetar especificamente variantes conflictivas:
   - LED vs OLED/QLED;
   - base vs Pro/Plus/Ultra/FE;
   - nuevo vs reacondicionado/usado;
   - notebooks misma GPU/CPU pero distinta linea.
4. Reevaluar `matching-v3` despues de nuevas reglas/labels.

### Plan D — Mercado Libre provider/diagnostics

1. Cambiar `MercadoLibreAdapter` HTML para detectar challenge anti-bot:
   - `x-is-search-bot: true`;
   - HTML chico sin cards;
   - strings `verifyChallenge`, `_bmstate`, account verification.
2. Lanzar `ScraperError` explicito en vez de devolver 0 productos.
3. Extender `AgentSearchResponse` con diagnostico por tienda o reutilizar `errors`/metricas de forma visible para el agente.
4. Mantener `ApifyMercadoLibreAdapter` como provider opcional, pero no usarlo en interactive sin cache por la latencia/calidad observada.
5. Evaluar proveedores pagos alternativos con una matriz:
   - tasa de exito para ML Argentina;
   - latencia p50/p95;
   - precision de query;
   - costo por 1.000 resultados;
   - contrato/ToS/SLA.
