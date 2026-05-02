import {
  extractTextDelta,
  parseSseChunk,
  traceStepFromEvent,
  type MastraStreamEvent,
  type TraceStep,
} from "./mastraStream";

export type StreamAgentInput = {
  prompt: string;
  threadId: string;
  resourceId: string;
  agentId: string;
  baseUrl: string;
  maxSteps?: number;
};

export type StreamAgentHandlers = {
  onTextDelta?: (delta: string) => void;
  onEvent?: (event: MastraStreamEvent) => void;
  onTrace?: (trace: TraceStep) => void;
  onContentReset?: () => void;
};

export type FetchLike = typeof fetch;

export async function streamAgentResponse(
  input: StreamAgentInput,
  handlers: StreamAgentHandlers,
  fetcher: FetchLike = fetch,
): Promise<void> {
  const response = await fetcher(buildAgentStreamUrl(input.baseUrl, input.agentId), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      messages: input.prompt,
      memory: {
        thread: input.threadId,
        resource: input.resourceId,
      },
      maxSteps: input.maxSteps ?? 5,
    }),
  });

  if (!response.ok) {
    const body = await response.text().catch(() => "");
    throw new Error(
      `Mastra respondio ${response.status} ${response.statusText}${body ? `: ${body}` : ""}`,
    );
  }

  if (!response.body) {
    throw new Error("Mastra no devolvio un stream legible.");
  }

  await readMastraStream(response.body, handlers);
}

export function buildAgentStreamUrl(baseUrl: string, agentId: string): string {
  const path = `/api/agents/${encodeURIComponent(agentId)}/stream`;
  return baseUrl ? `${baseUrl.replace(/\/$/, "")}${path}` : path;
}

async function readMastraStream(
  body: ReadableStream<Uint8Array>,
  handlers: StreamAgentHandlers,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let remainder = "";
  let eventIndex = 0;

  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }

    const parsed = parseSseChunk(decoder.decode(value, { stream: true }), remainder);
    remainder = parsed.remainder;

    for (const event of parsed.events) {
      eventIndex += 1;
      handlers.onEvent?.(event);

      if (event.type === "data-content-reset") {
        handlers.onContentReset?.();
        continue;
      }

      const delta = extractTextDelta(event);
      if (delta) {
        handlers.onTextDelta?.(delta);
      }

      const trace = traceStepFromEvent(event, eventIndex);
      if (trace) {
        handlers.onTrace?.(trace);
      }
    }
  }

  const finalText = decoder.decode();
  if (finalText) {
    const parsed = parseSseChunk(finalText, remainder);
    for (const event of parsed.events) {
      eventIndex += 1;
      handlers.onEvent?.(event);

      if (event.type === "data-content-reset") {
        handlers.onContentReset?.();
        continue;
      }

      const delta = extractTextDelta(event);
      if (delta) {
        handlers.onTextDelta?.(delta);
      }
      const trace = traceStepFromEvent(event, eventIndex);
      if (trace) {
        handlers.onTrace?.(trace);
      }
    }
  }
}
