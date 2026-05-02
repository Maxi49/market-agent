# Routing Normalization Scoring

## Routing

Archivo: `app/routing.py`.

Responsabilidad: decidir que tiendas consultar segun query, perfil de tienda/categoria/marca y generar explicacion.

El routing actual usa perfiles estaticos:

- `strong`: tienda prioritaria para esa categoria/marca.
- `ok`: tienda aceptable.
- `weak`: tienda comercialmente debil para la query, se difiere/omite en interactivo.
- `blocked`: tienda no debe consultarse para esa query.

Razones esperadas:

- `selected_by_strong_profile`
- `selected_by_ok_profile`
- `deferred_weak_profile`
- `blocked_by_store_profile`

Ejemplos actuales:

- `iphone 15`
  - Consulta Mercado Libre y Fravega.
  - Excluye Samsung por Apple/iPhone.
  - Excluye Carrefour por perfil debil/bloqueado para Apple smartphones.

- `galaxy s24`
  - Incluye Samsung.
  - Excluye Carrefour.

- `smart tv 55`
  - Incluye Mercado Libre, Fravega, Carrefour y Samsung.

Objetivo: que el agente no tenga que saber cosas como "no buscar iPhone en Samsung".

## Normalizacion

Archivo: `app/normalization.py`.

Responsabilidades:

- Normalizar texto.
- Detectar marcas.
- Detectar categorias.
- Detectar modelo/capacidad/color cuando sea posible.
- Detectar accesorios.
- Detectar condicion.
- Generar `canonical_key`.
- Generar `raw_compact`.

Ejemplo de objetivo:

- `Apple iPhone 15 128 GB Negro`
- `Celular iPhone 15 128gb negro`

Ambos deberian producir canonical keys compatibles.

Accesorios a marcar:

- fundas
- protectores
- cargadores
- cables
- soportes
- repuestos
- S-Pen/repuestos si la query busca el producto principal

## Ranking Legacy

Archivo: `app/ranking.py`.

Usado por `/search` y `/best`.

Ranking V1:

- Descartar accesorios cuando la query parece pedir producto principal.
- Penalizar usados/reacondicionados salvo query explicita.
- Ordenar por precio visible.
- Usar posicion original y sponsored como desempates.

Terminos conocidos de accesorios:

```text
adaptador, cable, carcasa, case, cargador, funda, hybrid, lamina, magfit,
pen, protector, proteccion, repuesto, s-pen, soporte, spigen, templado,
vidrio
```

## Scoring Agent-Friendly

Archivo: `app/scoring.py`.

El score de `/agent/search` busca confianza de compra. Factores:

- Relevancia.
- Precio.
- Condicion.
- Disponibilidad.
- Confianza vendedor/tienda.
- Envio.
- Reviews/rating.
- Penalizaciones.

El resultado incluye `ScoreBreakdown` para explicar el score.

Principio importante: un producto nuevo y disponible puede ganarle a uno reacondicionado mas barato si la query no pidio reacondicionado.

## Matching Async / Active Learning

Archivo: `app/matching.py`.
Modelo local: `app/matching_model.py`.

Objetivo:

- Generar dataset propio para product matching sin OpenAI.
- Comparar pares de productos con features genericas.
- Persistir `match_confidence` heuristico y labels humanos.
- Entrenar un modelo local con labels humanos y features semanticas locales.
- No modificar ranking ni scoring publico: V5 predice asincronicamente y el agente/UI lo consume por `debug_ref`.
- En MVP, dejar que el LLM del agente sea el juez final de equivalencia. V5 sirve como señal calibrada, priorizador y herramienta de escala, no como decisión automática irreversible.

Features actuales (`pair_features_v5`):

- token overlap
- rare/model token overlap
- numeric token agreement
- title similarity
- brand agreement
- category agreement
- accessory mismatch
- model suffix conflict (`Pro`, `Ultra`, `Plus`, `Max`, `FE`, etc.)
- storage conflict (`128GB` vs `256GB`, ambos especificados)
- screen size conflict (`50"` vs `55"`, ambos especificados)
- bundle conflict (producto solo vs combo/pack/` + ` accesorio)
- canonical key match
- price ratio
- BGE-M3 similarities: title, normalized title, canonical text y brand/model text
- BGE reranker scores: raw bidireccional y `same_query` bidireccional

Flujo de candidatos:

1. `/agent/search` obtiene productos y ranking comercial como siempre.
2. En background, `PersistenceWorker` genera candidatos de matching sobre top 20 productos no accesorios.
3. Solo se guardan pares cross-store con confidence incierta (`0.35-0.75`).
4. Si `MatchingPredictionWorker` esta instanciado y conectado en runtime, calcula predicciones V5. Actualmente `app/main.py` no lo cablea.
5. Endpoints internos `/internal/matching/*` permiten listar candidatos, etiquetar y ver resumen.

Etiquetado humano:

- CLI: `python -m app.matching_labeler review --limit 100`.
- Stats/readiness: `python -m app.matching_labeler stats`.
- Labels: `same`, `different`, `unsure`.
- El training usa solo `same` y `different`; `unsure` queda como auditoria.
- Minimo operativo: 50 labels utiles, con al menos 10 `same` y 10 `different`.
- Recomendado antes del primer modelo real: 30 `same` y 30 `different`.

Modelo local V5:

- Usa `LogisticRegression(class_weight="balanced", max_iter=1000)` sobre features numericas estables.
- Si hay al menos 100 labels utiles y 20 por clase, usa calibracion sigmoid con `CalibratedClassifierCV`.
- Recalcula/enriquece features desde titulos, canonical keys y precios al entrenar/predictir para reutilizar labels/candidatos guardados con JSON viejo.
- En V5 agrega BGE-M3 y BGE reranker desde artefacto/cache local.
- Calcula metricas held-out 20% durante training cuando hay datos suficientes; `evaluate` sigue reportando performance sobre todos los labels disponibles.
- Entrena solo con labels `same` y `different`; ignora `unsure`.
- Guarda artifact en `artifacts/matching/model-v5.joblib`.
- Persiste metadata en `product_match_models` y predicciones en `product_match_predictions`.
- Expone `model_match_probability`, `model_decision` y `model_version` en candidatos internos si ya hay prediccion.

Comandos:

```bash
python -m app.matching_model train --semantic-model bge-m3 --reranker-model bge-reranker-v2-m3 --artifact-path artifacts/matching/model-v5.joblib
python -m app.matching_model predict-unlabeled --limit 1000
python -m app.matching_model evaluate
```

## Dataset V3 / Frozen Test

Archivo: `app/matching_dataset.py`.

Objetivo:

- Generar un dataset nuevo con scrapers reales y queries variadas.
- Separar una campaña de dataset del training activo.
- Mantener 200 pares para train y 100 pares como frozen test.
- Evitar que labels del frozen test entren accidentalmente a `product_match_candidates.label`.

Flujo recomendado:

```bash
python -m app.matching_dataset build-campaign --name matching-v3
python -m app.matching_dataset sample-campaign --name matching-v3 --target-train 200 --target-test 100
python -m app.matching_dataset review --name matching-v3 --split train
python -m app.matching_dataset review --name matching-v3 --split test
python -m app.matching_dataset freeze --name matching-v3
python -m app.matching_dataset evaluate --name matching-v3
```

Sampling:

- `uncertainty`: probabilidad del modelo activo entre 0.2 y 0.8.
- `high_risk`: probabilidad alta o conflictos explicitos.
- `random`: muestra estratificada de relleno.
- `deliberate`: accesorios/bundles.

Importante:

- Los labels de campaña viven en `matching_dataset_items.label`.
- `evaluate` entrena solo con split `train` y reporta sobre split `test`.
- Reporta confusion-style metrics, Brier/calibration buckets y precision de `same` con thresholds 0.50, 0.80, 0.90 y 0.95.

Resultado inicial `matching-v3`:

- Pool: 659 pares unicos.
- Split: train=200, test=100.
- Labels binarios usados por evaluate: train=196, test=98.
- Frozen test threshold 0.5: accuracy=0.7041, precision=0.8378, recall=0.5741, f1=0.6813, brier=0.184612.
- Frozen test threshold 0.8: same precision=1.0, same recall=0.1852.
- Estado: baseline antiguo para comparar mejoras futuras.

Resultado V5 `matching-v3`:

- Frozen test threshold 0.5: accuracy=0.8469, precision=0.9149, recall=0.7963, f1=0.8515, brier=0.121416.
- Frozen test threshold 0.8: precision=0.9333, recall=0.5185, f1=0.6667.
- Artefacto activo: `match-20260428024114`.

Importante:

- Sigue sin modificar `best_matches`.
- El agente/UI consume predicciones via `/internal/matching/candidates?run_id=...`.
- Antes de usar matching para penalizar ranking, hay que revisar falsos positivos de variantes/condicion.

## Enrichment

Archivo: `app/enrichment.py`.

Estado actual:

- Existe `ProductEnricher.enrich_top(top_n=5)`.
- Hoy es liviano y no hace requests extra.
- Fue dejado como seam para futura entrada a paginas de detalle.

Futuro:

- Mercado Libre: reputacion vendedor, tienda oficial, garantia, atributos tecnicos, reviews si son accesibles.
- VTEX: specs, seller, stock, promociones, garantia, cuotas.
- Mantener limite top 5 para no multiplicar costos/latencia/scraping.
