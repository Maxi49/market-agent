# Scraping Adapters

## Tiendas Activas

### Mercado Libre (`mercado_libre`)

- Adapter actual en codigo:
  - `app/scrapers/search_index_mercado_libre.py`: busqueda por indice externo via SerpApi, sin tocar servidores de Mercado Libre.
- `app/scrapers/registry.py` registra `MercadoLibreSearchIndexAdapter` directamente.
- No hay adapter HTML ni adapter Apify cableados en el codigo actual.
- Estado actual: fuente critica pero inestable. Mercado Libre empezo a responder a requests HTML con una micro-landing/challenge anti-bot en vez de resultados.
- Señales observadas en la respuesta bloqueada:
  - `HTTP 200`, pero body chico de ~5.5 KB.
  - header `x-is-search-bot: true`.
  - HTML con challenge JS (`verifyChallenge`, `_bmstate`, `_bmc`).
  - sin selectors de resultados (`ui-search-layout__item`, `poly-component__title`).
- Resultado: el scraper HTML devuelve 0 productos aunque existan publicaciones reales.
- La API oficial OAuth fue probada:
  - OAuth authorization code funciona y devuelve `access_token` `APP_USR-...`.
  - `GET https://api.mercadolibre.com/sites/MLA/search?q=...` devuelve `403 forbidden` incluso con `Authorization: Bearer APP_USR-...`.
  - Conclusión: la API oficial actual no sirve para busqueda general marketplace con los scopes/app probados; esta orientada principalmente a integraciones de negocio/seller.
- Pruebas reales historicas con Apify, 2026-04-29:
  - `crawlerbros/mercadolibre-scraper`: falló con `MercadoLibre search was blocked on every attempt`; intentos residenciales tambien bloqueados.
  - `sourabhbgp/mercadolibre-scraper`: `SUCCEEDED` pero 0 items; logs con `ANTIBOT_JS_SHIM` y `ANTIBOT_CAPTCHA_PAGE`.
  - `easyapi/mercadolibre-search-results-scraper`: 0 items.
  - `saswave/mercadolibre-product-scraper`: falla parseando HTML bloqueado (`__PRELOADED_STATE__` / `__NORDIC_RENDERING_CTX__` ausentes).
  - `ecomscrape/mercadolibre-product-search-scraper`: requiere rentar actor pago.
  - `duvan517x/mercadolibre-scraper-product-scraper`: devolvio 3 items, pero tardo ~2m24s y la relevancia fue mala para `iphone 15 pro 128gb` (devolvio iPhone 13/14 antes).
- Decision operativa:
  - No usar Mercado Libre/Apify en el path interactivo como fuente confiable hasta resolver proveedor.
  - Si se usa Apify, preferir background/cache o modo deep, no request sincrono de agente.
  - Exponer diagnosticos por tienda para que el agente no interprete "0 resultados" como ausencia real de productos.
- Search index actual:
  - Consulta SerpApi con `engine=google_shopping`, `google_domain=google.com.ar`, `gl=ar`, `hl=es`.
  - No envia `location`, para no cerrar resultados a una ciudad; la localizacion queda a nivel Argentina con `gl=ar` y `google_domain=google.com.ar`.
  - Filtra resultados donde `source` sea Mercado Libre/MercadoLibre o el link apunte a un host de Mercado Libre.
  - Usa `extracted_price` solamente desde el mismo item de Shopping.
  - No hace enriquecimiento de precio por similitud de titulo contra otros resultados.
  - Si SerpApi devuelve `second_hand_condition` como `de segunda mano`, el adapter marca el `Product.condition` como `used` para que ranking/scoring lo penalicen cuando la query no pide usados.
  - Solo expone como `product_url` un link directo si el host ya es Mercado Libre. Cuando Google Shopping devuelve un link indirecto (`google.com.ar/search?...`, immersive/shopping), el adapter usa una URL de busqueda segura en Mercado Libre basada en el titulo (`https://listado.mercadolibre.com.ar/...`) para evitar 404.
  - Requiere `SERPAPI_API_KEY` o `SERP_API_KEY`.
  - No usa `SERPER_API_KEY`, Google CSE ni selector `MERCADO_LIBRE_PROVIDER` en el codigo actual.
  - Configuracion:

```bash
SERPAPI_API_KEY=...
# o:
SERP_API_KEY=...
```

  - Campos:
    - `title`, `product_url`, `image_url`, `source`, `rating`, `reviews`.
    - `price` desde `extracted_price` del propio item de Shopping, o texto `price` del mismo item como fallback de menor confianza.
    - `raw_metadata["provider_family"]="search_index"`.
    - `raw_metadata["engine"]="google_shopping"`.
    - `raw_metadata["reliability"]="low"`.
    - `raw_metadata["price_reliability"]="medium"` con `extracted_price`, `"low"` con texto, `"unknown"` sin precio.
    - `raw_metadata["link_reliability"]="direct"` cuando el link apunta a Mercado Libre; `"search_fallback"` cuando se genero busqueda segura.
    - `raw_metadata["google_product_link"]` conserva el link original de Google Shopping cuando se uso fallback.
  - Limitaciones:
    - precio puede estar cacheado o mezclado con precio anterior/promocion;
    - stock no verificado;
    - puede devolver usados o publicaciones de segunda mano junto con nuevos;
    - Google Shopping suele no entregar una URL directa de publicacion de Mercado Libre; el fallback evita 404, pero no garantiza abrir la publicacion exacta;
    - para resolver permalinks exactos habria que sumar una etapa opcional con Google Immersive Product/otra fuente, con mas latencia y costo;
    - sirve como descubrimiento/fallback, no como fuente final de compra.
  - Prueba live `Smart TV 4K 55 pulgadas`: devolvio 5 resultados de Mercado Libre via Google Shopping; uno tenia precio muy bajo (`$18.000`) y venia marcado por SerpApi como `second_hand_condition=de segunda mano`, por lo que ahora se clasifica como `used`.
- Seguridad:
  - Rotar tokens expuestos accidentalmente durante pruebas (`APIFY_TOKEN`, Mercado Libre client secret/tokens).
  - No loguear tokens completos ni ponerlos en query string.

### Fravega (`fravega`)

- Adapter: `app/scrapers/fravega.py`.
- Estrategia: parseo HTML / tarjetas renderizadas por Next.js.
- Buena fuente para tecnologia/electro.

### Carrefour Argentina (`carrefour_ar`)

- Adapter: `CarrefourAdapter` en `app/scrapers/vtex.py`.
- Plataforma: VTEX.
- Estrategia:
  - primero intenta APIs publicas VTEX JSON;
  - fallback a estado embebido en HTML.
- Util para electrodomesticos, retail general y algunos productos tecnologicos.

### Samsung Argentina (`samsung_ar`)

- Adapter: `SamsungAdapter` en `app/scrapers/vtex.py`.
- Plataforma: VTEX.
- Debe usarse principalmente con queries Samsung-native:
  - `galaxy`
  - `samsung`
  - `smart tv samsung`
  - `heladera samsung`
  - tablets/celulares/TV/electrodomesticos Samsung.
- El router excluye Samsung para queries Apple/iPhone porque Samsung no vende iPhone y puede devolver accesorios irrelevantes.

### Cetrogar (`cetrogar_ar`)

- Adapter: `CetrogarAdapter` en `app/scrapers/vtex.py`.
- Plataforma: VTEX.
- Perfil similar a Fravega: fuerte en tecnologia, TV y electrodomesticos.

### Easy Argentina (`easy_ar`)

- Adapter: `EasyAdapter` en `app/scrapers/vtex.py`.
- Plataforma: VTEX.
- Fuerte en hogar, electro y TV; weak para smartphones/notebooks.

## Link Guard

- Archivo: `app/link_guard.py`.
- `SearchService` lo aplica sobre los candidatos finales de `/agent/search` y `/agent/search/stream` antes de construir `best_matches`.
- Objetivo: evitar que el agente entregue links directos muertos de cualquier tienda.
- Comportamiento:
  - valida solo hosts esperados por tienda para no hacer requests a URLs de prueba o dominios externos;
  - reemplaza links con status `404` o `410` por una URL de busqueda de la tienda basada en el titulo;
  - no considera `403` como link muerto, porque varias tiendas bloquean clientes automatizados pero abren en navegador;
  - agrega `link_dead_fallback:<store_id>` en `risks` del match cuando reemplaza el link;
  - conserva `raw_metadata["original_product_url"]`, `raw_metadata["link_status"]` y `raw_metadata["link_reliability"]="search_fallback_dead_link"` en el producto transformado.
- Fallbacks actuales:
  - Mercado Libre: `https://listado.mercadolibre.com.ar/<titulo>`
  - Fravega: `https://www.fravega.com/l/?keyword=<titulo>`
  - Amazon US: `https://www.amazon.com/s?k=<titulo>`
  - Samsung: `https://shop.samsung.com/ar/search?q=<titulo>`
  - VTEX locales: `/<titulo>?_q=<titulo>&map=ft` sobre el dominio de la tienda.

### Megatone (`megatone_ar`)

- Adapter: `app/scrapers/megatone.py`.
- Plataforma: **Doofinder** (SaaS de búsqueda para e-commerce, no VTEX).
- Estrategia: llamada directa al API JSON de Doofinder — sin scraping HTML.
- Endpoint:
  ```
  GET https://us1-search.doofinder.com/6/7d78864dfd68192d967ce98f7af00970/_search?page=1&rpp=50&query={q}
  ```
- Auth: ninguna. El hashid es público por diseño (widget client-side).
- Headers requeridos: `Referer: https://www.megatone.net/` y `Origin: https://www.megatone.net` (CORS check del lado de Doofinder).
- Campos disponibles en la respuesta:
  - `title`, `link` (URL producto), `image_link`
  - `best_price` / `sale_price` — precio con descuento
  - `price` — precio de lista (original)
  - `calculated_discount` — porcentaje de descuento
  - `highlight_installments` — cuotas como texto (ej: `"6 x $450883 Rebajas"`)
  - `availability` — `"in Stock"` / otro
  - `brand`, `id` (SKU), `gtin`, `category_name`, `category_path`
- Discovery: el hashid se encontró hardcodeado en `/next/Files/Vue/Listados/ResultadosBusqueda/ResultadosBusqueda.vue` junto con la URL del endpoint. La página de resultados es un SPA que carga el componente Vue dinámicamente; el HTML del server-render es solo un shell de 5KB.
- Perfil de tienda: electrodomésticos, TVs, celulares, audio, climatización. Similar a Fravega.

### BGH (`bgh_ar`)

- Adapter: `BGHAdapter` en `app/scrapers/vtex.py`.
- Plataforma: VTEX.
- Marca/fuente propia para electrodomesticos, aire acondicionado, microondas y TVs.
- Router bloquea notebooks y prioriza productos BGH.

### Naldo Lombardi (`naldo_ar`)

- Adapter: `NaldoAdapter` en `app/scrapers/vtex.py`.
- Plataforma: VTEX.
- Retail electro/tecno similar a Fravega/Cetrogar.
- Estado: clase disponible en `vtex.py`, pero no registrada en `app/scrapers/registry.py` actualmente.

### Sony Store Argentina (`sony_ar`)

- Adapter: `SonyAdapter` en `app/scrapers/vtex.py`.
- Plataforma: VTEX.
- Tienda de marca. Router la bloquea salvo queries Sony/Bravia o categorias donde tenga sentido.

### Amazon US (`amazon_us`)

- Adapter: `app/scrapers/amazon_serpapi.py`.
- Provider: SerpApi `engine=amazon`.
- Estado: opcional, no activo por default.
- Activacion:

```bash
AMAZON_PROVIDER=serpapi
SERP_API_KEY=...
# o SERPAPI_API_KEY=...
AMAZON_SERPAPI_DOMAIN=amazon.com
AMAZON_SERPAPI_LANGUAGE=en_US
AMAZON_SERPAPI_SHIPPING_LOCATION=ar
ACTIVE_STORES="mercado_libre,fravega,cetrogar_ar,amazon_us"
```

- Usa la misma key SerpApi global que Mercado Libre search-index; no existe `AMAZON_SERPAPI_API_KEY`.
- Campos:
  - `title`, `product_url`, `image_url`, `rating`, `reviews_count`.
  - `price` desde `extracted_price` cuando SerpApi lo devuelve.
  - `raw_metadata["provider"]="serpapi"`.
  - `raw_metadata["engine"]="amazon"`.
  - `raw_metadata["asin"]` cuando exista.
  - `raw_metadata["reliability"]="medium"`.
  - `raw_metadata["price_reliability"]="medium"` con `extracted_price`, `"low"` si se parsea desde texto.
- Uso recomendado:
  - tecnologia internacional, audio, notebooks, gaming, accesorios, Kindle/libros.
  - comparar referencia global o encontrar modelos que no aparecen bien en tiendas locales.
- Limitaciones:
  - Amazon US no es tienda local argentina;
  - precio final puede cambiar por envio, impuestos/importacion, garantia y disponibilidad;
  - el agente debe avisar esas restricciones y no presentarlo como precio final local garantizado.

## Tiendas Inactivas / Postergadas

### Musimundo

- Archivo: `app/scrapers/musimundo.py`.
- Estado: fuera de V1 activa.
- Motivo: las rutas publicas observadas devolvian mantenimiento, redirecciones a home o HTML no util.
- No esta en `app/scrapers/registry.py`.
- No esta en `ACTIVE_STORES` default.
- Mantener el archivo solo como referencia hasta decidir si se reintenta.

Opciones futuras para reemplazar o sumar:

- OnCity.
- Sodimac para hogar/muebles.
- Coto/Dia/Jumbo/Vea/Disco para supermercado, pero como vertical separada por dependencia fuerte de ubicacion y stock.
- CompraGamer/Maximus si se quiere tecnologia/hardware especializado.

## StoreAdapter Interface

Todo adapter debe implementar:

```python
async def search(
    query: str,
    limit: int,
    location: SearchLocation,
    mode: SearchMode = SearchMode.INTERACTIVE,
) -> list[Product]:
    ...
```

Reglas para nuevos adapters:

- Devolver `Product` lo mas completo posible.
- `title`, `price`, `product_url`, `store_id`, `store_name` y `position` son esenciales.
- Usar `raw_metadata` solo para metadata compacta y relevante.
- No guardar HTML completo.
- Lanzar `ScraperError` para fallas esperables de fetch/parseo.
- No romper la respuesta global por una tienda fallida.

## VTEX Research Notes

VTEX tiene al menos dos rutas utiles:

- Classic Catalog Search.
- Intelligent Search.

Notas observadas:

- Search results dependen del catalogo indexado.
- Intelligent Search puede autocorregir, ampliar multi-term searches y rankear por relevancia/config merchant.
- `page=1` es valido en Intelligent Search.
- `page=0` devuelve error: `Page should be greater than 0.`

Endpoints observados:

```text
https://www.carrefour.com.ar/api/catalog_system/pub/products/search/iphone%2015?_from=0&_to=2
https://www.carrefour.com.ar/api/catalog_system/pub/products/search?ft=iphone%2015&_from=0&_to=2
https://shop.samsung.com/ar/api/catalog_system/pub/products/search?ft=iphone%2015&_from=0&_to=0
https://www.carrefour.com.ar/api/io/_v/api/intelligent-search/product_search/search?query=iphone%2015&page=1&count=1
https://shop.samsung.com/ar/api/io/_v/api/intelligent-search/product_search/search?query=iphone%2015&page=1&count=1
```

Comportamiento observado:

- Carrefour devuelve productos por VTEX JSON.
- Samsung puede devolver vacio o accesorios Samsung para queries no Samsung, como `iphone 15`.
- Esto valida la necesidad del `StoreRouter`.

Implementacion actual:

- En `interactive`, VTEX corre Catalog Search e Intelligent Search en paralelo y retorna el primer set no vacio.
- En `deep`, VTEX intenta JSON y luego fallback HTML.
- No se usa Playwright/Selenium.
- No se migro a Scrapy; se adoptaron principios de budgets, metricas y estrategias por fuente dentro del stack actual.

Docs utiles:

- VTEX Intelligent Search API overview: https://developers.vtex.com/docs/guides/intelligent-search-api-overview
- VTEX Catalog API overview: https://developers.vtex.com/docs/guides/catalog-api-overview
- How VTEX Search works: https://help.vtex.com/en/docs/tutorials/how-does-vtex-search-work
- Intelligent Search behavior: https://help.vtex.com/en/docs/tutorials/search-behavior

## Musimundo Research Notes

Rutas observadas:

```text
https://www.musimundo.com/search?text=notebook
https://www.musimundo.com/SearchDisplay?searchTerm=notebook
https://www.musimundo.com/tecnologia/celulares
https://www.musimundo.com/api/catalog_system/pub/products/search?ft=notebook&_from=0&_to=0
```

Resultado:

- Search/category pages devolvieron mantenimiento.
- La ruta tipo VTEX respondio `301` a home.
- Luego devolvio HTML en vez de JSON de productos.

Decision:

- Musimundo queda fuera de V1 activa.
- Si vuelve a funcionar, validar con fixtures antes de reactivarlo.

## Como Agregar Una Nueva Tienda

1. Crear adapter en `app/scrapers/<tienda>.py`.
2. Implementar `StoreAdapter.search`.
3. Devolver `Product` normalizado.
4. Agregar fixtures HTML/JSON en `tests/fixtures`.
5. Agregar tests unitarios del parser.
6. Registrar adapter en `app/scrapers/registry.py`.
7. Agregar store id a `ACTIVE_STORES` si debe estar activa por default.
8. Actualizar `StoreRouter` si la tienda aplica solo a ciertas categorias/marcas.
9. Actualizar los docs relevantes en `docs/context/` con estado, riesgos y estrategia de scraping.
