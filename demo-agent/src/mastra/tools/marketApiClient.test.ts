import { describe, expect, test } from "vitest";
import {
  getMatchingCandidates,
  getPriceHistory,
  searchProducts,
  type FetchLike,
} from "./marketApiClient";

const jsonResponse = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });

describe("searchProducts", () => {
  test("calls /agent/search and returns compact matches", async () => {
    const urls: string[] = [];
    const fetcher: FetchLike = async (url) => {
      urls.push(String(url));
      return jsonResponse({
        query: "iphone 15",
        debug_ref: 82,
        routing: { included_stores: ["mercado_libre"], excluded_stores: [] },
        query_understanding: { brand: "apple", category: "phone" },
        best_matches: [
          {
            store_id: "mercado_libre",
            store_name: "Mercado Libre",
            title: "Apple iPhone 15 128GB",
            normalized_name: "iphone 15 128gb",
            price: 1234,
            currency: "ARS",
            price_ars: 1234,
            price_usd: null,
            product_url: "https://example.com/iphone",
            image_url: null,
            score: 0.91,
            explanation: "Buen match",
            risks: ["Sin garantia visible"],
            trust_signals: { seller_signal: "ok" },
            historical_signal: null,
            semantic_match: null,
            score_breakdown: {},
          },
        ],
        history_status: { status: "available_on_demand", lookup_url: "/agent/search/82/history" },
        warnings: [],
        errors: [],
      });
    };

    const result = await searchProducts(
      { query: "iphone 15", limit: 3, mode: "interactive" },
      { baseUrl: "http://api.test", fetcher },
    );

    expect(urls[0]).toBe("http://api.test/agent/search?query=iphone+15&limit=3&mode=interactive");
    expect(result.debugRef).toBe(82);
    expect(result.bestMatches[0]?.title).toBe("Apple iPhone 15 128GB");
    expect(result.bestMatches[0]?.score).toBe(0.91);
    expect(result.bestMatches[0]?.priceARS).toBe(1234);
    expect(result.bestMatches[0]?.priceUSD).toBeNull();
  });

  test("passes the requested limit through unchanged", async () => {
    const urls: string[] = [];
    const fetcher: FetchLike = async (url) => {
      urls.push(String(url));
      return jsonResponse({
        query: "kindle paperwhite",
        debug_ref: 116,
        best_matches: [],
        warnings: [],
        errors: [],
      });
    };

    await searchProducts(
      { query: "kindle paperwhite", limit: 1, mode: "interactive" },
      { baseUrl: "http://api.test", fetcher },
    );

    expect(urls[0]).toBe(
      "http://api.test/agent/search?query=kindle+paperwhite&limit=1&mode=interactive",
    );
  });

  test("passes minPriceARS and maxPriceARS as query params", async () => {
    const urls: string[] = [];
    const fetcher: FetchLike = async (url) => {
      urls.push(String(url));
      return jsonResponse({ query: "auriculares sony", best_matches: [], warnings: [], errors: [] });
    };

    await searchProducts(
      { query: "auriculares sony", limit: 5, mode: "interactive", minPriceARS: 40000, maxPriceARS: 150000 },
      { baseUrl: "http://api.test", fetcher },
    );

    expect(urls[0]).toContain("min_price_ars=40000");
    expect(urls[0]).toContain("max_price_ars=150000");
  });

  test("aborts slow requests with client timeout", async () => {
    const fetcher: FetchLike = (_url, init) =>
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => {
          reject(new DOMException("Timed out", "TimeoutError"));
        });
      });

    await expect(
      searchProducts(
        { query: "auriculares huawei" },
        { baseUrl: "http://api.test", fetcher, timeoutMs: 1 },
      ),
    ).rejects.toThrow(/timed out/i);
  });
});

describe("getMatchingCandidates", () => {
  test("polls until V5 predictions arrive", async () => {
    let calls = 0;
    const fetcher: FetchLike = async () => {
      calls += 1;
      if (calls === 1) {
        return jsonResponse([
          { id: 1, run_id: 82, model_version: null, model_match_probability: null },
        ]);
      }

      return jsonResponse([
        {
          id: 1,
          run_id: 82,
          query: "iphone 15",
          left_title: "iPhone 15 128GB",
          right_title: "Apple iPhone 15 128 GB",
          model_version: "match-v5",
          model_match_probability: 0.87,
          model_decision: "same",
        },
      ]);
    };

    const result = await getMatchingCandidates(
      { runId: 82, wait: true, limit: 100 },
      {
        baseUrl: "http://api.test",
        fetcher,
        pollAttempts: 3,
        pollIntervalMs: 0,
      },
    );

    expect(calls).toBe(2);
    expect(result.completed).toBe(true);
    expect(result.predictedCount).toBe(1);
    expect(result.candidates[0]?.modelDecision).toBe("same");
  });
});

describe("getPriceHistory", () => {
  test("calls the run history endpoint and maps response fields", async () => {
    const urls: string[] = [];
    const fetcher: FetchLike = async (url) => {
      urls.push(String(url));
      return jsonResponse({
        run_id: 82,
        count: 1,
        items: [
          {
            store_id: "mercado_libre",
            product_url: "https://example.com/iphone",
            canonical_key: "apple iphone 15 128gb",
            normalized_title: "Apple iPhone 15 128GB",
            price: 1234,
            historical_signal: "below_recent_average",
            average_price: 1400,
            price_count: 4,
          },
        ],
        errors: [],
      });
    };

    const result = await getPriceHistory({ runId: 82 }, { baseUrl: "http://api.test", fetcher });

    expect(urls[0]).toBe("http://api.test/agent/search/82/history");
    expect(result.items[0]?.historicalSignal).toBe("below_recent_average");
  });
});
