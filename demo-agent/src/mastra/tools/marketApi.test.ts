import { describe, expect, test, vi, beforeEach } from "vitest";
import { searchProductsTool } from "./marketApi";

describe("searchProductsTool.toModelOutput", () => {
  test("sends serialized text to the model", () => {
    const output = {
      query: "auriculares xiaomi",
      debugRef: 127,
      routing: { selected_store_ids: ["mercado_libre"] },
      queryUnderstanding: null,
      bestMatches: [
        {
          storeId: "mercado_libre",
          storeName: "Mercado Libre",
          title: "Auriculares Xiaomi Redmi Buds",
          normalizedName: "Xiaomi Redmi Buds",
          price: 12345,
          currency: "$",
          priceARS: 12345,
          priceUSD: null,
          productUrl: "https://example.com",
          imageUrl: null,
          score: 80,
          explanation: "Buen match",
          risks: [],
          trustSignals: null,
          historicalSignal: null,
          semanticMatch: null,
        },
      ],
      historyStatus: null,
      warnings: [],
      errors: [],
    };

    const modelOutput = searchProductsTool.toModelOutput?.(output) as { type: string; value: string } | undefined;

    expect(modelOutput?.type).toBe("text");
    expect(typeof modelOutput?.value).toBe("string");
    expect(modelOutput?.value).toMatch(/Auriculares Xiaomi Redmi Buds/);
    expect(String(modelOutput?.value)).not.toBe("[object Object]");
  });

  test("marks missing requested stores as incomplete coverage", () => {
    const output = {
      query: "cargador MacBook Pro M3 fuente poder original",
      debugRef: 177,
      routing: { selected_store_ids: ["mercado_libre", "fravega", "amazon_us"] },
      queryUnderstanding: null,
      bestMatches: [
        {
          storeId: "amazon_us",
          storeName: "Amazon US",
          title: "MacBook charger",
          normalizedName: "Apple Macbook",
          price: 23.99,
          currency: "USD",
          priceARS: null,
          priceUSD: 23.99,
          productUrl: "https://example.com",
          imageUrl: null,
          score: 70,
          explanation: "Amazon result",
          risks: [],
          trustSignals: null,
          historicalSignal: null,
          semanticMatch: null,
        },
      ],
      historyStatus: null,
      warnings: ["Algunas tiendas no pudieron consultarse."],
      errors: [
        {
          store_id: "mercado_libre",
          store_name: "Mercado Libre",
          message: "Timeout consultando tienda.",
        },
      ],
    };

    const modelOutput = searchProductsTool.toModelOutput?.(output) as { type: string; value: string } | undefined;
    const payload = JSON.parse(modelOutput?.value ?? "{}");

    expect(payload.coverage.complete).toBe(false);
    expect(payload.coverage.failedStores).toEqual(["mercado_libre"]);
    expect(payload.coverage.missingRequestedStores).toEqual(["mercado_libre", "fravega"]);
    expect(payload.responseRules.join(" ")).toMatch(/No afirmes resultados ni precios/);
  });

  test("asks for nearby alternatives when matches are empty", async () => {
    const output = {
      query: "smart tv 45 pulgadas",
      debugRef: 201,
      routing: { selected_store_ids: ["mercado_libre", "fravega"] },
      queryUnderstanding: null,
      bestMatches: [],
      historyStatus: null,
      warnings: ["No se encontraron candidatos claros."],
      errors: [],
    };

    const modelOutput = searchProductsTool.toModelOutput?.(output) as { type: string; value: string } | undefined;
    const payload = JSON.parse(modelOutput?.value ?? "{}");

    expect(payload.responseRules.join(" ")).toMatch(/alternativas cercanas/i);
    expect(payload.responseRules.join(" ")).toMatch(/45.*50.*55/);
  });
});

// ---------------------------------------------------------------------------
// Rate-limit blocking
// ---------------------------------------------------------------------------

vi.mock("./marketApiClient", async (importOriginal) => {
  const original = await importOriginal<typeof import("./marketApiClient")>();
  return { ...original, searchProducts: vi.fn() };
});

describe("searchProductsTool rate-limit blocking", () => {
  // Use unique threadId per test group so the module-level Map doesn't bleed between tests
  let threadId: string;

  beforeEach(() => {
    threadId = `test-thread-${Math.random().toString(36).slice(2)}`;
  });

  function makeContext(tid: string) {
    return { threadId: tid, abortSignal: undefined } as Record<string, unknown>;
  }

  function makeSuccessOutput(query: string, storeId: string) {
    return {
      query,
      debugRef: null,
      routing: { selected_store_ids: [storeId] },
      queryUnderstanding: null,
      bestMatches: [],
      historyStatus: null,
      warnings: [],
      errors: [],
    };
  }

  function makeRateLimitOutput(query: string, storeId: string) {
    return {
      query,
      debugRef: null,
      routing: { selected_store_ids: [storeId] },
      queryUnderstanding: null,
      bestMatches: [],
      historyStatus: null,
      warnings: [],
      errors: [{ store_id: storeId, store_name: storeId, message: "429 Too Many Requests" }],
    };
  }

  test("passes through normally when store is not blocked", async () => {
    const { searchProducts } = await import("./marketApiClient");
    vi.mocked(searchProducts).mockResolvedValueOnce(makeSuccessOutput("notebook", "fravega") as never);

    const result = await searchProductsTool.execute?.(
      { query: "notebook", limit: 10, mode: "interactive", stores: "fravega" } as never,
      makeContext(threadId) as never,
    );

    expect(vi.mocked(searchProducts)).toHaveBeenCalledOnce();
    expect((result as { errors: unknown[] }).errors).toHaveLength(0);
  });

  test("registers a store as blocked when API returns a rate-limit error", async () => {
    const { searchProducts } = await import("./marketApiClient");
    vi.mocked(searchProducts).mockResolvedValueOnce(makeRateLimitOutput("notebook", "mercado_libre") as never);

    // First call — API returns 429 in errors array
    const first = await searchProductsTool.execute?.(
      { query: "notebook", limit: 10, mode: "interactive", stores: "mercado_libre" } as never,
      makeContext(threadId) as never,
    );
    expect((first as { errors: { store_id: string }[] }).errors[0]?.store_id).toBe("mercado_libre");

    // Second call — should be short-circuited, API must NOT be called again
    vi.mocked(searchProducts).mockClear();
    const second = await searchProductsTool.execute?.(
      { query: "notebook 2", limit: 10, mode: "interactive", stores: "mercado_libre" } as never,
      makeContext(threadId) as never,
    );

    expect(vi.mocked(searchProducts)).not.toHaveBeenCalled();
    expect((second as { errors: { message: string }[] }).errors[0]?.message).toMatch(/RATE_LIMIT_BLOCKED/);
    expect((second as { warnings: string[] }).warnings[0]).toMatch(/bloqueada/i);
  });

  test("blocking is scoped per thread — different threads are independent", async () => {
    const { searchProducts } = await import("./marketApiClient");
    const threadA = `${threadId}-A`;
    const threadB = `${threadId}-B`;

    // Block fravega on threadA
    vi.mocked(searchProducts).mockResolvedValueOnce(makeRateLimitOutput("tv", "fravega") as never);
    await searchProductsTool.execute?.(
      { query: "tv", limit: 10, mode: "interactive", stores: "fravega" } as never,
      makeContext(threadA) as never,
    );

    // threadB should still be able to query fravega
    vi.mocked(searchProducts).mockClear();
    vi.mocked(searchProducts).mockResolvedValueOnce(makeSuccessOutput("tv", "fravega") as never);
    const result = await searchProductsTool.execute?.(
      { query: "tv", limit: 10, mode: "interactive", stores: "fravega" } as never,
      makeContext(threadB) as never,
    );

    expect(vi.mocked(searchProducts)).toHaveBeenCalledOnce();
    expect((result as { errors: unknown[] }).errors).toHaveLength(0);
  });

  test("registers store as blocked when searchProducts throws a rate-limit error", async () => {
    const { searchProducts } = await import("./marketApiClient");
    vi.mocked(searchProducts).mockRejectedValueOnce(new Error("429 rate limit exceeded"));

    // First call throws
    await searchProductsTool.execute?.(
      { query: "celular", limit: 10, mode: "interactive", stores: "samsung_ar" } as never,
      makeContext(threadId) as never,
    );

    // Second call must be blocked without hitting the API
    vi.mocked(searchProducts).mockClear();
    const second = await searchProductsTool.execute?.(
      { query: "celular 2", limit: 10, mode: "interactive", stores: "samsung_ar" } as never,
      makeContext(threadId) as never,
    );

    expect(vi.mocked(searchProducts)).not.toHaveBeenCalled();
    expect((second as { errors: { message: string }[] }).errors[0]?.message).toMatch(/RATE_LIMIT_BLOCKED/);
  });
});
