import { Agent } from "@mastra/core/agent";
import { z } from "zod";

const instructions = `
Eres un sub-agente experto en extracción de datos estructurados.
Tu único objetivo es recibir un JSON crudo (proveniente de resultados de SerpAPI) y extraer la información relevante de los productos.

Reglas obligatorias:
1. Extrae el nombre del producto (title), el precio numérico (price), la URL del producto (link o product_link), la imagen (thumbnail) y la fuente/tienda (source).
2. Si el precio viene con formato de moneda (ej. "$ 1.200,50"), conviértelo a un número puro decimal (ej. 1200.50). Si no hay precio, usa null.
3. Devuelve los resultados estructurados según el esquema solicitado.
4. No agregues texto introductorio, ni conclusiones, ni markdown. Solo el array JSON puro.
5. Un resultado es un producto válido si tiene título y precio. La URL puede ser de Google Shopping (google.com/search?ibp=oshop...) — eso es completamente normal y NO es motivo para descartarlo. Solo descarta entradas sin título o que sean claramente páginas de categorías o blogs (sin precio alguno).
6. Para la URL: usa "product_link" si existe, si no usa "link". Nunca inventes ni construyas URLs.
`;

export const searchEverywhereExtractorAgent = new Agent({
  id: "search-everywhere-extractor-agent",
  name: "Search Everywhere Extractor",
  instructions,
  model: "deepseek/deepseek-v4-flash",
});

// Zod schema for structured output
export const extractorOutputSchema = z.object({
  results: z.array(
    z.object({
      title: z.string(),
      price: z.number().nullable(),
      url: z.string().nullable(),
      imageUrl: z.string().nullable(),
      source: z.string().nullable(),
    })
  )
});
