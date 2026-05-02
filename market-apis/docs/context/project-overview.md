# Project Overview

## Workspace Actual

- Carpeta actual del proyecto: `/Users/maxigimenez/Desktop/dev/market-agent/market-apis`.
- Nombre anterior de la carpeta: `Mercado Libre ETL API`.
- El proyecto no esta inicializado como repo git al ultimo chequeo conocido.
- Stack principal: Python, FastAPI, SQLAlchemy Core, httpx, BeautifulSoup, APScheduler, PostgreSQL.
- DB actual de desarrollo: Supabase PostgreSQL via `DATABASE_URL` en `.env` local.
- Tests: pytest.
- Mercado inicial: Argentina.
- Ubicacion default: Cordoba, codigo postal `5800`.

## Vision Del Proyecto

Construir una API modular de busqueda de productos en tiendas online argentinas. La API debe servir como capa de herramientas para un futuro agente de IA que pueda:

- Recibir una query de producto.
- Elegir que tiendas consultar.
- Scrappear o consultar endpoints publicos de esas tiendas.
- Normalizar productos heterogeneos a un contrato comun.
- Filtrar ruido evidente, como accesorios cuando el usuario busca el producto principal.
- Rankear candidatos por confianza de compra, relevancia y precio.
- Persistir observaciones historicas para detectar senales de precio.
- Exponer una respuesta compacta y opinada para que el agente no tenga que hardcodear reglas internas de cada tienda.

La idea no es crear un marketplace completo, sino una capa ETL + API agent-friendly para comparar productos.

## Filosofia De Diseno

- Pocas tiendas solidas antes que muchas fuentes fragiles.
- Adapters por tienda con una interfaz comun.
- ETL hibrido: busqueda live + persistencia historica.
- Ranking comercial deterministico y explicable.
- Vector search opcional, barato y controlado, solo como ayuda semantica/canonical matching en MVP.
- La API debe degradar bien: si una tienda falla, la respuesta global no debe romperse.
- El futuro agente debe consumir `/agent/search` y no depender de reglas hardcodeadas sobre tiendas.

## Estado General

El proyecto ya tiene una API FastAPI funcional con:

- Busqueda multi-tienda.
- Adapters activos para Mercado Libre, Fravega, Carrefour Argentina, Samsung Argentina, Cetrogar, Easy, BGH y Sony Store Argentina.
- Adapter de Naldo disponible en registry pero fuera de `ACTIVE_STORES` default por baja precision en queries tecnologicas como `iphone 15 pro` (devolvia ruido tipo Pro Plan).
- Routing deterministico por query.
- Normalizacion de productos.
- Scoring explicable.
- Persistencia de raw observations, productos canonicos y transformaciones.
- Endpoint agent-friendly.
- Implementacion interna de streaming por eventos en `SearchService.agent_search_events`; no esta expuesta como endpoint HTTP en `app/main.py`.
- Modulo de jobs programados opcional en `app/jobs.py`; no esta cableado en `app/main.py`.
- Servicio de backfill manual de embeddings con control estricto de costos; no esta expuesto como endpoint HTTP actualmente.
- Routing por perfil de tienda/categoria.
- Modos de busqueda `interactive` y `deep`.
- Metricas por adapter persistidas para observar latencia.
- Infra active learning para matching y frozen tests de campaña.
- Modelo local de matching V5 activo como prediccion asincronica: features tabulares + BGE-M3 embeddings + BGE reranker, sin bloquear `/agent/search`.
- Worker opcional de matching con prewarm para cargar artefacto activo, embeddings y reranker; existe en `app/matching_runtime.py`, pero no esta conectado en `app/main.py`.
- Alembic baseline y migracion de predicciones/modelos para evolucion futura de schema.
- Tests unitarios y de integracion local.

Ultimo resultado de tests conocido:

```bash
.venv/bin/python -m pytest -q
# 79 passed
```

### Modelo de matching activo

- Versión: `match-20260428024114`
- Algoritmo: `logistic_regression_calibrated_sigmoid`
- Dataset global de training: 509 labels binarios (371 same / 138 different).
- Frozen test principal: `matching-v3`, train=196, test=98.
- Features: `pair_features_v5` con features tabulares, 4 similitudes BGE-M3 y 2 scores de reranker.
- Modelos HF: `BAAI/bge-m3` y `BAAI/bge-reranker-v2-m3`.
- Artifact: `artifacts/matching/model-v5.joblib`
- Métricas en `matching-v3`, threshold 0.5: accuracy=0.8469, precision=0.9149, recall=0.7963, f1=0.8515, brier=0.121416.
- Métricas de training held-out interno al entrenar artefacto activo: accuracy=0.9608, precision=0.9605, recall=0.9865, f1=0.9733, brier=0.041828.
- Estado: implementado para predicciones asincronicas sobre candidatos; no modifica ranking ni bloquea `/agent/search`. En la app FastAPI actual falta cablear `MatchingPredictionWorker` para que corra automaticamente.
- Rol de producto: señal auxiliar y escalable para el agente, no juez final de igualdad en MVP. Mientras haya pocas tiendas/pares, el LLM del agente decide si dos ofertas son realmente el mismo producto usando títulos, atributos, condición, precio y la probabilidad V5 como contexto.
- Umbrales actuales: `prob >= 0.60` => `same`, `prob <= 0.20` => `different`, resto `unsure`.
- Validacion E2E historica: `/agent/search?query=iphone 15` respondio en 3.114s; el worker predijo 12/12 candidatos del run con version V5 cuando estaba cableado en runtime.

Flags de produccion recomendados:

```bash
MATCHING_PREDICTIONS_ENABLED=true
MATCHING_MODEL_PREWARM_ENABLED=true
MATCHING_PREDICTION_LIMIT=1000
```

Para reentrenar después de agregar más labels:

```bash
.venv/bin/python -m app.matching_model train \
  --semantic-model bge-m3 \
  --reranker-model bge-reranker-v2-m3 \
  --artifact-path artifacts/matching/model-v5.joblib
```

Para ver estadísticas de labels:

```bash
.venv/bin/python -m app.matching_labeler stats
```

## Estado Mental Del Proyecto

El proyecto ya no es "solo scraper de Mercado Libre". Es una base de API ETL multi-tienda para comparacion agent-friendly. Mercado Libre sigue siendo fuente base, pero la arquitectura ya esta preparada para adapters, routing, transformaciones, historico, scoring y vector search controlado.

La decision mas importante a mantener: el agente debe recibir una salida limpia y opinada desde `/agent/search`; la inteligencia operacional de tiendas, filtros, costos y dedupe debe vivir en esta API. El matching V5 es una señal para ayudar y escalar, pero la decisión final de equivalencia puede quedar en el LLM hasta que haya volumen suficiente para automatizar con confianza.
