export type MastraStreamEvent = Record<string, unknown> & {
  type?: string;
  textDelta?: string;
  delta?: string;
  text?: string;
  toolName?: string;
  payload?: Record<string, unknown>;
  finishReason?: string;
};

export type ParsedSseChunk = {
  events: MastraStreamEvent[];
  remainder: string;
};

export type TraceStatus = "running" | "done" | "error";

export type TraceStep = {
  id: string;
  label: string;
  status: TraceStatus;
  detail?: string;
};

const traceEventTypes = [
  "tool-call",
  "tool-call-input-streaming-start",
  "tool-call-delta",
  "tool-result",
  "finish-step",
  "finish",
  "error",
];

const unsafeOutputPatterns = [
  /<\s*\/?\s*tool_?call\b/i,
  /<\s*\/?\s*invoke\b/i,
  /<\s*\/?\s*parameter\b/i,
  /"name"\s*:\s*"search-?products?"/i,
  /"name"\s*:\s*"searchProducts"/i,
  /"arguments"\s*:\s*\{[^}]*"query"\s*:/i,
  /\b(voy|paso|vamos)\s+a\s+buscar\b/i,
  /\bllamo\s+en\s+paralelo\b/i,
  /\bahora\s+armo\s+la\s+respuesta\s+final\b/i,
];

export function parseSseChunk(chunk: string, previousRemainder: string): ParsedSseChunk {
  const source = `${previousRemainder}${chunk}`;
  const parts = source.split(/\r?\n\r?\n/);
  const remainder = parts.pop() ?? "";
  const events = parts.flatMap(parseSseEvent);

  return { events, remainder };
}

export function extractTextDelta(event: MastraStreamEvent): string {
  if (event.type !== "text-delta") {
    return "";
  }

  return (
    stringValue(event.textDelta) ??
    stringValue(event.delta) ??
    stringValue(event.text) ??
    stringValue(event.payload?.textDelta) ??
    stringValue(event.payload?.delta) ??
    stringValue(event.payload?.text) ??
    ""
  );
}

export function toTraceLabel(event: MastraStreamEvent): string {
  if (event.type === "finish") {
    return "Respuesta lista";
  }

  if (event.type === "finish-step") {
    return "Paso completado";
  }

  const toolName = toolNameFromEvent(event);
  if (event.type === "tool-result") {
    return `${toolDisplayName(toolName)} listo`;
  }

  if (isToolRunningEvent(event.type)) {
    return toolRunningLabel(toolName);
  }

  return "Procesando";
}

export function traceStepFromEvent(event: MastraStreamEvent, index: number): TraceStep | null {
  if (
    !event.type ||
    !traceEventTypes.includes(event.type)
  ) {
    return null;
  }

  const status: TraceStatus =
    isToolRunningEvent(event.type) ? "running" : event.type === "error" ? "error" : "done";

  return {
    id: `${event.type}-${index}-${toolNameFromEvent(event) ?? "agent"}`,
    label: toTraceLabel(event),
    status,
    detail: traceDetail(event),
  };
}

export function mergeTraceStep(traces: TraceStep[], next: TraceStep): TraceStep[] {
  const nextKey = traceIdentity(next);
  const closedTraces =
    next.status === "running"
      ? traces.map((trace) =>
          trace.status === "running" && traceIdentity(trace) !== nextKey
            ? { ...trace, status: "done" as const }
            : trace,
        )
      : traces;

  const existingIndex = closedTraces.findIndex((trace) => traceIdentity(trace) === nextKey);

  if (existingIndex === -1) {
    return [...closedTraces, next];
  }

  return closedTraces.map((trace, index) =>
    index === existingIndex
      ? {
          ...trace,
          label: next.label,
          status: next.status,
          detail: next.detail ?? trace.detail,
        }
      : trace,
  );
}

export function compactTraceSteps(traces: TraceStep[], maxVisible = 5): TraceStep[] {
  const deduped = traces.filter((trace, index) => {
    const previous = traces[index - 1];
    return (
      !previous ||
      previous.label !== trace.label ||
      previous.status !== trace.status ||
      previous.detail !== trace.detail
    );
  });

  const running = deduped.findLast((trace) => trace.status === "running");
  const completed = deduped.filter((trace) => trace.status !== "running");
  const first = completed[0];
  const recentLimit = Math.max(0, maxVisible - (running ? 2 : 1));
  const recent = completed.slice(-recentLimit);
  const visible = [first, ...recent, running].filter((trace): trace is TraceStep => Boolean(trace));

  return visible.filter(
    (trace, index, list) => list.findIndex((item) => item.id === trace.id) === index,
  );
}

export function appendTextDelta(
  currentContent: string,
  delta: string,
  needsSeparator: boolean,
): string {
  if (!needsSeparator || !currentContent || /^\s/.test(delta)) {
    return `${currentContent}${delta}`;
  }

  return `${currentContent}\n\n${delta}`;
}

export function hasUnsafeAgentOutput(text: string): boolean {
  return unsafeOutputPatterns.some((pattern) => pattern.test(text));
}

export function extractSafeContent(text: string): string {
  let lastUnsafeEnd = -1;

  for (const pattern of unsafeOutputPatterns) {
    const global = new RegExp(pattern.source, pattern.flags.includes("g") ? pattern.flags : `${pattern.flags}g`);
    for (const match of text.matchAll(global)) {
      const lineEnd = text.indexOf("\n", match.index + match[0].length);
      const endPos = lineEnd === -1 ? text.length : lineEnd + 1;
      if (endPos > lastUnsafeEnd) {
        lastUnsafeEnd = endPos;
      }
    }
  }

  if (lastUnsafeEnd === -1) return text;
  return text.slice(lastUnsafeEnd).trim();
}

function isToolRunningEvent(type: string | undefined): boolean {
  return (
    type === "tool-call" ||
    type === "tool-call-input-streaming-start" ||
    type === "tool-call-delta"
  );
}

function traceIdentity(trace: TraceStep): string {
  return trace.detail ?? trace.label;
}

function parseSseEvent(block: string): MastraStreamEvent[] {
  const data = block
    .split(/\r?\n/)
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trimStart())
    .join("\n")
    .trim();

  if (!data || data === "[DONE]") {
    return [];
  }

  try {
    return [JSON.parse(data) as MastraStreamEvent];
  } catch {
    return [];
  }
}

function traceDetail(event: MastraStreamEvent): string | undefined {
  const toolName = toolNameFromEvent(event);
  const finishReason = stringValue(event.finishReason) ?? stringValue(event.payload?.finishReason);

  if (toolName) {
    return toolName;
  }

  if (finishReason) {
    return finishReason;
  }

  return undefined;
}

function toolRunningLabel(toolName: string | undefined): string {
  switch (toolName) {
    case "searchProducts":
      return "Buscando productos";
    case "getMatchingCandidates":
      return "Comparando candidatos";
    case "getPriceHistory":
      return "Consultando historial";
    case "getExchangeRates":
      return "Consultando dolar";
    case "calculateInstallments":
      return "Calculando cuotas";
    case "analyzeProductUrl":
      return "Analizando URL";
    case "think":
      return "Razonamiento interno";
    default:
      return toolName ? `Ejecutando ${toolName}` : "Ejecutando herramienta";
  }
}

function toolDisplayName(toolName: string | undefined): string {
  switch (toolName) {
    case "searchProducts":
      return "Busqueda";
    case "getMatchingCandidates":
      return "Matching";
    case "getPriceHistory":
      return "Historial";
    case "getExchangeRates":
      return "Dolar";
    case "calculateInstallments":
      return "Cuotas";
    case "analyzeProductUrl":
      return "Analisis";
    case "think":
      return "Razonamiento";
    default:
      return toolName ?? "Herramienta";
  }
}

function stringValue(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function toolNameFromEvent(event: MastraStreamEvent): string | undefined {
  return stringValue(event.toolName) ?? stringValue(event.payload?.toolName);
}
