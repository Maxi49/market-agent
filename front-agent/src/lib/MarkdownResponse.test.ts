import { render } from "svelte/server";
import { describe, expect, it } from "vitest";
import MarkdownResponse from "./MarkdownResponse.svelte";

describe("MarkdownResponse", () => {
  it("renders streaming text as animatable token spans", () => {
    const { body } = render(MarkdownResponse, {
      props: {
        markdown: "Hola mundo nuevo",
        streaming: true,
        enhanceRecommendations: false,
      },
    });

    expect(body).toContain("stream-token-enter");
    expect(body).toContain("--stream-token-delay");
  });

  it("removes token animation classes once streaming has finished", () => {
    const { body } = render(MarkdownResponse, {
      props: {
        markdown: "Hola mundo nuevo",
        streaming: false,
        enhanceRecommendations: false,
      },
    });

    expect(body).not.toContain("stream-token-enter");
  });
});
