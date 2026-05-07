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

const parser = unified().use(remarkParse).use(remarkGfm);

export function parseMarkdown(markdown: string): MarkdownRoot {
  return parser.parse(sanitizeLinkUrls(markdown)) as MarkdownRoot;
}

function sanitizeLinkUrls(markdown: string): string {
  // Encode | and spaces inside link URLs so remark-gfm table splitting and
  // CommonMark link parsing don't break on Google Shopping URLs.
  return markdown.replace(/\[([^\]]*)\]\(([^)]*)\)/g, (_match, text, url) => {
    const safeUrl = url.replace(/\|/g, "%7C").replace(/[ \t]+/g, "%20");
    return `[${text}](${safeUrl})`;
  });
}

export function buildMarkdownAst(
  markdown: string,
  options: { enhanceRecommendations?: boolean } = {},
): MarkdownRoot {
  const root = cloneAst(parseMarkdown(markdown));

  if (options.enhanceRecommendations !== false) {
    enhanceRecommendationTables(root);
  }

  return root;
}

export function enhanceRecommendationTables(root: MarkdownRoot): MarkdownRoot {
  visit(root, "table", (table: MarkdownNode) => {
    const rows = table.children ?? [];
    const header = rows[0];
    const bodyRows = rows.slice(1);

    if (!header || bodyRows.length < 2 || !isCompatibleRecommendationTable(header)) {
      return;
    }

    for (const row of bodyRows) {
      const cells = rowTexts(row);
      const firstCell = (cells[0] || "").trim().replace(/^#/, "");
      const rank = parseInt(firstCell, 10);

      if (!isNaN(rank) && rank >= 1 && rank <= 3) {
        row.data = {
          ...(row.data ?? {}),
          recommendationRank: rank as RecommendationRank,
          recommendationClass: `recommendation-row ${rankClass(rank as RecommendationRank)}`,
        };
      }
    }
  });

  return root;
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

function rankClass(rank: RecommendationRank): string {
  if (rank === 1) return "recommendation-gold";
  if (rank === 2) return "recommendation-silver";
  if (rank === 3) return "recommendation-bronze";
  return "recommendation-highlight";
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

function encodeQuery(value: string): string {
  return encodeURIComponent(value).replace(/%20/g, "+");
}
