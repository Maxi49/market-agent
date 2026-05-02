import type { OutputProcessor } from "@mastra/core/processors";

const TOOL_MARKUP_PATTERNS = [
  /DSML/i,
  /tool_calls/i,
  /^\s*\[Tool call:/i,
  /\bsearch-products\s*\(/i,
  /\bsearchProducts\s*\(/i,
  /<\|\s*\|\s*invoke\b/i,
  /invoke\s+name=/i,
  /parameter\s+name=/i,
];

export function hasToolMarkup(text: string | undefined): boolean {
  if (!text) {
    return false;
  }

  return TOOL_MARKUP_PATTERNS.some((pattern) => pattern.test(text));
}

function hasVisibleText(text: string | undefined): boolean {
  return Boolean(text?.trim());
}

export const toolMarkupGuardProcessor: OutputProcessor = {
  id: "tool-markup-guard",
  name: "Tool Markup Guard",
  async processOutputStream({ abort, part }) {
    if (part.type === "text-delta" && hasToolMarkup(part.payload.text)) {
      abort(
        [
          "La respuesta intento mostrar una llamada a herramienta como texto visible.",
          "No muestres tool calls; responde en lenguaje natural con los resultados disponibles.",
        ].join(" "),
        { retry: true },
      );
    }

    return part;
  },
  processOutputStep({ abort, text, toolCalls, messages }) {
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
