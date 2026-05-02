import { createTool } from "@mastra/core/tools";
import { z } from "zod";

const DOLAR_API_URL = "https://dolarapi.com/v1/dolares";

// Recargos típicos promedio de tarjetas de crédito en Argentina
const INSTALLMENT_RATES: Record<number, number> = {
  1: 0,
  3: 0.15,
  6: 0.32,
  9: 0.48,
  12: 0.65,
  18: 1.0,
  24: 1.3,
};

export const getExchangeRatesTool = createTool({
  id: "get-exchange-rates",
  description:
    "Obtiene las cotizaciones actuales del dolar en Argentina (blue, oficial, tarjeta/turista). Usala cuando haya precios en USD y necesites mostrarle al usuario cuánto sale en pesos.",
  inputSchema: z.object({}),
  outputSchema: z.object({
    blue: z.object({ compra: z.number(), venta: z.number() }),
    oficial: z.object({ compra: z.number(), venta: z.number() }),
    tarjeta: z.object({ compra: z.number(), venta: z.number() }).nullable(),
    updatedAt: z.string().nullable(),
  }),
  execute: async (_input, context) => {
    const signal = context?.abortSignal;
    const response = await fetch(DOLAR_API_URL, { signal });
    if (!response.ok) {
      throw new Error(`dolarapi.com error: ${response.status}`);
    }
    const data = (await response.json()) as Array<{
      casa: string;
      compra: number;
      venta: number;
      fechaActualizacion: string;
    }>;
    const find = (casa: string) => data.find((d) => d.casa === casa);
    const blue = find("blue");
    const oficial = find("oficial");
    const tarjeta = find("tarjeta");
    return {
      blue: { compra: blue?.compra ?? 0, venta: blue?.venta ?? 0 },
      oficial: { compra: oficial?.compra ?? 0, venta: oficial?.venta ?? 0 },
      tarjeta: tarjeta ? { compra: tarjeta.compra, venta: tarjeta.venta } : null,
      updatedAt: blue?.fechaActualizacion ?? null,
    };
  },
});

export const thinkTool = createTool({
  id: "think",
  description:
    "Usalo para razonar internamente antes de responder. No obtiene datos ni modifica nada — el resultado nunca se muestra al usuario. " +
    "Usalo obligatoriamente antes de tu respuesta final para: calcular el precio promedio de los productos nuevos, identificar cuáles valen la mitad o menos (sospechosos), decidir el orden de la tabla y qué columnas incluir.",
  inputSchema: z.object({
    thought: z.string().describe("Tu razonamiento interno"),
  }),
  outputSchema: z.object({ ok: z.literal(true) }),
  execute: async () => ({ ok: true as const }),
});

export const calculateInstallmentsTool = createTool({
  id: "calculate-installments",
  description:
    "Calcula cuánto sale un producto en cuotas con los recargos típicos de tarjetas de crédito en Argentina. Usala cuando el usuario pregunte por financiación o cuotas.",
  inputSchema: z.object({
    priceARS: z.number().positive().describe("Precio en pesos argentinos"),
    installmentOptions: z
      .array(z.number().int().positive())
      .default([3, 6, 12, 18, 24])
      .describe("Cantidades de cuotas a calcular"),
  }),
  outputSchema: z.object({
    priceARS: z.number(),
    options: z.array(
      z.object({
        cuotas: z.number(),
        recargo: z.number(),
        totalARS: z.number(),
        cuotaARS: z.number(),
      }),
    ),
    disclaimer: z.string(),
  }),
  execute: async ({ priceARS, installmentOptions = [3, 6, 12, 18, 24] }) => {
    const options = installmentOptions.map((cuotas) => {
      const rate = INSTALLMENT_RATES[cuotas] ?? INSTALLMENT_RATES[24];
      const totalARS = Math.round(priceARS * (1 + rate));
      const cuotaARS = Math.round(totalARS / cuotas);
      return { cuotas, recargo: rate * 100, totalARS, cuotaARS };
    });
    return {
      priceARS,
      options,
      disclaimer:
        "Recargos estimados promedio. Los valores exactos dependen del banco y la tarjeta.",
    };
  },
});
