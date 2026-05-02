# Architecture Flows

## Flujo Legacy `SearchService.search`

Nota: este flujo existe como metodo de servicio, pero no esta expuesto en
`app/main.py` como endpoint `/search` o `/best` actualmente.

1. `SearchService.search` consulta todos los adapters activos.
2. Cada adapter devuelve `Product`.
3. `rank_products` aplica ranking legacy:
   - filtra accesorios obvios;
   - penaliza usados/reacondicionados salvo que la query lo pida;
   - ordena por precio visible, sponsored y posicion.
4. Se guarda snapshot crudo en DB mediante `save_search_snapshot`.
5. Se devuelve `SearchResponse`.

Estos endpoints son utiles para debug, clientes simples y compatibilidad.

## Flujo `/agent/search`

Este es el flujo importante para el futuro agente:

1. `StoreRouter`
   - Interpreta query.
   - Decide tiendas seleccionadas.
   - Explica tiendas excluidas.

2. Extract
   - Consulta adapters seleccionados en paralelo.
   - En streaming procesa tiendas a medida que terminan con `asyncio.as_completed`.
   - Cada adapter devuelve lista de `Product`.
   - Errores por tienda se convierten en `StoreError`.

3. Transform
   - `ProductNormalizer` produce `NormalizedProduct`.
   - Extrae marca, modelo, categoria, atributos, condicion, accesorio y canonical key.
   - Guarda un `raw_compact`, no HTML completo.

4. Score
   - `ProductScorer` genera `ScoredProduct`.
   - Score orientado a confianza de compra, no solamente menor precio.
   - Incluye breakdown explicable.
   - Historico aparece como senal informativa.

5. Enrich
   - `ProductEnricher.enrich_top(top_n=5)`.
   - Hoy no hace requests extra; existe como punto de extension para entrar a paginas de detalle.

6. Load
   - `SearchRepository.save_search_snapshot` guarda:
     - `scrape_runs`
     - `product_observations`
     - `canonical_products`
     - `transformed_product_observations`
   - `PersistenceWorker` guarda candidatos de matching en background.
   - El codigo tiene `MatchingPredictionWorker` para predecir candidatos con el modelo activo sin bloquear el response, pero actualmente no esta instanciado en `app/main.py`.

7. Serve
   - Filtra accesorios de `best_matches`.
   - Agrega warnings globales.
   - Intenta agregar `semantic_match` si existe.
   - Devuelve `AgentSearchResponse`.

## Scraping Modes

Modelo: `SearchMode` en `app/models.py`.

### `interactive`

Usado por default en el endpoint HTTP expuesto:

- `/agent/search`

Tambien lo usa `SearchService.agent_search_events`, que existe como API interna
pero no esta cableada a HTTP.

Objetivo:

- Primer resultado visible rapido.
- Menos resultados por tienda: `min(12, max(4, limit * 3))`.
- Timeout por tienda actual: 8s.
- Evitar trabajo profundo.
- VTEX usa JSON paralelo y no hace fallback HTML salvo que el modo sea `deep`.

### `deep`

Usado por jobs/tracked queries cuando `ScrapeJobRunner` se ejecuta. El modulo
existe, pero el scheduler no esta conectado en `app/main.py`.

Objetivo:

- Mayor completitud.
- Mas tolerancia a latencia.
- VTEX mantiene fallback HTML.
- Pruebas historicas de `POST /jobs/run-once` tardaban mas de 90s con las tracked queries default. Ese endpoint no esta expuesto actualmente.

## Flujo Active Learning — Matching ML

El sistema tiene un pipeline de matching ML asincronico: predice si dos listings son el mismo producto, pero no afecta el ranking todavía.

### Componentes

| Archivo | Rol |
|---|---|
| `app/matching.py` | Computa features tabulares de `ProductPairFeatures` desde dos `ScoredProduct` o desde filas historicas |
| `app/matching_semantic.py` | Calcula features BGE-M3 y BGE reranker offline/runtime background |
| `app/matching_model.py` | Entrena/predice/evalua `LogisticRegression` calibrado |
| `app/matching_runtime.py` | Prewarm + worker background para predicciones V5 |
| `app/matching_labeler.py` | CLI para revisar candidatos y aplicar labels manualmente |
| `app/database.py` | `list_match_candidates`, `label_match_candidate`, `get_match_summary` |

### Features (`ProductPairFeatures`, `pair_features_v5`)

1. `token_overlap` — Jaccard entre sets de tokens de título
2. `rare_token_overlap` — Jaccard excluyendo tokens comunes (apple, tv, gb, nuevo, etc.)
3. `numeric_token_agreement` — coincidencia de tokens numéricos (128, 55, 256, etc.)
4. `title_similarity` — SequenceMatcher ratio
5. `brand_agreement` — brand normalizada igual en ambos
6. `category_agreement` — categoría normalizada igual en ambos
7. `accessory_mismatch` — uno es accesorio y el otro no
8. `model_suffix_conflict` — conflicto de sufijos como Pro/Ultra/Plus/Max/FE
9. `storage_conflict` — ambos especifican almacenamiento y difiere
10. `screen_size_conflict` — ambos especifican pulgadas y difieren
11. `bundle_conflict` — producto solo vs combo/pack/` + ` accesorio
12. `canonical_key_match` — canonical keys normalizadas iguales
13. `price_ratio` — min/max de precios (1.0 = mismo precio, 0.0 = muy distinto)
14. `title_embedding_similarity`
15. `normalized_title_embedding_similarity`
16. `canonical_text_embedding_similarity`
17. `brand_model_text_embedding_similarity`
18. `reranker_score_raw_avg`
19. `reranker_score_same_query_avg`

### Decisión del modelo

- `prob >= 0.60` → `same`
- `prob <= 0.20` → `different`
- `0.20 < prob < 0.60` → `unsure`

Nota: en frozen test `matching-v3`, threshold 0.5 maximiza F1, pero prod conserva 0.60 para evitar falsos positivos de variantes comerciales.

### Candidatos

Se generan durante `save_match_candidates` comparando pares de productos con confidence entre 0.35 y 0.75 (zona de incertidumbre de la heurística). Los candidatos son ordered by `abs(match_confidence - 0.5)` para priorizar los más inciertos.

### Consumo por el agente

1. El agente llama `/agent/search`.
2. La respuesta trae `debug_ref`.
3. Si `MatchingPredictionWorker` esta cableado en runtime, calcula predicciones en background para los candidatos del run.
4. El agente consulta `GET /internal/matching/candidates?run_id={debug_ref}&status=all`.
5. Cada candidato puede traer `model_match_probability`, `model_decision` y `model_version`.

El response inicial de `/agent/search` no espera BGE-M3 ni reranker. Para UI se recomienda streaming/polling posterior con `debug_ref`.

### Ciclo de mejora

```
1. Acumular candidatos vía scraping normal
2. Etiquetar: python -m app.matching_labeler review --limit 50
   (o etiquetar automáticamente si se tiene API key: script externo)
3. Reentrenar: python -m app.matching_model train
4. Evaluar: python -m app.matching_model evaluate
```

### Estado actual (2026-04-28)

- Modelo activo: `match-20260428024114`
- Artifact: `artifacts/matching/model-v5.joblib`
- Features: `pair_features_v5`
- HF: `BAAI/bge-m3` + `BAAI/bge-reranker-v2-m3`
- Dataset global de training: 509 labels binarios.
- Frozen test `matching-v3`, threshold 0.5: accuracy=0.8469 | precision=0.9149 | recall=0.7963 | f1=0.8515 | brier=0.121416.
- Predicciones sobre candidatos reales: 445 guardadas inicialmente; prueba E2E `run_id=82` genero 12 candidatos y 12 predicciones V5.
- Estado: activo para predicciones asincronicas; no afecta ranking ni bloquea search.
