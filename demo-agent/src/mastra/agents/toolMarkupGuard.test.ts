import assert from "node:assert/strict";
import { test } from "node:test";
import {
  hasToolMarkup,
  toolMarkupGuardProcessor,
} from "./toolMarkupGuard";

test("tool markup guard detects Mastra tool-call DSL leaked as text", () => {
  assert.equal(
    hasToolMarkup(
      '<| | DSML | | tool_calls><| | DSML | | invoke name="searchProducts">',
    ),
    true,
  );
  assert.equal(hasToolMarkup('parameter name="query"'), true);
  assert.equal(
    hasToolMarkup(
      '[Tool call: search-products(query: "cargador MacBook Pro M3 Pro")]',
    ),
    true,
  );
  assert.equal(hasToolMarkup("Te recomiendo esta opcion por precio."), false);
});

test("tool markup guard retries when tool-call DSL leaks into final text", () => {
  assert.throws(
    () =>
      toolMarkupGuardProcessor.processOutputStep?.({
        text: '<| | DSML | | invoke name="searchProducts">',
        abort: (reason?: string, options?: any) => {
          assert.match(String(reason), /herramienta como texto visible/i);
          assert.equal(options?.retry, true);
          throw new Error("retry requested");
        },
      } as never),
    /retry requested/,
  );
});

test("tool markup guard leaves normal text untouched", () => {
  const result = toolMarkupGuardProcessor.processOutputStep?.({
    text: "Te recomiendo esta opcion por precio y garantia.",
  } as never);

  assert.equal(result, undefined);
});

test("tool markup guard retries when text is emitted before a tool call", () => {
  assert.throws(
    () =>
      toolMarkupGuardProcessor.processOutputStep?.({
        text: "Buscame un cargador para MacBook Pro.",
        toolCalls: [
          {
            toolName: "searchProducts",
            toolCallId: "call-1",
            args: {},
          },
        ],
        abort: (reason?: string, options?: any) => {
          assert.match(String(reason), /texto visible antes de llamar/i);
          assert.equal(options?.retry, true);
          throw new Error("retry requested");
        },
      } as never),
    /retry requested/,
  );
});

test("tool markup guard aborts streamed pseudo tool calls before emitting them", async () => {
  await assert.rejects(
    async () =>
      await toolMarkupGuardProcessor.processOutputStream?.({
        part: {
          type: "text-delta",
          payload: {
            id: "text-1",
            text: '[Tool call: search-products(query: "cargador")]',
          },
        },
        abort: (reason?: string, options?: any) => {
          assert.match(String(reason), /llamada a herramienta como texto/i);
          assert.equal(options?.retry, true);
          throw new Error("retry requested");
        },
      } as never),
    /retry requested/,
  );
});
