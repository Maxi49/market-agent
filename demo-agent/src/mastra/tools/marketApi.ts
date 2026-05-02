import { createTool } from "@mastra/core/tools";
import { z } from "zod";
import {
  analyzeProductUrl,
  getMatchingCandidates,
  getPriceHistory,
  searchProducts,
  type MatchingCandidatesOutput,
  type PriceHistoryOutput,
  type ProductAnalysis,
  type SearchProductsOutput,
} from "./marketApiClient";

const compactMatchSchema = z.object({
  storeId: z.string(),
  storeName: z.string(),
  title: z.string(),
  normalizedName: z.string().nullable(),
  price: z.number().nullable(),
  currency: z.string().nullable(),
  priceARS: z.number().nullable(),
  priceUSD: z.number().nullable(),
  productUrl: z.string().nullable(),
  imageUrl: z.string().nullable(),
  score: z.number().nullable(),
  explanation: z.string().nullable(),
  risks: z.array(z.string()),
  trustSignals: z.unknown(),
  historicalSignal: z.string().nullable(),
  semanticMatch: z.unknown(),
});

const candidateSideSchema = z.object({
  storeId: z.string().nullable(),
  title: z.string().nullable(),
  productUrl: z.string().nullable(),
  canonicalKey: z.string().nullable(),
  price: z.number().nullable(),
});

const compactCandidateSchema = z.object({
  id: z.number(),
  runId: z.number(),
  query: z.string().nullable(),
  left: candidateSideSchema,
  right: candidateSideSchema,
  matchConfidence: z.number().nullable(),
  label: z.string().nullable(),
  modelMatchProbability: z.number().nullable(),
  modelDecision: z.string().nullable(),
  modelVersion: z.string().nullable(),
});

export const searchProductsTool = createTool({
  id: "search-products",
  description:
    "Busca productos actuales en una tienda usando la Market API. Usala siempre que el usuario pida precios, opciones, comparaciones o recomendaciones de compra. Para multiples tiendas, hacer una llamada separada por tienda en paralelo. No repetir la misma combinacion query + stores + mode + maxPriceARS; espera los outputs antes de decidir una segunda tanda con alternativas. Tiendas disponibles: mercado_libre, fravega, carrefour_ar, cetrogar_ar, easy_ar, samsung_ar, sony_ar, bgh_ar, amazon_us. Si no se especifican tiendas, el pipeline elige automaticamente.",
  inputSchema: z.object({
    query: z.string().min(2).max(120),
    limit: z.number().int().min(1).max(20).default(10),
    mode: z.enum(["interactive", "deep"]).default("interactive"),
    stores: z.string().optional().describe("ID de una sola tienda donde buscar (ej: 'fravega'). Para varias tiendas, una llamada por tienda en paralelo. No repetir la misma query para la misma tienda."),
    maxPriceARS: z.number().positive().optional().describe("Presupuesto maximo en pesos argentinos. Filtra resultados de tiendas locales que superen ese precio."),
  }),
  outputSchema: z.object({
    query: z.string(),
    debugRef: z.number().nullable(),
    routing: z.unknown(),
    queryUnderstanding: z.unknown(),
    bestMatches: z.array(compactMatchSchema),
    historyStatus: z.unknown(),
    warnings: z.array(z.string()),
    errors: z.array(z.unknown()),
  }),
  execute: async (input, context) => {
    try {
      return await searchProducts(input, { abortSignal: context.abortSignal });
    } catch (error) {
      return emptySearchOutput(input.query, error);
    }
  },
  toModelOutput: (output) => ({
    type: "text",
    value: toModelText(compactSearchModelOutput(output)),
  }),
});

export const getMatchingCandidatesTool = createTool({
  id: "get-matching-candidates",
  description:
    "Consulta candidatos de matching V5 por run/debugRef. Usala despues de search-products cuando necesites saber si listings de distintas tiendas son el mismo producto o variantes.",
  inputSchema: z.object({
    runId: z.number().int().positive(),
    limit: z.number().int().min(1).max(200).default(100),
    wait: z.boolean().default(true),
  }),
  outputSchema: z.object({
    runId: z.number(),
    completed: z.boolean(),
    totalCount: z.number(),
    predictedCount: z.number(),
    candidates: z.array(compactCandidateSchema),
  }),
  execute: async (input, context) => {
    try {
      return await getMatchingCandidates(input, { abortSignal: context.abortSignal });
    } catch {
      return emptyMatchingCandidatesOutput(input.runId);
    }
  },
  toModelOutput: (output) => ({
    type: "text",
    value: toModelText(compactCandidatesModelOutput(output)),
  }),
});

export const getPriceHistoryTool = createTool({
  id: "get-price-history",
  description:
    "Obtiene senales historicas de precio para un run/debugRef. Usala cuando el usuario pregunte si un precio conviene o cuando el historico mejore una recomendacion.",
  inputSchema: z.object({
    runId: z.number().int().positive(),
  }),
  outputSchema: z.object({
    runId: z.number(),
    count: z.number(),
    items: z.array(
      z.object({
        storeId: z.string().nullable(),
        productUrl: z.string().nullable(),
        canonicalKey: z.string().nullable(),
        normalizedTitle: z.string().nullable(),
        price: z.number().nullable(),
        historicalSignal: z.string().nullable(),
        averagePrice: z.number().nullable(),
        priceCount: z.number().nullable(),
      }),
    ),
    errors: z.array(z.unknown()),
  }),
  execute: async (input, context) => {
    try {
      return await getPriceHistory(input, { abortSignal: context.abortSignal });
    } catch (error) {
      return emptyPriceHistoryOutput(input.runId, error);
    }
  },
  toModelOutput: (output) => ({
    type: "text",
    value: toModelText(compactHistoryModelOutput(output)),
  }),
});

export const analyzeProductUrlTool = createTool({
  id: "analyze-product-url",
  description:
    "Analiza una URL de producto de cualquier tienda y extrae titulo, precio, marca, condicion, disponibilidad, cuotas y descuento. Usala cuando el usuario pase un link directo a un producto.",
  inputSchema: z.object({
    url: z.string().url().describe("URL completa del producto a analizar"),
  }),
  outputSchema: z.object({
    url: z.string(),
    store: z.string().nullable(),
    title: z.string().nullable(),
    price: z.number().nullable(),
    currency: z.string().nullable(),
    originalPrice: z.number().nullable(),
    discount: z.string().nullable(),
    brand: z.string().nullable(),
    description: z.string().nullable(),
    condition: z.string().nullable(),
    availability: z.string().nullable(),
    installments: z.string().nullable(),
    imageUrl: z.string().nullable(),
    error: z.string().nullable(),
  }),
  execute: async (input, context) => {
    try {
      return await analyzeProductUrl(input, { abortSignal: context.abortSignal });
    } catch (error) {
      return {
        url: input.url,
        store: null,
        title: null,
        price: null,
        currency: null,
        originalPrice: null,
        discount: null,
        brand: null,
        description: null,
        condition: null,
        availability: null,
        installments: null,
        imageUrl: null,
        error: errorMessage(error),
      };
    }
  },
  toModelOutput: (output) => ({
    type: "text",
    value: toModelText(compactUrlAnalysisModelOutput(output)),
  }),
});

function toModelText(value: unknown): string {
  return JSON.stringify(value);
}

function compactSearchModelOutput(output: SearchProductsOutput) {
  const coverage = searchCoverage(output);
  return {
    query: output.query,
    debugRef: output.debugRef,
    coverage,
    responseRules: responseRulesForCoverage(coverage),
    bestMatches: output.bestMatches.map((match) => ({
      storeId: match.storeId,
      storeName: match.storeName,
      title: match.title,
      normalizedName: match.normalizedName,
      price: match.price,
      currency: match.currency,
      priceARS: match.priceARS,
      priceUSD: match.priceUSD,
      productUrl: match.productUrl,
      score: match.score,
      explanation: match.explanation,
      risks: match.risks,
    })),
    warnings: output.warnings,
    errors: output.errors,
  };
}

type SearchCoverage = {
  requestedStores: string[];
  storesWithMatches: string[];
  failedStores: string[];
  missingRequestedStores: string[];
  complete: boolean;
};

function searchCoverage(output: SearchProductsOutput): SearchCoverage {
  const requestedStores = selectedStoreIds(output.routing);
  const storesWithMatches = unique(output.bestMatches.map((match) => match.storeId).filter(Boolean));
  const failedStores = unique(output.errors.map(errorStoreId).filter(Boolean));
  const missingRequestedStores = requestedStores.filter(
    (storeId) => !storesWithMatches.includes(storeId) || failedStores.includes(storeId),
  );

  return {
    requestedStores,
    storesWithMatches,
    failedStores,
    missingRequestedStores,
    complete: missingRequestedStores.length === 0,
  };
}

function responseRulesForCoverage(coverage: SearchCoverage): string[] {
  const rules = [
    "No inventes precios, rangos, stock ni links que no esten en bestMatches.",
  ];

  if (coverage.storesWithMatches.length === 0) {
    rules.push(
      "bestMatches esta vacio. Si queda un paso disponible, llama search-products con alternativas cercanas razonables antes de responder.",
    );
    rules.push(
      "Para atributos agotados o poco comunes, relaja el atributo mas restrictivo y conserva la intencion: por ejemplo TV 45 pulgadas -> buscar 43, 50 o 55 pulgadas.",
    );
  }

  if (!coverage.complete) {
    rules.push(
      `La busqueda esta incompleta. No afirmes resultados ni precios de estas tiendas sin datos: ${coverage.missingRequestedStores.join(", ")}.`,
    );
    rules.push("Si una tienda critica fallo, decilo explicitamente y recomenda solo con los resultados reales disponibles.");
  }

  return rules;
}

function selectedStoreIds(routing: unknown): string[] {
  if (!routing || typeof routing !== "object") {
    return [];
  }
  const value = (routing as { selected_store_ids?: unknown }).selected_store_ids;
  return Array.isArray(value) ? unique(value.map(String).filter(Boolean)) : [];
}

function errorStoreId(error: unknown): string {
  if (!error || typeof error !== "object") {
    return "";
  }
  return String((error as { store_id?: unknown }).store_id ?? "");
}

function unique(values: string[]): string[] {
  return [...new Set(values)];
}

function compactCandidatesModelOutput(output: MatchingCandidatesOutput) {
  return {
    runId: output.runId,
    completed: output.completed,
    totalCount: output.totalCount,
    predictedCount: output.predictedCount,
    candidates: output.candidates.slice(0, 20).map((candidate) => ({
      left: candidate.left,
      right: candidate.right,
      modelMatchProbability: candidate.modelMatchProbability,
      modelDecision: candidate.modelDecision,
      modelVersion: candidate.modelVersion,
    })),
  };
}

function compactHistoryModelOutput(output: PriceHistoryOutput) {
  return {
    runId: output.runId,
    count: output.count,
    items: output.items.slice(0, 10),
    errors: output.errors,
  };
}

function emptySearchOutput(query: string, error: unknown): SearchProductsOutput {
  return {
    query,
    debugRef: null,
    routing: null,
    queryUnderstanding: null,
    bestMatches: [],
    historyStatus: null,
    warnings: [],
    errors: [{ message: errorMessage(error) }],
  };
}

function emptyMatchingCandidatesOutput(runId: number): MatchingCandidatesOutput {
  return {
    runId,
    completed: false,
    totalCount: 0,
    predictedCount: 0,
    candidates: [],
  };
}

function emptyPriceHistoryOutput(runId: number, error: unknown): PriceHistoryOutput {
  return {
    runId,
    count: 0,
    items: [],
    errors: [{ message: errorMessage(error) }],
  };
}

function compactUrlAnalysisModelOutput(output: ProductAnalysis) {
  return {
    store: output.store,
    title: output.title,
    price: output.price,
    currency: output.currency,
    originalPrice: output.originalPrice,
    discount: output.discount,
    brand: output.brand,
    condition: output.condition,
    availability: output.availability,
    installments: output.installments,
    error: output.error,
  };
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
