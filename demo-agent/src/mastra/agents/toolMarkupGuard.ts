import type { OutputProcessor } from "@mastra/core/processors";

// Actual tool-call syntax that must never appear as visible text.
// Used in both stream and step processors.
const TOOL_DSL_PATTERNS = [
  /DSML/i,
  /tool_calls/i,
  /^\s*\[Tool call:/i,
  /\bsearch-products\s*\(/i,
  /\bsearchProducts\s*\(/i,
  /"name"\s*:\s*"search-?products?"/i,
  /"name"\s*:\s*"searchProducts"/i,
  /"arguments"\s*:\s*\{[^}]*"query"\s*:/i,
  /<\|\s*\|\s*invoke\b/i,
  /invoke\s+name=/i,
  /parameter\s+name=/i,
];

// Meta-narration: the model describing what it is about to do before calling
// a tool.  These are suppressed in the stream only — the step processor must
// NOT abort on them because the same phrases appear legitimately at the start
// of final-answer steps.
const NARRATION_PATTERNS = [
  /\b(voy|paso|vamos)\s+a\s+buscar\b/i,
  /\bllamo\s+en\s+paralelo\b/i,
  /\bahora\s+armo\s+la\s+respuesta\s+final\b/i,
];

/** Returns true only for actual tool-call DSL syntax leaked as text. */
export function hasToolMarkup(text: string | undefined): boolean {
  if (!text) {
    return false;
  }

  return TOOL_DSL_PATTERNS.some((pattern) => pattern.test(text));
}

/** Returns true for both DSL syntax and meta-narration (stream-side check). */
function hasUnsafeStreamContent(text: string | undefined): boolean {
  if (!text) {
    return false;
  }

  return (
    TOOL_DSL_PATTERNS.some((p) => p.test(text)) ||
    NARRATION_PATTERNS.some((p) => p.test(text))
  );
}

function hasVisibleText(text: string | undefined): boolean {
  return Boolean(text?.trim());
}

export const toolMarkupGuardProcessor: OutputProcessor = {
  id: "tool-markup-guard",
  name: "Tool Markup Guard",
  async processOutputStream({ part, state, retryCount, writer }) {
    if (part.type !== "text-delta") {
      // Non-text event: reset text accumulation for the next text block
      state.streamText = "";
      state.streamRetry = retryCount;
      state.dropText = false;
      return part;
    }

    // Reset when a new retry attempt starts
    if ((state.streamRetry as number | undefined) !== retryCount) {
      state.streamRetry = retryCount;
      state.streamText = "";
      state.dropText = false;
    }

    const chunk = (part.payload?.text as string | undefined) ?? "";

    if (state.dropText) {
      // Track dropped text so we can detect a paragraph break.
      // Once we see \n\n the narration preamble is over and the actual
      // response content begins — resume streaming from the next chunk.
      const droppedSoFar = ((state.streamText as string | undefined) ?? "") + chunk;
      state.streamText = droppedSoFar;

      if (droppedSoFar.includes("\n\n")) {
        // Preamble ended. Resume for next chunk; current chunk still dropped.
        state.dropText = false;
        state.streamText = "";
      }

      return null;
    }

    state.streamText = ((state.streamText as string | undefined) ?? "") + chunk;

    if (hasUnsafeStreamContent(state.streamText as string)) {
      state.dropText = true;
      state.hadSilentDrop = true;
      // Signal the client to discard accumulated content so far
      await writer?.custom({ type: "data-content-reset" as const, data: null });
      return null;
    }

    return part;
  },
  processOutputStep({ abort, text, toolCalls, messages, state }) {
    // Text preamble was silently dropped at stream level and actual tool calls
    // were made — let the tool calls proceed without aborting.
    if (state.hadSilentDrop) {
      state.hadSilentDrop = false;
      if (toolCalls?.length) {
        return messages;
      }
    }

    // Only abort for actual tool-call DSL syntax in the final text.
    // Meta-narration patterns are intentionally excluded here: the same
    // phrases appear legitimately in final-answer steps and must not abort.
    if (hasToolMarkup(text)) {
      abort(
        [
          "La respuesta intento escribir una llamada a herramienta como texto visible.",
          "No escribas DSML, tool_calls, invoke ni parametros de tools.",
          "Responde al usuario en lenguaje natural usando los resultados ya disponibles.",
        ].join(" "),
        { retry: true },
      );
    }

    if (toolCalls?.length && hasVisibleText(text)) {
      abort(
        [
          "La respuesta escribio texto visible antes de llamar una herramienta.",
          "Cuando uses tools, no escribas introducciones ni narracion previa.",
          "Llama la herramienta directamente y deja la explicacion para la respuesta final.",
        ].join(" "),
        { retry: true },
      );
    }

    return messages;
  },
};
