# Embeddings Semantic

## Vector Search / Embeddings

Objetivo MVP:

- Ayudar a canonical matching/dedupe y senales semanticas.
- No reemplazar ranking comercial.
- No modificar orden final todavia.

Provider:

- OpenAI embeddings.
- Modelo default: `text-embedding-3-small`.
- Dimensiones default: `1536`.

## Opcion Local Hugging Face / Sentence Transformers

Para product matching conviene evaluar embeddings locales como features semanticas, antes de entrenar deep learning propio.

Motivacion:

- Costo por experimento casi cero.
- No se mandan titulos/productos a una API externa.
- Permite generar similitud textual para pares de productos reales.
- Puede mejorar recall de `same` donde las features V2 tabulares quedan cortas.

Modelos candidatos:

- `BAAI/bge-m3` — primera opcion experimental actual; multilingue, moderno, fuerte en retrieval, sin prefijo obligatorio para este uso dense.
- `intfloat/multilingual-e5-small` — baseline liviano moderno; requiere cuidar prefijos si se usa como retrieval.
- `intfloat/multilingual-e5-base` — punto medio de calidad/costo.
- `sentence-transformers/paraphrase-multilingual-mpnet-base-v2`
- `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
- `sentence-transformers/distiluse-base-multilingual-cased-v2`

Features posibles:

- `title_embedding_similarity`
- `normalized_title_embedding_similarity`
- `canonical_text_embedding_similarity`
- `brand_model_text_embedding_similarity`

Regla importante:

- Los embeddings son una senal adicional, no reemplazan reglas duras.
- Si hay `storage_conflict`, `screen_size_conflict`, `model_suffix_conflict` o `bundle_conflict`, esa senal sigue pesando fuerte aunque la similitud semantica sea alta.

Uso recomendado para evaluacion:

1. Generar embeddings offline para `matching-v3`.
2. Calcular cosine similarity por par.
3. Entrenar un modelo hibrido tabular + semantico.
4. Evaluar contra frozen test `matching-v3`.
5. Mantener fuera del request path sincrono; usar background async si se activa en runtime.

Implementacion experimental para matching:

```bash
pip install -e ".[ml-text]"

python -m app.matching_dataset evaluate \
  --name matching-v3 \
  --semantic-model bge-m3 \
  --reranker-model bge-reranker-v2-m3
```

Entrenar artefacto V5:

```bash
python -m app.matching_model train \
  --semantic-model bge-m3 \
  --reranker-model bge-reranker-v2-m3 \
  --artifact-path artifacts/matching/model-v5.joblib
```

Detalles:

- Las features semánticas originales viven desde `pair_features_v4`; V5 agrega reranker offline.
- Features agregadas: `title_embedding_similarity`, `normalized_title_embedding_similarity`, `canonical_text_embedding_similarity`, `brand_model_text_embedding_similarity`, `reranker_score_raw_avg`, `reranker_score_same_query_avg`.
- El cache local default es `artifacts/matching/semantic_embedding_cache.joblib`.
- El cache local default del reranker es `artifacts/matching/reranker_score_cache.joblib`.
- La inferencia local se usa en CLI/offline y en worker background; no se llama Sentence Transformers sincronicamente dentro de `/agent/search`.
- Los artefactos guardan `feature_names`, `semantic_embedding_model` y `reranker_model` para poder predecir con el mismo layout.
- Presets CLI: `bge-m3`, `e5-small`, `e5-base`, `minilm`, `mpnet`. Tambien se puede pasar un repo id completo de Hugging Face.

Runtime V5:

```bash
MATCHING_PREDICTIONS_ENABLED=true
MATCHING_MODEL_PREWARM_ENABLED=true
MATCHING_PREDICTION_LIMIT=1000
```

- Prewarm evita la carga fria de BGE/reranker.
- El worker calcula predicciones despues de guardar candidatos si `MatchingPredictionWorker`
  esta instanciado y conectado al callback del worker de persistencia. En `app/main.py`
  actualmente no esta conectado.
- El agente/UI consume por `debug_ref` via `/internal/matching/candidates?run_id=...`.

Kill switch:

```bash
EMBEDDINGS_ENABLED=false
```

Controles de costo:

- No embeddings en startup.
- No embeddings sincronicos dentro de `/agent/search`.
- Backfill manual solamente.
- `dry_run=true` por default.
- Cache obligatorio por `embedding_text_hash`.
- Solo se vectorizan `canonical_products`, no observaciones.
- Texto sintetico corto:
  - titulo normalizado;
  - marca;
  - modelo;
  - categoria;
  - atributos normalizados.
- Nunca HTML completo.
- Nunca raw metadata completo.
- Nunca reviews largas en MVP.
- Max items por corrida: `EMBEDDING_MAX_ITEMS_PER_RUN`.
- Presupuesto mensual: `EMBEDDING_MONTHLY_TOKEN_BUDGET`.
- Costo estimado auditable: `EMBEDDING_ESTIMATED_COST_PER_1M_TOKENS`.

Servicio interno:

`EmbeddingBackfillService.run(dry_run=True, limit=100)` implementa el backfill.
Actualmente no hay endpoint HTTP cableado en `app/main.py`.

Respuesta esperada si se expone por HTTP o se llama desde codigo:

- `processed`
- `skipped`
- `estimated_tokens`
- `estimated_cost_usd`
- `budget_remaining_tokens`
- `errors`
- `dry_run`

Comportamiento sin configurar:

- La API sigue funcionando.
- `semantic_match` queda `null`.
- `/agent/search` agrega warning `semantic_search_unavailable` si no hay match semantico.

Nota importante:

- Actualmente `SemanticMatcher.match(scored)` busca un embedding en `scored.normalized.raw_compact["embedding"]`.
- El flujo normal no adjunta embeddings a los productos scoreados.
- Por eso `/agent/search` suele devolver `semantic_match: null` aunque existan canonical embeddings guardados.
- Es un gap conocido para mejorar: el matcher deberia consultar embeddings guardados por `canonical_key` o generar una representacion comparable sin llamar OpenAI en request.
