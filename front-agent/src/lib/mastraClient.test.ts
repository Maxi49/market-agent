import { describe, expect, it } from "vitest";
import { streamAgentResponse } from "./mastraClient";

describe("streamAgentResponse", () => {
  it("posts to the configured Mastra stream route with memory scope", async () => {
    const calls: Array<{ input: RequestInfo | URL; init?: RequestInit }> = [];
    const fetcher = async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ input, init });
      return new Response(
        new ReadableStream({
          start(controller) {
            controller.enqueue(
              new TextEncoder().encode('data: {"type":"text-delta","textDelta":"Hola"}\n\n'),
            );
            controller.close();
          },
        }),
      );
    };
    const text: string[] = [];

    await streamAgentResponse(
      {
        prompt: "notebooks",
        threadId: "thread-1",
        resourceId: "front-agent-local-user",
        agentId: "market-shopping-agent",
        baseUrl: "",
      },
      {
        onTextDelta: (delta) => text.push(delta),
      },
      fetcher,
    );

    expect(calls).toHaveLength(1);
    expect(String(calls[0].input)).toBe("/api/agents/market-shopping-agent/stream");
    expect(calls[0].init?.method).toBe("POST");
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({
      messages: "notebooks",
      memory: {
        thread: "thread-1",
        resource: "front-agent-local-user",
      },
      maxSteps: 5,
    });
    expect(text.join("")).toBe("Hola");
  });

  it("reports a useful error when the response is not ok", async () => {
    await expect(
      streamAgentResponse(
        {
          prompt: "iphone",
          threadId: "thread-1",
          resourceId: "front-agent-local-user",
          agentId: "market-shopping-agent",
          baseUrl: "",
        },
        {},
        async () => new Response("Nope", { status: 503, statusText: "Unavailable" }),
      ),
    ).rejects.toThrow("Mastra respondio 503 Unavailable: Nope");
  });
});
