import { describe, expect, test } from "vitest";
import {
  MARKET_AGENT_MAX_STEPS,
  marketShoppingAgent,
} from "./marketShoppingAgent";

async function getInstructions(): Promise<string> {
  const raw = await marketShoppingAgent.getInstructions();
  return Array.isArray(raw) ? raw.join("\n") : String(raw);
}

describe("agent instructions", () => {
  test("forbid repeated final-answer loops", async () => {
    const instructions = await getInstructions();
    expect(instructions).toMatch(/Nunca repitas la misma oracion/i);
  });

  test("forbid invented prices when search has no matches", async () => {
    const instructions = await getInstructions();
    expect(instructions).toMatch(/bestMatches.*vacio/i);
    expect(instructions).toMatch(/no inventes precios/i);
  });

  test("require nearby alternatives instead of dead ends", async () => {
    const instructions = await getInstructions();
    expect(instructions).toMatch(/alternativas cercanas/i);
    expect(instructions).toMatch(/no cierres la respuesta/i);
    expect(instructions).toMatch(/45.*50.*55/i);
  });

  test("keep one call per store but forbid duplicate store searches", async () => {
    const instructions = await getInstructions();
    expect(instructions).toMatch(/una llamada separada por tienda/i);
    expect(instructions).toMatch(/no repitas/i);
    expect(instructions).toMatch(/query \+ tienda \+ modo \+ presupuesto/i);
    expect(instructions).toMatch(/espera.*respuestas/i);
  });

  test("prefer explicit ARS and USD prices from search results", async () => {
    const instructions = await getInstructions();
    expect(instructions).toMatch(/priceARS.*priceUSD/i);
  });

  test("document budget as quality signal with minPriceARS guidance", async () => {
    const instructions = await getInstructions();
    expect(instructions).toMatch(/minPriceARS/i);
    expect(instructions).toMatch(/presupuesto.*calidad/i);
    expect(instructions).toMatch(/>80k/i);
  });
});

describe("prepareStep", () => {
  test("forces tool use on step 0", async () => {
    const options = await marketShoppingAgent.getDefaultOptions();
    expect(MARKET_AGENT_MAX_STEPS).toBe(5);
    expect(options.maxSteps).toBe(5);

    const result = await options.prepareStep?.({
      stepNumber: 0,
      systemMessages: [],
    } as never);

    expect((result as { toolChoice?: string })?.toolChoice).toBe("required");
  });

  test("returns system message for intermediate steps", async () => {
    const options = await marketShoppingAgent.getDefaultOptions();
    const result = await options.prepareStep?.({
      stepNumber: 1,
      systemMessages: [],
    } as never) as { systemMessages?: unknown[] } | undefined;

    expect(result?.systemMessages?.length).toBeGreaterThan(0);
  });

  test("locks to no-tool final answer on last step", async () => {
    const options = await marketShoppingAgent.getDefaultOptions();
    const finalStepResult = await options.prepareStep?.({
      stepNumber: MARKET_AGENT_MAX_STEPS - 1,
      systemMessages: [],
    } as never);

    expect(finalStepResult).toMatchObject({
      activeTools: [],
      tools: {},
      toolChoice: "none",
      systemMessages: [
        {
          role: "system",
          content: expect.stringMatching(/Este es el paso final/),
        },
      ],
    });
  });

  test("final step system message forbids tool syntax in response", async () => {
    const options = await marketShoppingAgent.getDefaultOptions();
    const result = await options.prepareStep?.({
      stepNumber: MARKET_AGENT_MAX_STEPS - 1,
      systemMessages: [],
    } as never) as { systemMessages?: { content?: string }[] } | undefined;

    const content = result?.systemMessages?.[0]?.content ?? "";
    expect(content).toMatch(/no escribas sintaxis/i);
  });
});
