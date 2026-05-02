import assert from "node:assert/strict";
import { test } from "node:test";
import { searchProductsTool } from "./marketApi";

test("search-products sends serialized text to the model", () => {
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

  assert.equal(modelOutput?.type, "text");
  assert.equal(typeof modelOutput?.value, "string");
  assert.match(modelOutput?.value ?? "", /Auriculares Xiaomi Redmi Buds/);
  assert.notEqual(String(modelOutput?.value), "[object Object]");
});

test("search-products model output marks missing requested stores as incomplete coverage", () => {
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

  assert.equal(payload.coverage.complete, false);
  assert.deepEqual(payload.coverage.failedStores, ["mercado_libre"]);
  assert.deepEqual(payload.coverage.missingRequestedStores, ["mercado_libre", "fravega"]);
  assert.match(payload.responseRules.join(" "), /No afirmes resultados ni precios/);
});

test("search-products model output asks for nearby alternatives when matches are empty", () => {
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

  assert.match(payload.responseRules.join(" "), /alternativas cercanas/i);
  assert.match(payload.responseRules.join(" "), /45.*50.*55/);
});
