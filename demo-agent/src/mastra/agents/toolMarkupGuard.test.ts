import { describe, expect, test, vi } from "vitest";
import {
  hasToolMarkup,
  toolMarkupGuardProcessor,
} from "./toolMarkupGuard";

describe("hasToolMarkup", () => {
  test("detects Mastra tool-call DSL leaked as text", () => {
    expect(hasToolMarkup('<| | DSML | | tool_calls><| | DSML | | invoke name="searchProducts">')).toBe(true);
    expect(hasToolMarkup('parameter name="query"')).toBe(true);
    expect(hasToolMarkup('[Tool call: search-products(query: "cargador MacBook Pro M3 Pro")]')).toBe(true);
    expect(hasToolMarkup("Te recomiendo esta opcion por precio.")).toBe(false);
  });

  test("detects JSON-ish tool calls leaked as text", () => {
    expect(
      hasToolMarkup('{"name": "search-products", "arguments": {"query": "notebook estudiante"}}'),
    ).toBe(true);
    expect(
      hasToolMarkup('{"name": "searchProducts", "arguments": {"query": "notebook estudiante"}}'),
    ).toBe(true);
  });

  test("does NOT flag meta-narration — only tool-call DSL causes abort", () => {
    // These phrases appear legitimately at the start of final-answer steps.
    // hasToolMarkup must return false so the step processor does not abort them.
    expect(hasToolMarkup("Voy a buscar notebooks en varias tiendas con ese presupuesto.")).toBe(false);
    expect(hasToolMarkup("Ahora armo la respuesta final con los datos disponibles.")).toBe(false);
    expect(hasToolMarkup("Vamos a buscar marcas mas reconocidas para vos.")).toBe(false);
  });
});

describe("processOutputStep", () => {
  const messages = [{ role: "assistant", content: "" }] as never[];

  test("returns messages unchanged for normal text", () => {
    const result = toolMarkupGuardProcessor.processOutputStep?.({
      text: "Te recomiendo esta opcion por precio y garantia.",
      messages,
      state: {},
      toolCalls: undefined,
      abort: vi.fn(),
    } as never);

    expect(result).toBe(messages);
  });

  test("aborts when tool-call DSL leaks into final text", () => {
    const abort = vi.fn((reason?: string, options?: { retry?: boolean }) => {
      throw new Error("abort");
    });

    expect(() =>
      toolMarkupGuardProcessor.processOutputStep?.({
        text: '<| | DSML | | invoke name="searchProducts">',
        messages,
        state: {},
        toolCalls: undefined,
        abort,
      } as never),
    ).toThrow("abort");

    expect(abort).toHaveBeenCalledWith(
      expect.stringMatching(/herramienta como texto visible/i),
      { retry: true },
    );
  });

  test("does NOT abort when step text contains only meta-narration (no DSL)", () => {
    // The model may open its final answer with "Ahora armo la respuesta final…"
    // That phrase is benign — only DSL syntax should trigger an abort here.
    const abort = vi.fn();

    const result = toolMarkupGuardProcessor.processOutputStep?.({
      text: "Ahora armo la respuesta final con los datos disponibles.\n\n| # | Tienda |",
      messages,
      state: {},
      toolCalls: [],
      abort,
    } as never);

    expect(abort).not.toHaveBeenCalled();
    expect(result).toBe(messages);
  });

  test("aborts when text is emitted before tool calls", () => {
    const abort = vi.fn((reason?: string, options?: { retry?: boolean }) => {
      throw new Error("abort");
    });

    expect(() =>
      toolMarkupGuardProcessor.processOutputStep?.({
        text: "Buscame un cargador para MacBook Pro.",
        toolCalls: [{ toolName: "searchProducts", toolCallId: "call-1", args: {} }],
        messages,
        state: {},
        abort,
      } as never),
    ).toThrow("abort");

    expect(abort).toHaveBeenCalledWith(
      expect.stringMatching(/texto visible antes de llamar/i),
      { retry: true },
    );
  });

  test("does not abort when hadSilentDrop is set and there are tool calls", () => {
    const abort = vi.fn();
    const state = { hadSilentDrop: true };

    const result = toolMarkupGuardProcessor.processOutputStep?.({
      text: "",
      toolCalls: [{ toolName: "searchProducts", toolCallId: "call-1", args: {} }],
      messages,
      state,
      abort,
    } as never);

    expect(abort).not.toHaveBeenCalled();
    expect(result).toBe(messages);
    expect(state.hadSilentDrop).toBe(false);
  });

  test("still aborts when hadSilentDrop is set but DSL syntax leaked as text", () => {
    const abort = vi.fn((reason?: string, options?: { retry?: boolean }) => {
      throw new Error("abort");
    });
    const state = { hadSilentDrop: true };

    expect(() =>
      toolMarkupGuardProcessor.processOutputStep?.({
        text: '<| | DSML | | invoke name="searchProducts">',
        toolCalls: [],
        messages,
        state,
        abort,
      } as never),
    ).toThrow("abort");
  });
});

describe("processOutputStream", () => {
  test("passes through non-text-delta events and resets accumulation state", async () => {
    const state: Record<string, unknown> = { streamText: "accumulated", dropText: true };

    const result = await toolMarkupGuardProcessor.processOutputStream?.({
      part: { type: "tool-call", payload: {} },
      state,
      retryCount: 0,
      writer: undefined,
    } as never);

    expect(result).toEqual({ type: "tool-call", payload: {} });
    expect(state.streamText).toBe("");
    expect(state.dropText).toBe(false);
  });

  test("passes through safe text chunks", async () => {
    const state: Record<string, unknown> = {};

    const part = { type: "text-delta", payload: { text: "Te recomiendo esta opcion." } };
    const result = await toolMarkupGuardProcessor.processOutputStream?.({
      part,
      state,
      retryCount: 0,
      writer: undefined,
    } as never);

    expect(result).toBe(part);
    expect(state.streamText).toBe("Te recomiendo esta opcion.");
    expect(state.dropText).toBeFalsy();
  });

  test("drops a chunk silently when unsafe pattern is detected in accumulated text", async () => {
    const state: Record<string, unknown> = { streamText: "Vamos ", streamRetry: 0 };
    const writer = { custom: vi.fn().mockResolvedValue(undefined) };

    const result = await toolMarkupGuardProcessor.processOutputStream?.({
      part: { type: "text-delta", payload: { text: "a buscar marcas." } },
      state,
      retryCount: 0,
      writer,
    } as never);

    expect(result).toBeNull();
    expect(state.dropText).toBe(true);
    expect(state.hadSilentDrop).toBe(true);
    expect(writer.custom).toHaveBeenCalledWith({ type: "data-content-reset", data: null });
  });

  test("continues dropping chunks after unsafe pattern detected", async () => {
    const state: Record<string, unknown> = { dropText: true, streamRetry: 0, streamText: "already dropped text" };
    const writer = { custom: vi.fn() };

    const result = await toolMarkupGuardProcessor.processOutputStream?.({
      part: { type: "text-delta", payload: { text: "additional text" } },
      state,
      retryCount: 0,
      writer,
    } as never);

    expect(result).toBeNull();
    expect(writer.custom).not.toHaveBeenCalled();
  });

  test("resumes streaming after paragraph break following narration drop", async () => {
    // Simulates: narration was dropped, then \n\n arrives — resume for next chunk.
    const state: Record<string, unknown> = {
      dropText: true,
      streamRetry: 0,
      streamText: "Ahora armo la respuesta final con los datos.",
    };
    const writer = { custom: vi.fn() };

    // Chunk that completes the paragraph break
    const result = await toolMarkupGuardProcessor.processOutputStream?.({
      part: { type: "text-delta", payload: { text: "\n\n" } },
      state,
      retryCount: 0,
      writer,
    } as never);

    // Current chunk still dropped (the \n\n itself is not emitted)
    expect(result).toBeNull();
    // But dropText is now reset so the NEXT chunk will stream
    expect(state.dropText).toBe(false);
    expect(state.streamText).toBe("");
    // No extra content-reset sent for the resume
    expect(writer.custom).not.toHaveBeenCalled();
  });

  test("resets accumulation state on new retry attempt", async () => {
    const state: Record<string, unknown> = {
      streamText: "Vamos a buscar",
      streamRetry: 0,
      dropText: true,
    };

    const part = { type: "text-delta", payload: { text: "Aqui estan los resultados." } };
    const result = await toolMarkupGuardProcessor.processOutputStream?.({
      part,
      state,
      retryCount: 1,
      writer: undefined,
    } as never);

    expect(result).toBe(part);
    expect(state.streamRetry).toBe(1);
    expect(state.dropText).toBe(false);
    expect(state.streamText).toBe("Aqui estan los resultados.");
  });

  test("detects multi-chunk pattern split across boundaries", async () => {
    const state: Record<string, unknown> = {};
    const writer = { custom: vi.fn().mockResolvedValue(undefined) };

    // First chunk: "Voy "
    await toolMarkupGuardProcessor.processOutputStream?.({
      part: { type: "text-delta", payload: { text: "Voy " } },
      state,
      retryCount: 0,
      writer,
    } as never);

    // Second chunk: "a " — still not complete
    await toolMarkupGuardProcessor.processOutputStream?.({
      part: { type: "text-delta", payload: { text: "a " } },
      state,
      retryCount: 0,
      writer,
    } as never);

    // Third chunk: "buscar" — now "Voy a buscar" is in accumulated text
    const result = await toolMarkupGuardProcessor.processOutputStream?.({
      part: { type: "text-delta", payload: { text: "buscar" } },
      state,
      retryCount: 0,
      writer,
    } as never);

    expect(result).toBeNull();
    expect(state.hadSilentDrop).toBe(true);
    expect(writer.custom).toHaveBeenCalledTimes(1);
  });
});
