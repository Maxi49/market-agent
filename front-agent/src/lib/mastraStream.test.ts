import { describe, expect, it } from "vitest";
import {
  compactTraceSteps,
  appendTextDelta,
  hasUnsafeAgentOutput,
  mergeTraceStep,
  parseSseChunk,
  toTraceLabel,
  traceStepFromEvent,
} from "./mastraStream";

describe("parseSseChunk", () => {
  it("keeps partial SSE events buffered until the next chunk", () => {
    const first = parseSseChunk('data: {"type":"text-delta","textDelta":"Ho', "");
    expect(first.events).toEqual([]);
    expect(first.remainder).toBe('data: {"type":"text-delta","textDelta":"Ho');

    const second = parseSseChunk('la"}\n\n', first.remainder);
    expect(second.remainder).toBe("");
    expect(second.events).toEqual([{ type: "text-delta", textDelta: "Hola" }]);
  });

  it("parses multiple data lines and ignores non-json stream markers", () => {
    const result = parseSseChunk(
      'event: message\ndata: {"type":"tool-call","toolName":"searchProducts"}\n\ndata: [DONE]\n\ndata: {"type":"finish","finishReason":"stop"}\n\n',
      "",
    );

    expect(result.remainder).toBe("");
    expect(result.events).toEqual([
      { type: "tool-call", toolName: "searchProducts" },
      { type: "finish", finishReason: "stop" },
    ]);
  });
});

describe("toTraceLabel", () => {
  it("maps Mastra tool chunks to compact Spanish labels", () => {
    expect(toTraceLabel({ type: "tool-call", toolName: "searchProducts" })).toBe(
      "Buscando productos",
    );
    expect(
      toTraceLabel({ type: "tool-call", payload: { toolName: "searchProducts" } }),
    ).toBe("Buscando productos");
    expect(toTraceLabel({ type: "tool-result", toolName: "getPriceHistory" })).toBe(
      "Historial listo",
    );
    expect(toTraceLabel({ type: "finish-step" })).toBe("Paso completado");
  });
});

describe("traceStepFromEvent", () => {
  it("treats Mastra tool input streaming events as the active tool", () => {
    expect(
      traceStepFromEvent(
        { type: "tool-call-input-streaming-start", payload: { toolName: "searchProducts" } },
        1,
      ),
    ).toMatchObject({
      label: "Buscando productos",
      status: "running",
      detail: "searchProducts",
    });
  });
});

describe("mergeTraceStep", () => {
  it("closes the previous running step when a different tool starts", () => {
    const traces = [
      { id: "searchProducts", label: "Buscando productos", status: "running" as const, detail: "searchProducts" },
    ];

    const merged = mergeTraceStep(traces, {
      id: "think",
      label: "Razonamiento interno",
      status: "running",
      detail: "think",
    });

    expect(merged).toEqual([
      { id: "searchProducts", label: "Buscando productos", status: "done", detail: "searchProducts" },
      { id: "think", label: "Razonamiento interno", status: "running", detail: "think" },
    ]);
  });

  it("updates a running tool to done when its result arrives", () => {
    const traces = [
      { id: "searchProducts", label: "Buscando productos", status: "running" as const, detail: "searchProducts" },
    ];

    const merged = mergeTraceStep(traces, {
      id: "searchProducts-result",
      label: "Busqueda lista",
      status: "done",
      detail: "searchProducts",
    });

    expect(merged).toEqual([
      { id: "searchProducts", label: "Busqueda lista", status: "done", detail: "searchProducts" },
    ]);
  });
});

describe("compactTraceSteps", () => {
  it("deduplicates noisy repeated trace rows and keeps recent activity", () => {
    const traces = compactTraceSteps([
      { id: "1", label: "Razonamiento interno", status: "done" },
      { id: "2", label: "Procesando", status: "done" },
      { id: "3", label: "Procesando", status: "done" },
      { id: "4", label: "Buscando productos", status: "done" },
      { id: "5", label: "Buscando productos", status: "done" },
      { id: "6", label: "Consultando historial", status: "running" },
    ]);

    expect(traces.map((trace) => trace.label)).toEqual([
      "Razonamiento interno",
      "Procesando",
      "Buscando productos",
      "Consultando historial",
    ]);
    expect(traces.at(-1)?.status).toBe("running");
  });

  it("limits completed activity but keeps the active running tool", () => {
    const traces = compactTraceSteps([
      { id: "1", label: "A", status: "done" },
      { id: "2", label: "B", status: "done" },
      { id: "3", label: "C", status: "done" },
      { id: "4", label: "D", status: "done" },
      { id: "5", label: "E", status: "done" },
      { id: "6", label: "F", status: "running" },
    ]);

    expect(traces.map((trace) => trace.label)).toEqual(["A", "C", "D", "E", "F"]);
  });
});

describe("appendTextDelta", () => {
  it("separates text that resumes after a tool trace from existing answer text", () => {
    expect(appendTextDelta("Buena pregunta.", "Mira, tengo datos nuevos.", true)).toBe(
      "Buena pregunta.\n\nMira, tengo datos nuevos.",
    );
  });

  it("does not add separators before the first content or when the delta already starts with spacing", () => {
    expect(appendTextDelta("", "Primera respuesta.", true)).toBe("Primera respuesta.");
    expect(appendTextDelta("Buena pregunta.", "\n\nMira, tengo datos nuevos.", true)).toBe(
      "Buena pregunta.\n\nMira, tengo datos nuevos.",
    );
  });
});

describe("hasUnsafeAgentOutput", () => {
  it("detects leaked internal tool markup", () => {
    expect(
      hasUnsafeAgentOutput(
        '<invoke name="search-products"><parameter name="query">notebook estudiante</parameter></invoke>',
      ),
    ).toBe(true);
    expect(
      hasUnsafeAgentOutput(
        '{"name": "search-products", "arguments": {"query": "notebook estudiante"}}',
      ),
    ).toBe(true);
  });

  it("detects meta narration that is not a final answer", () => {
    expect(hasUnsafeAgentOutput("Voy a buscar notebooks en varias tiendas.")).toBe(true);
    expect(hasUnsafeAgentOutput("Ahora armo la respuesta final con los datos disponibles.")).toBe(
      true,
    );
  });

  it("allows normal user-facing recommendations", () => {
    expect(hasUnsafeAgentOutput("La mejor opcion general es esta notebook por garantia.")).toBe(
      false,
    );
  });
});
