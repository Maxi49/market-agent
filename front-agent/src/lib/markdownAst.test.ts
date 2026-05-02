import { describe, expect, it } from "vitest";
import {
  buildMarkdownAst,
  extractRecommendations,
  nodeText,
  normalizeLinkHref,
  parseMarkdown,
} from "./markdownAst";

function tableBodyProducts(markdown: string, enhanceRecommendations = true): string[] {
  const root = buildMarkdownAst(markdown, { enhanceRecommendations });
  const table = root.children.find((node) => node.type === "table");
  const rows = table?.children?.slice(1) ?? [];

  return rows.map((row) => nodeText(row.children?.[2]));
}

describe("markdown AST renderer pipeline", () => {
  it("parses gfm tables as stable AST nodes", () => {
    const root = parseMarkdown("| Tienda | Precio |\n|---|---|\n| ML | $ 10 |");
    const table = root.children.find((node) => node.type === "table");

    expect(table?.children?.[0]?.children?.map(nodeText)).toEqual(["Tienda", "Precio"]);
    expect(table?.children?.[1]?.children?.map(nodeText)).toEqual(["ML", "$ 10"]);
  });

  it("keeps unsafe html as text-like AST content instead of executable html", () => {
    const root = parseMarkdown("<script>alert(1)</script>");

    expect(root.children[0]?.type).toBe("html");
    expect(nodeText(root)).toContain("<script>alert(1)</script>");
  });

  it("normalizes Mercado Libre listing fallback slugs to stable search URLs", () => {
    expect(
      normalizeLinkHref(
        "https://listado.mercadolibre.com.ar/notebook-asus-vivobook-go-15-e1504ga-nj030w",
      ),
    ).toBe(
      "https://www.mercadolibre.com.ar/jm/search?as_word=notebook+asus+vivobook+go+15+e1504ga+nj030w",
    );
  });

  it("extracts recommendation ranks from analysis text", () => {
    const recommendations = extractRecommendations(
      [
        "**Mejor opcion general — Samsung RT29K577JS8 ($999.999 en Megatone)**",
        "**Mejor precio-calidad — Samsung RT42 Black ($1.199.000 en Carrefour)**",
        "**Si queres capacidad grande — Samsung RT53 ($1.399.000 en Carrefour)**",
      ].join("\n"),
    );

    expect(recommendations.map((recommendation) => recommendation.rank)).toEqual([1, 2, 3]);
    expect(recommendations[0]?.priceKey).toBe("999999");
  });

  it("promotes recommended rows only when enhancement is enabled", () => {
    const markdown = [
      "| # | Tienda | Producto | Precio ARS | Condicion | Link |",
      "|---|--------|----------|------------|-----------|------|",
      "| 1 | Megatone | Samsung RT29K577JS8 308L No Frost Inverter Dispenser Inox | $999.999 | Nuevo | [Ver](https://example.com/1) |",
      "| 2 | Fravega | Samsung 299L Freezer Inverter Twin Dispenser Inox | $1.124.999 | Nuevo | [Ver](https://example.com/2) |",
      "| 3 | Carrefour | Samsung RT42 407L Black Dispenser | $1.199.000 | Nuevo | [Ver](https://example.com/3) |",
      "| 4 | Carrefour | Samsung RT53 DG6750S9BG 517L Dispenser | $1.399.000 | Nuevo | [Ver](https://example.com/4) |",
      "",
      "**Mejor opcion general — Samsung RT29K577JS8 308L con dispenser ($999.999 en Megatone)**",
      "**Mejor precio-calidad — Samsung RT42 407L Black Dispenser ($1.199.000 en Carrefour)**",
      "**Si queres capacidad grande — Samsung RT53 DG6750S9BG 517L ($1.399.000 en Carrefour)**",
    ].join("\n");

    expect(tableBodyProducts(markdown, true)).toEqual([
      "Samsung RT29K577JS8 308L No Frost Inverter Dispenser Inox",
      "Samsung RT42 407L Black Dispenser",
      "Samsung RT53 DG6750S9BG 517L Dispenser",
      "Samsung 299L Freezer Inverter Twin Dispenser Inox",
    ]);
    expect(tableBodyProducts(markdown, false)).toEqual([
      "Samsung RT29K577JS8 308L No Frost Inverter Dispenser Inox",
      "Samsung 299L Freezer Inverter Twin Dispenser Inox",
      "Samsung RT42 407L Black Dispenser",
      "Samsung RT53 DG6750S9BG 517L Dispenser",
    ]);
  });

  it("marks promoted rows with medal data for the Svelte renderer", () => {
    const root = buildMarkdownAst(
      [
        "| # | Tienda | Producto | Precio ARS |",
        "|---|--------|----------|------------|",
        "| 1 | ML | Producto A | $10 |",
        "| 2 | ML | Producto B | $20 |",
        "",
        "**Mejor opcion general — Producto B ($20 en ML)**",
      ].join("\n"),
    );
    const table = root.children.find((node) => node.type === "table");
    const firstBodyRow = table?.children?.[1];

    expect(nodeText(firstBodyRow?.children?.[2])).toBe("Producto B");
    expect(firstBodyRow?.data?.recommendationRank).toBe(1);
    expect(firstBodyRow?.data?.recommendationClass).toBe("recommendation-row recommendation-gold");
  });

  it("leaves tables untouched when recommendations cannot be matched confidently", () => {
    const markdown = [
      "| # | Tienda | Producto | Precio ARS |",
      "|---|--------|----------|------------|",
      "| 1 | ML | Producto A | $10 |",
      "| 2 | ML | Producto B | $20 |",
      "",
      "**Mejor opcion general — Producto totalmente distinto ($999 en Fravega)**",
    ].join("\n");

    expect(tableBodyProducts(markdown)).toEqual(["Producto A", "Producto B"]);
  });
});
