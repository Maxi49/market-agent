# TODO: Mejoras al Modelo de Matching

Estado actual del modelo (`match-20260428024114`): `pair_features_v5`, artefacto `artifacts/matching/model-v5.joblib`, BGE-M3 + BGE reranker como features semanticas. El modelo esta activo para predicciones asincronicas en background, sin bloquear `/agent/search` ni alterar ranking.

Frozen test `matching-v3`:

- threshold 0.5: accuracy=0.8469, precision=0.9149, recall=0.7963, f1=0.8515, brier=0.121416.
- threshold 0.8: precision=0.9333, recall=0.5185, f1=0.6667.
- Threshold prod actual: `same >= 0.60`, `different <= 0.20`.

Implementado el 2026-04-27:

- Features de conflicto: `model_suffix_conflict`, `storage_conflict`, `screen_size_conflict`, `bundle_conflict`.
- Reentreno con 200 labels existentes (71 same / 129 different; `unsure` ignorado).
- Recalculo/enriquecimiento de features V2 desde titulos, canonical keys y precios para reutilizar candidatos guardados con JSON V1.
- 95 predicciones persistidas para candidatos unlabeled.
- Sanity checks manuales: `iPhone 15` vs `iPhone 15 Pro`, `S24 FE` vs bundle con Buds y TV 50" vs 55" predicen `different`.

---

## Problema arquitectónico de fondo

El modelo V1 tenía una debilidad estructural: las 8 features eran todas **métricas de similitud** (token overlap, title similarity, price ratio). No había ninguna feature que capturara **diferencia explícita**. Una regresión lineal sobre similitudes no podía distinguir bien `iPhone 15` de `iPhone 15 Pro` porque ambos son muy similares en todo — excepto en el token "Pro", que el modelo no tenía forma de interpretar como conflicto.

**Agregar más labels sin primero agregar features de conflicto era trabajo tirado.** El modelo iba a seguir fallando en los mismos casos porque la señal simplemente no estaba disponible. Esto queda resuelto en V2 para los conflictos cubiertos.

### Reencuadre del problema

Conviene separar la pregunta en dos pasos en vez de intentar responder todo con ML:

**Paso 1 — ¿Son el mismo modelo? (determinista)**
Normalizar cada título a un modelo canónico antes de comparar. En vez de comparar strings crudos, comparar `iphone-15-128gb` vs `iphone-15-pro-128gb`. El `ProductNormalizer` ya existe — probablemente solo hay que afinar qué extrae del campo `model`. Los casos claros (misma marca + mismo modelo canónico + mismo storage) se resuelven con reglas explícitas, sin ML, y son auditables.

**Paso 2 — ¿Son la misma oferta? (ML para los ambiguos)**
Una vez que el paso 1 confirma mismo modelo, el ML solo necesita resolver ambigüedades reales: nuevo vs reacondicionado, bundle vs producto solo, variantes de color. Ese es un problema mucho más acotado y con menos falsos positivos.

Con este approach el ML queda solo para los casos donde la normalización falla o es ambigua — que son bastante menos que todos los pares posibles.

**Orden de trabajo recomendado desde V2:**
1. Revisar falsos positivos/negativos reales entre las 95 predicciones unlabeled.
2. Acumular más labels en casos difíciles si aparecen nuevas variantes.
3. A largo plazo: evaluar si el paso 1 determinista reduce suficientemente el espacio de candidatos ambiguos.

---

## 1. Más labels — especialmente en los casos difíciles

El modelo actual tiene 200 labels pero está sesgado hacia casos fáciles (marcas distintas, tamaños distintos). Los casos que realmente importan para mejorar precisión son los que el modelo confunde.

**Casos a priorizar:**

- `iPhone 15` vs `iPhone 15 Pro` — comparten brand, category, token_overlap alto pero son productos distintos
- `S24` vs `S24+` vs `S24 Ultra` vs `S24 FE` — variantes dentro de la misma línea
- Producto solo vs bundle (mismo producto + accesorio incluido)
- Mismo modelo, diferente año (Samsung TV 2024 vs 2025)
- Notebook con mismo CPU pero distinta línea (HP EliteBook vs HP 250)

**Target:** llegar a 100+ ejemplos `same` y 200+ `different` con buena cobertura de los casos difíciles. Con el dataset actual de 71/129 el recall de `same` es solo 0.61 — el modelo es conservador.

---

## 2. Agregar features de detección de variante/sufijo — implementado

El problema central del fallo `iPhone 15` vs `iPhone 15 Pro` se atacó agregando señales explícitas de conflicto.

**Features nuevas a agregar en `app/matching.py` → `ProductPairFeatures`:**

```python
# En ProductPairFeatures (app/models.py):
model_suffix_conflict: bool = False   # uno tiene "Pro/Ultra/Plus/Max/FE" y el otro no
storage_conflict: bool = False        # ambos especifican GB pero son distintos (128 vs 256)
screen_size_conflict: bool = False    # ambos especifican pulgadas pero distintas (50 vs 55)
bundle_conflict: bool = False         # producto solo vs combo/pack/+ accesorio
```

Implementación en `build_pair_features` (`app/matching.py`):

```python
VARIANT_SUFFIXES = {"pro", "ultra", "plus", "max", "fe", "lite", "mini", "air"}

def _variant_conflict(left_title: str, right_title: str) -> bool:
    left_variants = VARIANT_SUFFIXES & _tokens(left_title)
    right_variants = VARIANT_SUFFIXES & _tokens(right_title)
    # conflicto si uno tiene un sufijo que el otro no tiene
    return bool(left_variants.symmetric_difference(right_variants))

def _storage_conflict(left_title: str, right_title: str) -> bool:
    # extrae tokens con "gb" o "tb" y chequea si son distintos
    ...

def _screen_size_conflict(left_title: str, right_title: str) -> bool:
    # extrae números seguidos de '"' o 'pulgadas' y chequea si son distintos
    ...
```

Estas 3 features son booleanas y muy específicas — prácticamente garantizan `different` cuando son True. El modelo las va a aprender con peso muy alto.

> **Estado:** `FEATURES_VERSION` ya es `pair_features_v2`. Los labels viejos se reutilizan recalculando/enriqueciendo features desde las filas guardadas.

---

## 3. Evaluar en un held-out set real (no solo training) — implementado

Actualmente `evaluate` corre sobre el mismo set de entrenamiento — las métricas están infladas. Para tener una medida honesta hay que separar un 20% de los labels como test set antes de entrenar.

**Cambio en `train_matching_model`:**

```python
from sklearn.model_selection import train_test_split

X_train, X_test, y_train, y_test = train_test_split(
    features, labels, test_size=0.2, random_state=42, stratify=labels
)
estimator.fit(X_train, y_train)
# calcular métricas sobre X_test, y_test
```

Con 200 labels el split 80/20 deja 40 ejemplos de test. Resultado V2: accuracy=0.975, precision=0.9333, recall=1.0, f1=0.9655, brier=0.034338.

---

## 4. Considerar un modelo más expresivo cuando haya 500+ labels

`LogisticRegression` es lineal — no puede capturar interacciones entre features (ej: "brand_agreement=1 AND numeric_token_agreement=0 → probably different"). Con más datos vale la pena probar:

- `RandomForestClassifier` — maneja interacciones sin tuning, interpretable via feature importance
- `GradientBoostingClassifier` / `XGBClassifier` — mejor performance general con datasets medianos
- `SVC(kernel='rbf', probability=True)` — buen baseline no-lineal

El código ya está estructurado para soportar esto: `_build_estimator` elige el estimador según cantidad de labels. Se puede extender con un tercer umbral para datasets más grandes.

---

## 5. Feature importance — entender qué está usando el modelo

Rápido de hacer, muy útil para diagnosticar:

```python
import joblib, numpy as np
bundle = joblib.load("artifacts/matching/model.joblib")
# Para LogisticRegression calibrado con CalibratedClassifierCV:
estimator = bundle["estimator"]
# Los coeficientes están en los calibrators internos
coefs = np.mean([
    clf.base_estimator.coef_[0]
    for clf in estimator.calibrated_classifiers_
], axis=0)
for name, coef in zip(bundle["feature_names"], coefs):
    print(f"{coef:+.3f}  {name}")
```

Esperado: `rare_token_overlap` y `brand_agreement` con coeficiente positivo alto; `accessory_mismatch` negativo fuerte. Si `title_similarity` tiene coeficiente más alto que `rare_token_overlap` hay un problema — título completo es ruidoso.

---

## 6. Bundle detection como feature explícita — implementado

El fallo del caso `S24 FE + Galaxy Buds4` muestra que el modelo no tiene señal para bundles. Una feature simple:

```python
BUNDLE_TOKENS = {"kit", "pack", "combo", "bundle", "incluye"}

def _is_bundle(title: str) -> bool:
    # "+" cuenta solo como separador con espacios para evitar specs tipo camara 50+12+8.
    ...

# En ProductPairFeatures:
bundle_conflict: bool = False  # uno es bundle y el otro no
```

---

## Resumen de prioridades

| # | Mejora | Impacto esperado | Esfuerzo |
|---|---|---|---|
| 1 | Más labels en casos difíciles (Pro/Ultra/variantes) | Alto | Bajo (etiquetar) |
| 2 | Features `model_suffix_conflict`, `storage_conflict`, `screen_size_conflict` | Implementado | Medio |
| 3 | Held-out test set en evaluación | Implementado | Bajo |
| 6 | Feature `bundle_conflict` | Implementado | Bajo |
| 4 | RandomForest/GBM cuando haya 500+ labels | Alto (largo plazo) | Medio |
| 5 | Feature importance analysis | Diagnóstico | Muy bajo |

**Siguiente ROI inmediato:** revisar casos reales V5 en banda `0.50-0.60`, variantes premium/base y cambios `different/unsure`, antes de bajar threshold o tocar ranking.

## 7. Embeddings locales como features semanticas — proximo experimento

Despues de `matching-v3`, la mejora mas prometedora no es entrenar deep learning propio desde cero, sino usar embeddings preentrenados de Hugging Face / Sentence Transformers como extractor textual.

Idea:

- Generar embeddings locales para titulos y textos canonicos.
- Calcular cosine similarity por par.
- Agregar esas similitudes como features al modelo tabular.
- Evaluar contra frozen test `matching-v3`.

Modelos candidatos:

- `BAAI/bge-m3`
- `intfloat/multilingual-e5-small`
- `intfloat/multilingual-e5-base`
- `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
- `sentence-transformers/distiluse-base-multilingual-cased-v2`
- `sentence-transformers/paraphrase-multilingual-mpnet-base-v2`

Features sugeridas:

- `title_embedding_similarity`
- `normalized_title_embedding_similarity`
- `canonical_text_embedding_similarity`
- `brand_model_text_embedding_similarity`

Principio:

- Los embeddings ayudan a recuperar `same` reales con naming distinto.
- No deben anular conflict flags: productos semantemente parecidos pueden ser comercialmente distintos.
- Mantener en shadow mode y medir contra `matching-v3` antes de tocar ranking/dedupe.

Resultado de experimentos:

- BGE-M3 embeddings solos mejoraron calibracion pero no movieron threshold 0.5.
- `BAAI/bge-reranker-v2-m3` solo no sirve como clasificador exact-match, porque rankea relevancia semantica y puede puntuar alto variantes comerciales distintas.
- `BAAI/bge-reranker-v2-m3` como feature extra si aporto:
  - baseline: threshold 0.5 `f1=0.8283`, `brier=0.150035`; threshold 0.8 `f1=0.6173`.
  - features tabulares + BGE-M3 embeddings + reranker `same_query_avg`: threshold 0.5 `f1=0.8515`, `brier=0.126674`; threshold 0.8 `f1=0.6667`.
  - agregar `raw_avg` mantuvo clasificacion y mejoro calibracion a `brier=0.121416`.

Implementación aplicada:

- `pair_features_v5` con `reranker_score_raw_avg` y `reranker_score_same_query_avg`, calculadas offline/shadow.
- CLI offline acepta `--semantic-model bge-m3 --reranker-model bge-reranker-v2-m3` para evaluate/train.
- Runtime con prewarm + background async: usar `MATCHING_PREDICTIONS_ENABLED=true` y `MATCHING_MODEL_PREWARM_ENABLED=true`.
- No usar reranker sincronicamente en `/agent/search`; queda fuera del request path.

Mediciones:

- Modelos calientes + textos nuevos: BGE-M3 + reranker `~0.133s/par`.
- Carga fria puede sumar ~16s; prewarm es obligatorio para prod.
- Prueba E2E: `/agent/search` respondio y luego V5 predijo 12/12 candidatos del `run_id`.

## 8. Normalizacion / attribute extraction como prioridad

La mejora semantica no reemplaza atributos estructurados. El siguiente bloque de trabajo deberia mejorar `ProductNormalizer` y el dataset de atributos.

Hallazgos:

- **MAVE** (`google-research-datasets/MAVE`): dataset grande de product attribute value extraction sobre Amazon; util para inspirar formato local de spans/atributos.
- **OpenTag / AdaTag / OA-Mine**: enfoques de sequence tagging / open-world attribute extraction; no son plug-and-play, pero validan la direccion.
- **WDC Product Data Corpus / LSPM**: corpus externo fuerte para product matching y hard negatives.
- **`recordlinkage`, `dedupe`, `splink`**: librerias de entity resolution utiles para blocking, comparison features, explainability y escalabilidad.
- **Schema.org JSON-LD**: antes de inferir desde titulo, los adapters deberian capturar structured data cuando exista (`brand`, `mpn`, `gtin`, `sku`, `model`, `offers`).

Propuesta:

1. Extraer structured data primero en adapters.
2. Mantener reglas deterministas por dominio para specs conflictivas.
3. Crear dataset local de atributos/spans desde casos reales.
4. Evaluar modelo span/token-classification o LLM local solo como sugeridor shadow.

Estado aplicado:

- Structured data first implementado para JSON-LD/meta y VTEX.
- `ProductNormalizer` prioriza structured `brand/model/category`.
- Reglas deterministas nuevas: pulgadas, RAM/storage, CPU/GPU y bundle.

Pendiente:

- Dataset local de atributos/spans.
- Reglas mas duras para condicion y variantes comerciales: LED/OLED/QLED, Pro/Plus/Ultra/FE/base, nuevo/reacondicionado.
