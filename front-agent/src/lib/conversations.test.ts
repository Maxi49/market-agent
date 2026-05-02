import { describe, expect, it } from "vitest";
import { createConversation, hydrateConversations, summarizeTitle } from "./conversations";

describe("createConversation", () => {
  it("creates a stable thread id and default title", () => {
    const conversation = createConversation(() => "fixed-id");

    expect(conversation.id).toBe("fixed-id");
    expect(conversation.title).toBe("Nueva consulta");
    expect(conversation.messages).toEqual([]);
  });
});

describe("summarizeTitle", () => {
  it("builds a short title from the first user prompt", () => {
    expect(summarizeTitle("Notebooks para estudiantes < USD 500 con SSD")).toBe(
      "Notebooks para estudiantes < USD",
    );
  });
});

describe("hydrateConversations", () => {
  it("falls back to one empty conversation when storage is empty or invalid", () => {
    expect(hydrateConversations(null, () => "empty-id")).toEqual([
      createConversation(() => "empty-id"),
    ]);

    expect(hydrateConversations("not-json", () => "invalid-id")).toEqual([
      createConversation(() => "invalid-id"),
    ]);
  });

  it("keeps only valid stored conversations", () => {
    const stored = JSON.stringify([
      { id: "a", title: "iPhone", messages: [], createdAt: 1, updatedAt: 2 },
      { id: "", title: "bad", messages: [] },
    ]);

    expect(hydrateConversations(stored, () => "fallback")).toEqual([
      { id: "a", title: "iPhone", messages: [], createdAt: 1, updatedAt: 2 },
    ]);
  });
});
