# TODO — Decisiones Pendientes

Documento de problemas abiertos con contexto, planteo intermedio y posible solución. Las soluciones son tentativas y están sujetas a cambio según cómo evolucione el producto.

---

## 1. El worker de 6 horas y el histórico probablemente sobran

### Problema

El sistema tiene modulo de scheduler (`app/jobs.py`) y `ScrapeJobRunner`, pero
actualmente no esta conectado en `app/main.py` ni existe endpoint HTTP
`POST /jobs/run-once`. La idea original era correr tracked queries cada 6 horas
para acumular datos históricos en `product_observations`, y que el modelo/agente
consultara el histórico como fuente primaria de precios.

Pero el flujo real que emergió es distinto: el agente scrappea live cada vez que el usuario pregunta, porque la latencia es baja y los datos frescos son siempre preferibles a datos de hace 6 horas. Si el agente tiene herramientas de scraping disponibles, nunca va a preferir el histórico como fuente primaria — sería irresponsable devolver un precio que puede estar desactualizado sin que el usuario lo sepa.

Esto hace que cablear el scheduler como flujo oficial pueda ser una capa de complejidad que no aporte al flujo real:
- Scrappea queries que quizás nadie está preguntando activamente.
- Acumula datos que el agente igualmente va a reemplazar con un fetch live.
- Agrega infraestructura operativa (scheduler, tracked queries, jobs) sin beneficio claro.

### Planteo intermedio

El histórico sí tiene valor, pero no como fuente primaria de precios — sino como **serie de tiempo para señales de tendencia**: "este producto bajó un 15% en los últimos 7 días", "suele estar más barato los fines de semana", "este precio es inusualmente alto respecto a la media histórica". Esas señales no se pueden computar live y son genuinamente útiles para el agente.

El problema es que hoy el histórico se construye con queries sintéticas del worker, no con queries reales de usuarios. Si nadie pregunta por "smart tv 55" pero el worker la trackea igual, se acumula ruido que no refleja el uso real.

### Posible solución

- **Eliminar el worker y las tracked queries** como mecanismo de acumulación activa.
- **Mantener `product_observations`** — se llenan automáticamente como efecto secundario de cada scraping live. El histórico se construye solo a partir de uso real.
- **Señal histórica on-demand** — cuando el agente la necesite, calcularla sobre las observaciones existentes en vez de tenerla pre-computada. Ya existe `get_history_baselines` que hace algo similar.
- **Si en el futuro se necesita cobertura proactiva** (ej: alertas de precio sin que el usuario pregunte), re-evaluar el worker con queries que reflejen uso real, no queries hardcodeadas en config.

### Estado

Pendiente de decisión. No es urgente porque el scheduler no esta activo en `app/main.py`.
Antes de eliminar el modulo conviene confirmar que ningún flujo futuro del agente
depende del histórico pre-computado.

---

## 2. Rate-limiting de persistencia de observaciones via PersistenceWorker

### Problema

Si el histórico se construye como efecto secundario de cada fetch live (ver punto #1), el riesgo es sobrecargar la DB con escrituras repetidas del mismo producto. Un usuario que pregunta por "iPhone 15" tres veces en diez minutos generaría tres observaciones idénticas que no aportan información nueva.

### Planteo intermedio

La infraestructura ya existe: `PersistenceWorker` es una queue de asyncio que drena jobs en background sin bloquear el path de respuesta. Solo hay que agregarle conciencia de qué guardó recientemente.

### Posible solución

Un dict en memoria dentro del worker `{canonical_key: last_saved_at}` actúa como rate-limiter liviano. Si el mismo `canonical_key` fue persistido hace menos de X minutos, el job se descarta silenciosamente. No requiere Redis ni infraestructura externa — vive en el proceso.

```python
OBSERVATION_TTL = timedelta(minutes=30)  # configurable por env

class PersistenceWorker:
    def __init__(self):
        self._recent: dict[str, datetime] = {}  # canonical_key → last_saved_at

    async def _handle_save_observation(self, job):
        key = job.canonical_key
        now = datetime.now(timezone.utc)
        if key in self._recent and now - self._recent[key] < OBSERVATION_TTL:
            return  # skip silencioso
        self._recent[key] = now
        # persistir normal...
```

El TTL podría ser configurable por categoría a futuro — más corto para productos volátiles (celulares en Argentina durante eventos de precio) y más largo para productos estables.

### Consideraciones

- El dict en memoria se pierde si el proceso se reinicia — aceptable, el peor caso es una observación duplicada en el arranque.
- Si en el futuro hay múltiples instancias del servidor, el rate-limiter en memoria no es compartido. Para ese escenario habría que moverlo a Redis o a una columna `last_observed_at` en la DB. Por ahora una sola instancia es el caso real.

### Estado

Pendiente de implementación. Depende de resolver primero el punto #1 (eliminar el worker de 6 horas) para que el flujo de persistencia via fetch live sea el camino oficial.

---

## 4. Features de conflicto en el modelo de matching

Ver [`matching-model-improvements.md`](matching-model-improvements.md) — sección "Problema arquitectónico de fondo" y punto #2.

Resumen: resuelto inicialmente en `pair_features_v2` y luego incorporado a V5. Se agregaron `model_suffix_conflict`, `storage_conflict`, `screen_size_conflict` y `bundle_conflict`; esas señales siguen siendo parte del artefacto activo `match-20260428024114`.

---

## 5. Embeddings locales Hugging Face como features semánticas de matching

### Problema

El frozen test `matching-v3` mostró que el modelo tabular V2 no generaliza tan bien como parecía en el split interno:

- threshold 0.5: accuracy=0.7041, precision=0.8378, recall=0.5741, f1=0.6813, brier=0.184612.
- threshold 0.8: `same_precision=1.0`, pero `same_recall=0.1852`.

La lectura es que los conflict flags son buenos para evitar falsos `same`, pero el modelo pierde muchos `same` reales porque todavía entiende poco texto. Casos como Samsung Buds, modelos de TV (`U8000F`, `DU7000`, `Q7F`), notebooks y electro dependen mucho de semántica del título y variantes de naming.

### Planteo intermedio

No conviene saltar todavía a entrenar deep learning propio: faltan muchos labels y el pipeline sería caro. Pero sí conviene usar un modelo textual preentrenado como extractor de features.

Propuesta: usar embeddings locales de Hugging Face / Sentence Transformers para agregar similitudes de texto al modelo tabular:

- `title_embedding_similarity`
- `normalized_title_embedding_similarity`
- `canonical_text_embedding_similarity`
- opcional: `brand_model_text_embedding_similarity`

Modelos candidatos:

- `BAAI/bge-m3` — primera opción experimental actual por calidad multilingüe/retrieval.
- `intfloat/multilingual-e5-small` — baseline moderno liviano.
- `intfloat/multilingual-e5-base` — punto medio.
- `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` — baseline liviano clásico.
- `sentence-transformers/distiluse-base-multilingual-cased-v2`
- `sentence-transformers/paraphrase-multilingual-mpnet-base-v2`

### Posible solución

Implementar un experimento V3 híbrido:

1. Agregar dependencia opcional `sentence-transformers` / extra `ml-text` o similar.
2. Crear provider local de embeddings Hugging Face con cache por hash de texto/modelo.
3. Generar embeddings offline para los títulos/canonical texts de `matching-v3`.
4. Agregar cosine similarity como features nuevas, sin llamar modelos dentro del request path.
5. Entrenar Logistic Regression o GradientBoosting con:
   - features V2 actuales;
   - conflict flags;
   - similitudes semánticas por embeddings.
6. Evaluar exclusivamente contra frozen test `matching-v3`.

### Consideraciones

- Los embeddings no reemplazan conflict flags: `iPhone 15` y `iPhone 15 Pro` son semánticamente parecidos pero comercialmente distintos.
- Mantener fuera del request path sincrono; V5 ya supero frozen test como prediccion asincronica.
- Criterio de éxito inicial: subir recall de `same` en `matching-v3` sin bajar demasiado precision; idealmente mantener `same_precision >= 0.95` en threshold conservador.
- Si Hugging Face embeddings no mejoran contra frozen test, descartar o posponer sin sumar complejidad al runtime.

### Estado

Implementación experimental agregada:

- `pair_features_v4` sumo las 4 similitudes semánticas como features opcionales; `pair_features_v5` agrega reranker.
- `pair_features_v5` suma `reranker_score_raw_avg` y `reranker_score_same_query_avg` como features opcionales offline/shadow.
- `python -m app.matching_dataset evaluate --name matching-v3 --semantic-model bge-m3` permite comparar contra frozen test sin tocar el request path.
- `python -m app.matching_dataset evaluate --name matching-v3 --semantic-model bge-m3 --reranker-model bge-reranker-v2-m3` evalúa el layout V5.
- `python -m app.matching_model train --semantic-model ... --reranker-model ...` permite entrenar un artefacto híbrido si el frozen test mejora.
- Cache local por hash de texto/modelo en `artifacts/matching/semantic_embedding_cache.joblib`.
- Cache local del reranker por modelo y par textual en `artifacts/matching/reranker_score_cache.joblib`.
- Compatibilidad con artefactos viejos: predicción/evaluación usan `feature_names` del bundle cargado.
- Artefacto activo V5: `match-20260428024114`, `artifacts/matching/model-v5.joblib`.
- Worker runtime: prewarm + prediccion background con `MATCHING_PREDICTIONS_ENABLED=true` y `MATCHING_MODEL_PREWARM_ENABLED=true`.

Resultado experimental sobre `matching-v3`:

- Embeddings BGE-M3 solos mejoran calibracion (`brier` 0.150035 -> 0.142913) y un poco threshold 0.8 (`f1` 0.6173 -> 0.6341), pero no mueven threshold 0.5.
- `BAAI/bge-reranker-v2-m3` solo no sirve como reemplazo: confunde relevancia con "mismo producto exacto".
- Como feature auxiliar, el reranker si aporta:
  - baseline: threshold 0.5 `f1=0.8283`, `brier=0.150035`; threshold 0.8 `f1=0.6173`.
  - `base + BGE-M3 embeddings + bge-reranker same_query_avg`: threshold 0.5 `f1=0.8515`, `brier=0.126674`; threshold 0.8 `f1=0.6667`, precision `0.9333`, recall `0.5185`.
  - `base + BGE-M3 embeddings + raw_avg + same_query_avg`: misma clasificacion pero mejor calibracion (`brier=0.121416`).

Implementado como V5 offline/background: `reranker_score_raw_avg` y `reranker_score_same_query_avg` quedan en cero si no se pasa `--reranker-model`, y el artefacto guarda `reranker_model`.

Rollout runtime:

- `MATCHING_PREDICTIONS_ENABLED=true` activa un worker separado que predice candidatos no etiquetados en background despues de guardar nuevos match candidates.
- `MATCHING_MODEL_PREWARM_ENABLED=true` precarga el artefacto activo, BGE-M3 y el reranker al arrancar el proceso.
- `MATCHING_PREDICTION_LIMIT=1000` controla el batch maximo por disparo.
- El reranker sigue fuera de `/agent/search`; el usuario recibe resultados inmediatos y las predicciones V5 se guardan asincronicamente.

Validacion E2E:

- `/agent/search?query=iphone 15&limit=3&mode=interactive` respondio 200 en 3.114s con `debug_ref=82`.
- El worker genero predicciones V5 para 12/12 candidatos del run.
- El agente/UI puede consumirlas via `/internal/matching/candidates?run_id=82&status=all`.

---

## 6. Normalización y extracción de atributos de producto

### Problema

El matching sigue dependiendo mucho de que `ProductNormalizer` extraiga bien marca, modelo, storage, pulgadas, categoria, condicion, bundle y accesorios. Los modelos semánticos ayudan, pero no reemplazan señales estructuradas: `iPhone 15` vs `iPhone 15 Pro`, `TV 50"` vs `55"`, notebook `i5` vs `i7`, o producto solo vs combo.

### Descubrimientos útiles

- **MAVE** (`google-research-datasets/MAVE`): dataset de attribute value extraction para productos Amazon, con millones de anotaciones de atributo-valor. No está cómodo como dataset HF principal, pero es probablemente la referencia abierta más útil para aprender/enmarcar extracción de atributos.
- **OpenTag / AdaTag / OA-Mine**: líneas de investigación de Amazon/academia para extracción abierta de atributos desde títulos/descripciones. No parecen plug-and-play para nuestro stack, pero confirman que el enfoque correcto es sequence tagging / span extraction + active learning, no solo embeddings.
- **WDC Product Data Corpus / LSPM**: corpus grande de ofertas de producto con clusters y gold standard de matching. Útil para datasets externos de matching y para generar hard negatives con atributos reales.
- **`recordlinkage`, `dedupe`, `splink`**: librerías de entity resolution. No resuelven atributos de producto por sí solas, pero pueden aportar blocking, comparación modular, explainability y evaluación para dedupe a escala.
- **Schema.org / JSON-LD extractors**: antes de inferir atributos desde título, conviene extraer structured data si el HTML lo trae (`brand`, `model`, `gtin`, `mpn`, `sku`, `offers`). Esto puede ahorrar más errores que un modelo.

### Posible solución

Separar normalización en capas:

1. **Structured data first**: cuando el adapter pueda, leer JSON-LD/OpenGraph/meta y guardar `brand`, `mpn`, `gtin`, `sku`, `model`, `category` en `raw_compact`.
2. **Reglas por dominio**: mantener extractores deterministas para specs críticas (`gb/tb`, pulgadas, `pro/ultra/fe/plus`, CPU, RAM, storage, frigorias, litros).
3. **Attribute extraction shadow**: crear un dataset local de spans/atributos desde `matching-v3` + labels manuales, inspirado en MAVE/OpenTag.
4. **Modelo liviano opcional**: recién después, evaluar token classification/span extraction o LLM local para sugerir atributos; nunca reemplazar reglas duras sin validación.
5. **Entity resolution toolkit**: evaluar `recordlinkage`/`dedupe`/`splink` para blocking y feature composition si el volumen crece.

### Estado

Base implementada:

- Helper `extract_structured_product_data` para JSON-LD `Product` y meta tags.
- Adapters guardan `raw_metadata["structured"]` cuando existe.
- VTEX extrae structured data desde JSON de catalogo/product state.
- `ProductNormalizer` prioriza structured `brand/model/category`.
- Reglas nuevas extraen `screen_size`, RAM, storage, CPU, GPU y bundle flags.

Pendiente:

- Dataset local de atributos/spans.
- Persistir/auditar atributos extraidos para revisar errores.
- Reglas por categoria para variantes premium/base y condicion.
- Evaluar MAVE/WDC solo como insumo de dataset, no como dependencia runtime inmediata.
