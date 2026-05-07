# TODO / Ideas a Futuro

## Feature: "Search Everywhere" (Extracción Agéntica)

**Objetivo:** Permitir que el agente busque en cualquier tienda de la web o utilice búsquedas genéricas sin estar limitado estrictamente a los scrapers definidos en código (como los de VTEX o Fravega).

### Arquitectura Propuesta

1. **Nueva Tool (`search-everywhere` o `explore-web`)**
   - El agente principal (DeepSeek/MarketShoppingAgent) detecta que el usuario pide un producto de nicho o solicita buscar en una tienda que no está en `ACTIVE_STORES`.
   - Llama a la herramienta enviando el dominio o la query de búsqueda libre.

2. **Backend / Motor de Búsqueda (SerpAPI)**
   - La herramienta se conecta a SerpAPI (Google Shopping u orgánico) para obtener el HTML crudo o el JSON sin procesar de los resultados de búsqueda.

3. **Sub-Agente Extractor**
   - En lugar de parsear el resultado con código duro en Python (lo cual es frágil ante cambios de diseño de la página), la herramienta invoca a un **sub-agente** especializado (ej. `gpt-4o-mini`).
   - El prompt del sub-agente tiene una única tarea estricta: *"Sos un extractor de datos. Tomá este texto/JSON crudo y extraé una lista de productos. Devolveme un JSON estricto."*
   - Se debe utilizar validación fuerte (ej. Zod) para garantizar que el sub-agente devuelva el esquema correcto (título, precio, url, imagen, etc.) y evitar alucinaciones.

4. **Retorno al Agente Principal**
   - El sub-agente devuelve los datos limpios y estructurados a la herramienta.
   - La herramienta se los pasa al agente principal, quien a su vez responde al usuario integrando esos resultados en su tabla comparativa.

### Consideraciones de Experiencia de Usuario (UX)

- **Latencia:** Esta operación en cadena (Agente -> SerpAPI -> Sub-Agente -> Agente) será más lenta que un scrapeo normal (estimado ~7 a 15 segundos).
- **Feedback Visual:** Para manejar las expectativas del usuario y evitar que piense que la aplicación se colgó, se debe agregar un mapeo en `front-agent/src/lib/mastraStream.ts` para esta herramienta.
  - Ejemplo: `if (toolName === "search-everywhere") return "Buscando en toda la web (esto puede tardar más de lo normal)...";`
  - Esto mostrará el spinner con el texto descriptivo mientras los agentes trabajan por detrás.

### Beneficios
- **Escalabilidad infinita:** No hace falta crear ni mantener adaptadores por cada tienda en el mundo.
- **Resiliencia:** Inmune a cambios estéticos del DOM de las tiendas.
- **Separación de responsabilidades:** El agente principal razona y aconseja, mientras el sub-agente solo procesa y extrae información.
