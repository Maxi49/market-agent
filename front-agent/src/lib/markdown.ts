type RenderMarkdownOptions = {
  enhanceRecommendations?: boolean;
};

export function renderBasicMarkdown(
  markdown: string,
  options: RenderMarkdownOptions = {},
): string {
  const lines = markdown.split(/\r?\n/);
  const recommendations = options.enhanceRecommendations === false ? [] : extractRecommendations(markdown);
  const blocks: string[] = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index].trim();

    if (!line) {
      index += 1;
      continue;
    }

    if (isTableStart(lines, index)) {
      const tableLines: string[] = [];
      while (index < lines.length && lines[index].includes("|")) {
        tableLines.push(lines[index]);
        index += 1;
      }
      blocks.push(renderTable(tableLines, recommendations));
      continue;
    }

    if (/^-{3,}$/.test(line)) {
      blocks.push("<hr>");
      index += 1;
      continue;
    }

    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      const level = heading[1].length;
      blocks.push(`<h${level}>${inlineMarkdown(heading[2])}</h${level}>`);
      index += 1;
      continue;
    }

    if (/^-\s+/.test(line)) {
      const items: string[] = [];
      while (index < lines.length && /^-\s+/.test(lines[index].trim())) {
        items.push(lines[index].trim().replace(/^-\s+/, ""));
        index += 1;
      }
      blocks.push(`<ul>${items.map((item) => `<li>${inlineMarkdown(item)}</li>`).join("")}</ul>`);
      continue;
    }

    const paragraph: string[] = [];
    while (
      index < lines.length &&
      lines[index].trim() &&
      !isTableStart(lines, index) &&
      !/^-{3,}$/.test(lines[index].trim()) &&
      !/^(#{1,3})\s+/.test(lines[index].trim()) &&
      !/^-\s+/.test(lines[index].trim())
    ) {
      paragraph.push(lines[index]);
      index += 1;
    }

    blocks.push(renderParagraph(paragraph.join("\n")));
  }

  return blocks.filter(Boolean).join("");
}

type RecommendationRank = 1 | 2 | 3 | 4;

type Recommendation = {
  rank: RecommendationRank;
  text: string;
  normalizedText: string;
  priceKey: string | null;
  store: string | null;
};

type TableRow = {
  cells: string[];
  originalIndex: number;
  rank?: RecommendationRank;
};

function isTableStart(lines: string[], index: number): boolean {
  return Boolean(lines[index]?.includes("|") && lines[index + 1]?.match(/^\s*\|?\s*:?-{3,}/));
}

function renderTable(lines: string[], recommendations: Recommendation[]): string {
  const [headerLine, , ...bodyLines] = lines;
  const headers = splitTableLine(headerLine);
  const rows = promoteRecommendedRows(
    bodyLines
      .map(splitTableLine)
      .filter((row) => row.length > 0)
      .map((cells, originalIndex) => ({ cells, originalIndex })),
    recommendations,
  );
  const hasRecommendations = rows.some((row) => row.rank);
  const tableClass = hasRecommendations ? ' class="recommendation-table"' : "";

  return `<table${tableClass}><thead><tr>${headers
    .map((cell) => `<th>${inlineMarkdown(cell)}</th>`)
    .join("")}</tr></thead><tbody>${rows
    .map((row) => renderTableRow(row))
    .join("")}</tbody></table>`;
}

function renderTableRow(row: TableRow): string {
  const className = row.rank ? ` class="recommendation-row ${rankClass(row.rank)}"` : "";
  const cells = row.cells
    .map((cell, index) => `<td>${renderTableCell(cell, row.rank, index)}</td>`)
    .join("");

  return `<tr${className}>${cells}</tr>`;
}

function renderTableCell(cell: string, rank: RecommendationRank | undefined, index: number): string {
  if (!rank || index !== 0) {
    return inlineMarkdown(cell);
  }

  return `<span class="recommendation-badge">#${rank}</span><span class="recommendation-original-rank">${inlineMarkdown(cell)}</span>`;
}

function rankClass(rank: RecommendationRank): string {
  if (rank === 1) return "recommendation-gold";
  if (rank === 2) return "recommendation-silver";
  if (rank === 3) return "recommendation-bronze";
  return "recommendation-highlight";
}

function promoteRecommendedRows(rows: TableRow[], recommendations: Recommendation[]): TableRow[] {
  if (!recommendations.length || rows.length < 2) {
    return rows;
  }

  const claimedRows = new Set<number>();
  const promoted: TableRow[] = [];

  for (const recommendation of recommendations) {
    const candidate = bestRowMatch(rows, recommendation, claimedRows);
    if (!candidate) {
      continue;
    }

    claimedRows.add(candidate.row.originalIndex);
    promoted.push({ ...candidate.row, rank: recommendation.rank });
  }

  if (!promoted.length) {
    return rows;
  }

  const promotedIds = new Set(promoted.map((row) => row.originalIndex));
  return [
    ...promoted.sort((left, right) => (left.rank ?? 4) - (right.rank ?? 4)),
    ...rows.filter((row) => !promotedIds.has(row.originalIndex)),
  ];
}

function bestRowMatch(
  rows: TableRow[],
  recommendation: Recommendation,
  claimedRows: Set<number>,
): { row: TableRow; score: number } | null {
  const candidates = rows
    .filter((row) => !claimedRows.has(row.originalIndex))
    .map((row) => ({ row, score: recommendationScore(row.cells, recommendation) }))
    .filter((candidate) => candidate.score >= 7)
    .sort((left, right) => right.score - left.score);

  return candidates[0] ?? null;
}

function recommendationScore(cells: string[], recommendation: Recommendation): number {
  const rowText = normalizeText(cells.join(" "));
  const rowPrice = normalizePrice(cells.join(" "));
  const rowTokens = significantTokens(rowText);
  const recommendationTokens = significantTokens(recommendation.normalizedText);
  const overlap = recommendationTokens.filter((token) => rowTokens.includes(token)).length;
  let score = overlap;

  if (recommendation.priceKey && rowPrice === recommendation.priceKey) {
    score += 6;
  }

  if (recommendation.store && rowText.includes(recommendation.store)) {
    score += 4;
  }

  if (recommendationTokens.length > 0 && overlap / recommendationTokens.length >= 0.45) {
    score += 3;
  }

  return score;
}

function extractRecommendations(markdown: string): Recommendation[] {
  const recommendations: Recommendation[] = [];
  const lines = markdown
    .split(/\r?\n/)
    .map((line) => stripMarkdown(line).trim())
    .filter(Boolean);

  for (const line of lines) {
    const rank = recommendationRank(line, recommendations.length);
    if (!rank || recommendations.some((recommendation) => recommendation.rank === rank)) {
      continue;
    }

    recommendations.push({
      rank,
      text: line,
      normalizedText: normalizeText(line),
      priceKey: normalizePrice(line),
      store: extractStore(line),
    });

    if (recommendations.length >= 4) {
      break;
    }
  }

  return recommendations;
}

function recommendationRank(line: string, currentCount: number): RecommendationRank | null {
  const normalized = normalizeText(line);

  if (/mejor opcion general/.test(normalized)) return 1;
  if (/mejor precio calidad|precio calidad/.test(normalized)) return 2;
  if (/alternativa solida|si queres|capacidad grande|tercer/.test(normalized)) return 3;

  if (
    currentCount >= 3 &&
    currentCount < 4 &&
    /^.+\s+-\s+.+/.test(normalized) &&
    !normalized.includes("productos a verificar")
  ) {
    return 4;
  }

  return null;
}

function renderParagraph(text: string): string {
  return text
    .split(/\n{2,}/)
    .map((block) => block.trim())
    .filter(Boolean)
    .map((block) => `<p>${inlineMarkdown(block).replace(/\n/g, "<br>")}</p>`)
    .join("");
}

function splitTableLine(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function stripMarkdown(value: string): string {
  return value
    .replace(/\*\*/g, "")
    .replace(/[`_*]/g, "")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, "$1")
    .replace(/^[^\p{L}\p{N}$]+/u, "")
    .trim();
}

function extractStore(value: string): string | null {
  const normalized = normalizeText(value);
  const match = normalized.match(/\ben\s+([a-z0-9 ]+)\)?$/);
  return match?.[1]?.trim() ?? null;
}

function normalizeText(value: string): string {
  return value
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[$.,]/g, " ")
    .replace(/[-–—]/g, " ")
    .replace(/[^\p{L}\p{N}\s]/gu, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function normalizePrice(value: string): string | null {
  const match = value.match(/\$\s*[\d.,]+/);
  if (!match) {
    return null;
  }

  const digits = match[0].replace(/\D/g, "");
  return digits.length > 0 ? digits : null;
}

function significantTokens(value: string): string[] {
  const stopWords = new Set([
    "mejor",
    "opcion",
    "general",
    "precio",
    "calidad",
    "alternativa",
    "solida",
    "queres",
    "capacidad",
    "grande",
    "con",
    "sin",
    "para",
    "por",
    "en",
    "el",
    "la",
    "los",
    "las",
    "una",
    "uno",
    "nuevo",
    "nueva",
    "ars",
  ]);

  return value
    .split(/\s+/)
    .map((token) => token.replace(/^-+|-+$/g, ""))
    .filter((token) => token.length >= 3 && !stopWords.has(token));
}

function inlineMarkdown(value: string): string {
  const segments: string[] = [];
  const linkPattern = /\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g;
  let cursor = 0;
  let match: RegExpExecArray | null;

  while ((match = linkPattern.exec(value))) {
    segments.push(formatInlineText(value.slice(cursor, match.index)));
    segments.push(renderLink(match[1], match[2]));
    cursor = match.index + match[0].length;
  }

  segments.push(formatInlineText(value.slice(cursor)));
  return segments.join("");
}

function formatInlineText(value: string): string {
  return escapeHtml(value).replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");
}

function renderLink(_label: string, href: string): string {
  const safeHref = escapeAttribute(normalizeHref(href));

  return `<a class="markdown-link-button" href="${safeHref}" target="_blank" rel="noreferrer">Ver</a>`;
}

function normalizeHref(href: string): string {
  try {
    const url = new URL(href);
    if (url.hostname === "listado.mercadolibre.com.ar") {
      const query = url.pathname.replace(/^\/+/, "").replace(/-/g, " ").trim();
      if (query) {
        return `https://www.mercadolibre.com.ar/jm/search?as_word=${encodeQuery(query)}`;
      }
    }
  } catch {
    return href;
  }

  return href;
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function escapeAttribute(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/"/g, "&quot;");
}

function encodeQuery(value: string): string {
  return encodeURIComponent(value).replace(/%20/g, "+");
}
