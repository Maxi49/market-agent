import remarkGfm from "remark-gfm";
import remarkParse from "remark-parse";
import { unified } from "unified";
import { visit } from "unist-util-visit";

export type RecommendationRank = 1 | 2 | 3 | 4;

export type MarkdownNode = {
  type: string;
  value?: string;
  children?: MarkdownNode[];
  depth?: number;
  ordered?: boolean;
  start?: number;
  url?: string;
  title?: string | null;
  data?: {
    recommendationRank?: RecommendationRank;
    recommendationClass?: string;
    [key: string]: unknown;
  };
};

export type MarkdownRoot = MarkdownNode & {
  type: "root";
  children: MarkdownNode[];
};

export type Recommendation = {
  rank: RecommendationRank;
  text: string;
  normalizedText: string;
  priceKey: string | null;
  store: string | null;
};

type TableRowMatch = {
  node: MarkdownNode;
  cells: string[];
  originalIndex: number;
  rank?: RecommendationRank;
};

const parser = unified().use(remarkParse).use(remarkGfm);

export function parseMarkdown(markdown: string): MarkdownRoot {
  return parser.parse(markdown) as MarkdownRoot;
}

export function buildMarkdownAst(
  markdown: string,
  options: { enhanceRecommendations?: boolean } = {},
): MarkdownRoot {
  const root = cloneAst(parseMarkdown(markdown));

  if (options.enhanceRecommendations !== false) {
    enhanceRecommendationTables(root, extractRecommendations(markdown));
  }

  return root;
}

export function enhanceRecommendationTables(
  root: MarkdownRoot,
  recommendations: Recommendation[],
): MarkdownRoot {
  if (!recommendations.length) {
    return root;
  }

  visit(root, "table", (table: MarkdownNode) => {
    const rows = table.children ?? [];
    const header = rows[0];
    const bodyRows = rows.slice(1);

    if (!header || bodyRows.length < 2 || !isCompatibleRecommendationTable(header)) {
      return;
    }

    const promoted = promoteRecommendedRows(
      bodyRows.map((node, originalIndex) => ({
        node,
        cells: rowTexts(node),
        originalIndex,
      })),
      recommendations,
    );

    if (!promoted.some((row) => row.rank)) {
      return;
    }

    for (const row of promoted) {
      if (row.rank) {
        row.node.data = {
          ...(row.node.data ?? {}),
          recommendationRank: row.rank,
          recommendationClass: `recommendation-row ${rankClass(row.rank)}`,
        };
      }
    }

    table.children = [header, ...promoted.map((row) => row.node)];
  });

  return root;
}

export function extractRecommendations(markdown: string): Recommendation[] {
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

export function nodeText(node: MarkdownNode | undefined): string {
  if (!node) {
    return "";
  }

  if (typeof node.value === "string") {
    return node.value;
  }

  return (node.children ?? []).map(nodeText).join("");
}

export function normalizeLinkHref(href: string): string {
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

export function isHttpUrl(href: string): boolean {
  try {
    const url = new URL(href);
    return url.protocol === "http:" || url.protocol === "https:";
  } catch {
    return false;
  }
}

function cloneAst(root: MarkdownRoot): MarkdownRoot {
  return JSON.parse(JSON.stringify(root)) as MarkdownRoot;
}

function isCompatibleRecommendationTable(header: MarkdownNode): boolean {
  const headers = rowTexts(header).map(normalizeText);
  const joined = headers.join(" ");

  return joined.includes("tienda") && joined.includes("producto") && joined.includes("precio");
}

function rowTexts(row: MarkdownNode): string[] {
  return (row.children ?? []).map(nodeText);
}

function promoteRecommendedRows(
  rows: TableRowMatch[],
  recommendations: Recommendation[],
): TableRowMatch[] {
  if (!recommendations.length || rows.length < 2) {
    return rows;
  }

  const claimedRows = new Set<number>();
  const promoted: TableRowMatch[] = [];

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
  rows: TableRowMatch[],
  recommendation: Recommendation,
  claimedRows: Set<number>,
): { row: TableRowMatch; score: number } | null {
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

function rankClass(rank: RecommendationRank): string {
  if (rank === 1) return "recommendation-gold";
  if (rank === 2) return "recommendation-silver";
  if (rank === 3) return "recommendation-bronze";
  return "recommendation-highlight";
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

function encodeQuery(value: string): string {
  return encodeURIComponent(value).replace(/%20/g, "+");
}
