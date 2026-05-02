import assert from "node:assert/strict";
import { test } from "node:test";
import {
  MARKET_AGENT_MAX_STEPS,
  marketShoppingAgent,
} from "./marketShoppingAgent";

test("agent instructions forbid repeated final-answer loops", async () => {
  const raw = await marketShoppingAgent.getInstructions();
  const instructions = Array.isArray(raw) ? raw.join("\n") : String(raw);

  assert.match(instructions, /Nunca repitas la misma oracion/i);
});

test("agent instructions forbid invented prices when search has no matches", async () => {
  const raw = await marketShoppingAgent.getInstructions();
  const instructions = Array.isArray(raw) ? raw.join("\n") : String(raw);

  assert.match(instructions, /bestMatches.*vacio/i);
  assert.match(instructions, /no inventes precios/i);
});

test("agent instructions require nearby alternatives instead of dead ends", async () => {
  const raw = await marketShoppingAgent.getInstructions();
  const instructions = Array.isArray(raw) ? raw.join("\n") : String(raw);

  assert.match(instructions, /alternativas cercanas/i);
  assert.match(instructions, /no cierres la respuesta/i);
  assert.match(instructions, /45.*50.*55/i);
});

test("agent instructions keep one call per store but forbid duplicate store searches", async () => {
  const raw = await marketShoppingAgent.getInstructions();
  const instructions = Array.isArray(raw) ? raw.join("\n") : String(raw);

  assert.match(instructions, /una llamada separada por tienda/i);
  assert.match(instructions, /no repitas/i);
  assert.match(instructions, /query \+ tienda \+ modo \+ presupuesto/i);
  assert.match(instructions, /espera.*respuestas/i);
});

test("agent instructions prefer explicit ARS and USD prices from search results", async () => {
  const raw = await marketShoppingAgent.getInstructions();
  const instructions = Array.isArray(raw) ? raw.join("\n") : String(raw);

  assert.match(instructions, /priceARS.*priceUSD/i);
});

test("agent forces tool use on step 0 and locks response on final step", async () => {
  const raw = await marketShoppingAgent.getInstructions();
  const instructions = Array.isArray(raw) ? raw.join("\n") : String(raw);
  const options = await marketShoppingAgent.getDefaultOptions();

  assert.equal(MARKET_AGENT_MAX_STEPS, 5);
  assert.equal(options.maxSteps, 5);

  assert.deepEqual(
    await options.prepareStep?.({
      stepNumber: 0,
      systemMessages: [],
    } as never),
    {
      toolChoice: "required",
    },
  );

  // Pasos intermedios: el agente puede usar tools o no segun necesite
  assert.equal(
    await options.prepareStep?.({ stepNumber: 1, systemMessages: [] } as never),
    undefined,
  );

  // Ultimo paso: sin tools, solo respuesta final
  const finalStepOptions = await options.prepareStep?.({
    stepNumber: MARKET_AGENT_MAX_STEPS - 1,
    systemMessages: [],
  } as never);

  assert.deepEqual(finalStepOptions, {
    activeTools: [],
    systemMessages: [
      {
        role: "system",
        content:
          "Este es el paso final. No intentes llamar herramientas, no describas tool calls y no escribas sintaxis de herramientas. Responde al usuario en lenguaje natural usando solo los resultados ya disponibles. Si faltan datos, aclara la limitacion sin inventar precios.",
      },
    ],
    tools: {},
    toolChoice: "none",
  });
  assert.match(instructions, /siguiente paso es la respuesta final/i);
  assert.match(instructions, /No escribas mensajes antes de usar herramientas/i);
  assert.match(instructions, /Nunca escribas sintaxis interna de herramientas/i);
});
