import { setTimeout as sleep } from "node:timers/promises";

export type SearchMode = "interactive" | "deep";

export type FetchLike = (
  input: string | URL,
  init?: RequestInit,
) => Promise<Response>;

type ClientOptions = {
  baseUrl?: string;
  fetcher?: FetchLike;
  abortSignal?: AbortSignal;
  timeoutMs?: number;
  pollAttempts?: number;
  pollIntervalMs?: number;
};

type SearchProductsInput = {
  query: string;
  limit?: number;
  mode?: SearchMode;
  stores?: string;
  maxPriceARS?: number;
  minPriceARS?: number;
};

type MatchingCandidatesInput = {
  runId: number;
  limit?: number;
  wait?: boolean;
};

type PriceHistoryInput = {
  runId: number;
};

type AnalyzeUrlInput = {
  url: string;
};

export type ProductAnalysis = {
  url: string;
  store: string | null;
  title: string | null;
  price: number | null;
  currency: string | null;
  originalPrice: number | null;
  discount: string | null;
  brand: string | null;
  description: string | null;
  condition: string | null;
  availability: string | null;
  installments: string | null;
  imageUrl: string | null;
  error: string | null;
};

export type CompactMatch = {
  storeId: string;
  storeName: string;
  title: string;
  normalizedName: string | null;
  price: number | null;
  currency: string | null;
  priceARS: number | null;
  priceUSD: number | null;
  productUrl: string | null;
  imageUrl: string | null;
  score: number | null;
  explanation: string | null;
  risks: string[];
  trustSignals: unknown;
  historicalSignal: string | null;
  semanticMatch: unknown;
};

export type SearchProductsOutput = {
  query: string;
  debugRef: number | null;
  routing: unknown;
  queryUnderstanding: unknown;
  bestMatches: CompactMatch[];
  historyStatus: unknown;
  warnings: string[];
  errors: unknown[];
};

export type CompactCandidate = {
  id: number;
  runId: number;
  query: string | null;
  left: CompactCandidateSide;
  right: CompactCandidateSide;
  matchConfidence: number | null;
  label: string | null;
  modelMatchProbability: number | null;
  modelDecision: string | null;
  modelVersion: string | null;
};

type CompactCandidateSide = {
  storeId: string | null;
  title: string | null;
  productUrl: string | null;
  canonicalKey: string | null;
  price: number | null;
};

export type MatchingCandidatesOutput = {
  runId: number;
  completed: boolean;
  totalCount: number;
  predictedCount: number;
  candidates: CompactCandidate[];
};

export type PriceHistoryOutput = {
  runId: number;
  count: number;
  items: Array<{
    storeId: string | null;
    productUrl: string | null;
    canonicalKey: string | null;
    normalizedTitle: string | null;
    price: number | null;
    historicalSignal: string | null;
    averagePrice: number | null;
    priceCount: number | null;
  }>;
  errors: unknown[];
};

export async function searchProducts(
  input: SearchProductsInput,
  options: ClientOptions = {},
): Promise<SearchProductsOutput> {
  const url = buildUrl(options.baseUrl, "/agent/search", {
    query: input.query,
    limit: String(input.limit ?? 5),
    mode: input.mode ?? defaultSearchMode(),
    ...(input.stores ? { stores: input.stores } : {}),
    ...(input.maxPriceARS != null ? { max_price_ars: String(input.maxPriceARS) } : {}),
    ...(input.minPriceARS != null ? { min_price_ars: String(input.minPriceARS) } : {}),
  });
  const data = await fetchJson<Record<string, unknown>>(url, options);

  return {
    query: String(data.query ?? input.query),
    debugRef: numberOrNull(data.debug_ref),
    routing: data.routing ?? null,
    queryUnderstanding: data.query_understanding ?? null,
    bestMatches: asArray(data.best_matches).map(compactMatch),
    historyStatus: data.history_status ?? null,
    warnings: asStringArray(data.warnings),
    errors: asArray(data.errors),
  };
}


export async function getMatchingCandidates(
  input: MatchingCandidatesInput,
  options: ClientOptions = {},
): Promise<MatchingCandidatesOutput> {
  const pollAttempts = Math.max(1, options.pollAttempts ?? envNumber("MATCHING_POLL_ATTEMPTS", 5));
  const pollIntervalMs = Math.max(0, options.pollIntervalMs ?? envNumber("MATCHING_POLL_INTERVAL_MS", 1000));
  const wait = input.wait ?? true;

  let candidates: CompactCandidate[] = [];
  for (let attempt = 1; attempt <= (wait ? pollAttempts : 1); attempt += 1) {
    const url = buildUrl(options.baseUrl, "/internal/matching/candidates", {
      run_id: String(input.runId),
      status: "all",
      limit: String(input.limit ?? 100),
    });
    const data = await fetchJson<unknown[]>(url, options);
    candidates = asArray(data).map(compactCandidate);

    if (!wait || candidates.length === 0 || candidates.some((candidate) => candidate.modelVersion)) {
      break;
    }

    if (attempt < pollAttempts) {
      await sleep(pollIntervalMs, undefined, { signal: options.abortSignal });
    }
  }

  const predictedCount = candidates.filter((candidate) => candidate.modelVersion).length;
  return {
    runId: input.runId,
    completed: candidates.length === 0 || predictedCount > 0,
    totalCount: candidates.length,
    predictedCount,
    candidates,
  };
}

export async function getPriceHistory(
  input: PriceHistoryInput,
  options: ClientOptions = {},
): Promise<PriceHistoryOutput> {
  const url = buildUrl(options.baseUrl, `/agent/search/${input.runId}/history`);
  const data = await fetchJson<Record<string, unknown>>(url, options);

  return {
    runId: numberOrNull(data.run_id) ?? input.runId,
    count: numberOrNull(data.count) ?? asArray(data.items).length,
    items: asArray(data.items).map((item) => ({
      storeId: stringOrNull(item.store_id),
      productUrl: stringOrNull(item.product_url),
      canonicalKey: stringOrNull(item.canonical_key),
      normalizedTitle: stringOrNull(item.normalized_title),
      price: numberOrNull(item.price),
      historicalSignal: stringOrNull(item.historical_signal),
      averagePrice: numberOrNull(item.average_price),
      priceCount: numberOrNull(item.price_count),
    })),
    errors: asArray(data.errors),
  };
}

export async function analyzeProductUrl(
  input: AnalyzeUrlInput,
  options: ClientOptions = {},
): Promise<ProductAnalysis> {
  const url = buildUrl(options.baseUrl, "/agent/analyze-url", { url: input.url });
  const data = await fetchJson<Record<string, unknown>>(url, options);

  return {
    url: String(data.url ?? input.url),
    store: stringOrNull(data.store),
    title: stringOrNull(data.title),
    price: numberOrNull(data.price),
    currency: stringOrNull(data.currency),
    originalPrice: numberOrNull(data.original_price),
    discount: stringOrNull(data.discount),
    brand: stringOrNull(data.brand),
    description: stringOrNull(data.description),
    condition: stringOrNull(data.condition),
    availability: stringOrNull(data.availability),
    installments: stringOrNull(data.installments),
    imageUrl: stringOrNull(data.image_url),
    error: stringOrNull(data.error),
  };
}

export type SearchEverywhereInput = {
  query: string;
  url?: string;
  limit?: number;
  maxPriceARS?: number;
  minPriceARS?: number;
  strict?: boolean;
};

export type SearchEverywhereRawOutput = {
  shopping_results: Record<string, unknown>[];
  error: string | null;
};

export async function searchEverywhere(
  input: SearchEverywhereInput,
  options: ClientOptions = {},
): Promise<SearchEverywhereRawOutput> {
  const url = buildUrl(options.baseUrl, "/agent/search-everywhere", {
    query: input.query,
    ...(input.url ? { url: input.url } : {}),
    ...(input.limit != null ? { limit: String(input.limit) } : {}),
    ...(input.maxPriceARS != null ? { max_price_ars: String(input.maxPriceARS) } : {}),
    ...(input.minPriceARS != null ? { min_price_ars: String(input.minPriceARS) } : {}),
    ...(input.strict != null ? { strict: String(input.strict) } : {}),
  });
  const data = await fetchJson<Record<string, unknown>>(url, options);

  return {
    shopping_results: asArray(data.shopping_results),
    error: stringOrNull(data.error),
  };
}

function buildUrl(
  baseUrl = process.env.MARKET_API_BASE_URL ?? "http://127.0.0.1:8000",
  path: string,
  query?: Record<string, string>,
): string {
  const url = new URL(path, baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`);
  for (const [key, value] of Object.entries(query ?? {})) {
    url.searchParams.set(key, value);
  }
  return url.toString();
}

async function fetchJson<T>(url: string, options: ClientOptions): Promise<T> {
  const fetcher = options.fetcher ?? fetch;
  const timeoutMs = Math.max(1, options.timeoutMs ?? envNumber("MARKET_API_TIMEOUT_MS", 40000));
  const timeoutSignal = AbortSignal.timeout(timeoutMs);
  const signal = options.abortSignal
    ? AbortSignal.any([options.abortSignal, timeoutSignal])
    : timeoutSignal;

  let response: Response;
  try {
    response = await fetcher(url, {
      headers: { accept: "application/json" },
      signal,
    });
  } catch (error) {
    if (isAbortLikeError(error)) {
      throw new Error(`Market API request aborted or timed out after ${timeoutMs}ms: ${url}`);
    }
    throw error;
  }

  if (!response.ok) {
    const body = await response.text().catch(() => "");
    throw new Error(`Market API request failed: ${response.status} ${response.statusText} ${body}`.trim());
  }

  return (await response.json()) as T;
}

function isAbortLikeError(error: unknown): boolean {
  return (
    error instanceof Error &&
    (error.name === "AbortError" || error.name === "TimeoutError")
  );
}

function compactMatch(value: Record<string, unknown>): CompactMatch {
  return {
    storeId: String(value.store_id ?? ""),
    storeName: String(value.store_name ?? ""),
    title: String(value.title ?? ""),
    normalizedName: stringOrNull(value.normalized_name),
    price: numberOrNull(value.price),
    currency: stringOrNull(value.currency),
    priceARS: numberOrNull(value.price_ars),
    priceUSD: numberOrNull(value.price_usd),
    productUrl: stringOrNull(value.product_url),
    imageUrl: stringOrNull(value.image_url),
    score: numberOrNull(value.score),
    explanation: stringOrNull(value.explanation),
    risks: asStringArray(value.risks),
    trustSignals: value.trust_signals ?? null,
    historicalSignal: stringOrNull(value.historical_signal),
    semanticMatch: value.semantic_match ?? null,
  };
}

function compactCandidate(value: Record<string, unknown>): CompactCandidate {
  return {
    id: numberOrNull(value.id) ?? 0,
    runId: numberOrNull(value.run_id) ?? 0,
    query: stringOrNull(value.query),
    left: {
      storeId: stringOrNull(value.left_store_id),
      title: stringOrNull(value.left_title),
      productUrl: stringOrNull(value.left_product_url),
      canonicalKey: stringOrNull(value.left_canonical_key),
      price: numberOrNull(value.left_price),
    },
    right: {
      storeId: stringOrNull(value.right_store_id),
      title: stringOrNull(value.right_title),
      productUrl: stringOrNull(value.right_product_url),
      canonicalKey: stringOrNull(value.right_canonical_key),
      price: numberOrNull(value.right_price),
    },
    matchConfidence: numberOrNull(value.match_confidence),
    label: stringOrNull(value.label),
    modelMatchProbability: numberOrNull(value.model_match_probability),
    modelDecision: stringOrNull(value.model_decision),
    modelVersion: stringOrNull(value.model_version),
  };
}

function defaultSearchMode(): SearchMode {
  return process.env.DEFAULT_SEARCH_MODE === "deep" ? "deep" : "interactive";
}

function envNumber(name: string, fallback: number): number {
  const parsed = Number(process.env[name]);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function asArray(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? (value as Record<string, unknown>[]) : [];
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : [];
}

function stringOrNull(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
