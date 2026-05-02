import { describe, expect, it } from "vitest";
import { renderBasicMarkdown } from "./markdown";

describe("renderBasicMarkdown", () => {
  it("renders markdown tables as html tables", () => {
    const html = renderBasicMarkdown("| Tienda | Precio |\n|---|---|\n| ML | $ 10 |");

    expect(html).toContain("<table>");
    expect(html).toContain("<th>Tienda</th>");
    expect(html).toContain("<td>$ 10</td>");
  });

  it("renders headings, horizontal rules, and dash lists as structured html", () => {
    const html = renderBasicMarkdown(
      "Intro\n\n---\n\n## Opciones en Argentina\n\n- Compra local\n- Garantia oficial",
    );

    expect(html).toContain("<hr>");
    expect(html).toContain("<h2>Opciones en Argentina</h2>");
    expect(html).toContain("<ul>");
    expect(html).toContain("<li>Compra local</li>");
  });

  it("renders markdown links as compact Ver buttons", () => {
    const html = renderBasicMarkdown("[Ver](https://example.com/producto-largo)");

    expect(html).toContain('href="https://example.com/producto-largo"');
    expect(html).toContain('class="markdown-link-button"');
    expect(html).toContain(">Ver</a>");
    expect(html).not.toContain("https://example.com/producto-largo</a>");
  });

  it("renders table link cells as Ver buttons without showing long URLs", () => {
    const html = renderBasicMarkdown(
      "| Producto | Link |\n|---|---|\n| Notebook | [Ver](https://listado.mercadolibre.com.ar/notebook-asus-vivobook-go-15-e1504ga-nj030w) |",
    );

    expect(html).toContain("<td>");
    expect(html).toContain('target="_blank"');
    expect(html).toContain(">Ver</a>");
    expect(html).not.toContain("[Ver](https://");
  });

  it("normalizes any markdown link label to Ver", () => {
    const html = renderBasicMarkdown("[Fravega](https://www.fravega.com/producto)");

    expect(html).toContain(">Ver</a>");
    expect(html).not.toContain(">Fravega</a>");
  });

  it("normalizes Mercado Libre listing fallback slugs to stable search URLs", () => {
    const html = renderBasicMarkdown(
      "[Ver](https://listado.mercadolibre.com.ar/notebook-asus-vivobook-go-15-e1504ga-nj030w)",
    );

    expect(html).toContain(
      'href="https://www.mercadolibre.com.ar/jm/search?as_word=notebook+asus+vivobook+go+15+e1504ga+nj030w"',
    );
    expect(html).not.toContain("listado.mercadolibre.com.ar/notebook-asus-vivobook");
  });

  it("escapes unsafe html from model text", () => {
    expect(renderBasicMarkdown("<script>alert(1)</script>")).toContain(
      "&lt;script&gt;alert(1)&lt;/script&gt;",
    );
  });

  it("promotes recommended table rows and assigns medal classes from the analysis", () => {
    const html = renderBasicMarkdown(
      [
        "| # | Tienda | Producto | Precio ARS | Condicion | Link |",
        "|---|--------|----------|------------|-----------|------|",
        "| 1 | Megatone | Samsung RT29K577JS8 308L No Frost Inverter Dispenser Inox | $999.999 | Nuevo | [Ver](https://example.com/1) |",
        "| 2 | Fravega | Samsung 299L Freezer Inverter Twin Dispenser Inox | $1.124.999 | Nuevo | [Ver](https://example.com/2) |",
        "| 3 | Carrefour | Samsung RT42 407L Black Dispenser | $1.199.000 | Nuevo | [Ver](https://example.com/3) |",
        "| 4 | Carrefour | Samsung RT53 DG6750S9BG 517L Dispenser | $1.399.000 | Nuevo | [Ver](https://example.com/4) |",
        "",
        "## Analisis",
        "",
        "**Mejor opcion general — Samsung RT29K577JS8 308L con dispenser ($999.999 en Megatone)**",
        "**Mejor precio-calidad — Samsung RT42 407L Black Dispenser ($1.199.000 en Carrefour)**",
        "**Si queres capacidad grande — Samsung RT53 DG6750S9BG 517L ($1.399.000 en Carrefour)**",
      ].join("\n"),
    );

    const first = html.indexOf("Samsung RT29K577JS8");
    const second = html.indexOf("Samsung RT42 407L Black Dispenser");
    const third = html.indexOf("Samsung RT53 DG6750S9BG");
    const nonRecommended = html.indexOf("Samsung 299L Freezer");

    expect(first).toBeLessThan(second);
    expect(second).toBeLessThan(third);
    expect(third).toBeLessThan(nonRecommended);
    expect(html).toContain('class="recommendation-row recommendation-gold"');
    expect(html).toContain('class="recommendation-row recommendation-silver"');
    expect(html).toContain('class="recommendation-row recommendation-bronze"');
    expect(html).toContain('<span class="recommendation-badge">#1</span>');
  });

  it("can defer recommendation promotion while the agent is still streaming", () => {
    const html = renderBasicMarkdown(
      [
        "| # | Tienda | Producto | Precio ARS |",
        "|---|--------|----------|------------|",
        "| 1 | ML | Producto A | $10 |",
        "| 2 | ML | Producto B | $20 |",
        "",
        "**Mejor opcion general — Producto B ($20 en ML)**",
      ].join("\n"),
      { enhanceRecommendations: false },
    );

    expect(html).not.toContain("recommendation-row");
    expect(html.indexOf("Producto A")).toBeLessThan(html.indexOf("Producto B"));
  });

  it("leaves tables untouched when recommendations cannot be matched confidently", () => {
    const html = renderBasicMarkdown(
      [
        "| # | Tienda | Producto | Precio ARS |",
        "|---|--------|----------|------------|",
        "| 1 | ML | Producto A | $10 |",
        "| 2 | ML | Producto B | $20 |",
        "",
        "**Mejor opcion general — Producto totalmente distinto ($999 en Fravega)**",
      ].join("\n"),
    );

    expect(html).not.toContain("recommendation-row");
    expect(html.indexOf("Producto A")).toBeLessThan(html.indexOf("Producto B"));
  });
});
