<script lang="ts">
  import { isHttpUrl, nodeText, normalizeLinkHref, type MarkdownNode } from "./markdownAst";
  import MarkdownNodeView from "./MarkdownNode.svelte";

  export let node: MarkdownNode;
  export let streaming = false;
  export let path = "0";

  type Token = {
    key: string;
    text: string;
    whitespace: boolean;
    delay: number;
  };

  // Per-node delay registry: once a token gets a delay assigned it never changes,
  // so re-renders don't shift the CSS variable and re-trigger the animation.
  const tokenDelays = new Map<string, number>();

  $: tagName = node.type === "heading" ? (`h${node.depth ?? 2}` as keyof HTMLElementTagNameMap) : "div";
  $: textTokens = tokenizeText(node.value ?? "", path, streaming);
  $: tableRows = node.children ?? [];
  $: tableHeader = tableRows[0];
  $: tableBody = tableRows.slice(1);
  $: hasRecommendationRows = tableBody.some((row) => row.data?.recommendationRank);
  $: listItemChildren =
    node.type === "listItem" &&
    node.children?.length === 1 &&
    node.children[0]?.type === "paragraph"
      ? node.children[0].children
      : node.children;

  function tokenizeText(value: string, tokenPath: string, isStreaming: boolean): Token[] {
    const parts = value.match(/\s+|\S+/g) ?? [];
    let offset = 0;
    // Counts only the tokens that are NEW in this particular render batch,
    // so the stagger resets per chunk instead of accumulating globally.
    let batchIndex = 0;

    return parts.map((text) => {
      const key = `${tokenPath}:${offset}`;
      offset += text.length;
      const isWhitespace = /^\s+$/.test(text);

      if (isStreaming && !isWhitespace && !tokenDelays.has(key)) {
        // Assign a delay only once; subsequent renders retrieve the same value
        // so the CSS variable never changes on existing spans.
        tokenDelays.set(key, Math.min(batchIndex * 9, 72));
        batchIndex++;
      }

      return {
        key,
        text,
        whitespace: isWhitespace,
        delay: tokenDelays.get(key) ?? 0,
      };
    });
  }

  function tokenClass(token: Token): string | null {
    if (!streaming || token.whitespace) {
      return null;
    }
    return "stream-token-enter";
  }

  function renderChildren(children: MarkdownNode[] | undefined, childPath: string): MarkdownNode[] {
    return children ?? [];
  }

  function childKey(child: MarkdownNode, childPath: string, index: number): string {
    return `${childPath}.${index}.${child.type}`;
  }

  function tableCellTag(isHeader: boolean): "th" | "td" {
    return isHeader ? "th" : "td";
  }
</script>

{#if node.type === "root"}
  {#each renderChildren(node.children, path) as child, index (childKey(child, path, index))}
    <MarkdownNodeView
      node={child}
      streaming={streaming}
      path={`${path}.${index}`}
    />
  {/each}
{:else if node.type === "paragraph"}
  <p>
    {#each renderChildren(node.children, path) as child, index (childKey(child, path, index))}
      <MarkdownNodeView
        node={child}
        streaming={streaming}
        path={`${path}.${index}`}
      />
    {/each}
  </p>
{:else if node.type === "heading"}
  <svelte:element this={tagName}>
    {#each renderChildren(node.children, path) as child, index (childKey(child, path, index))}
      <MarkdownNodeView
        node={child}
        streaming={streaming}
        path={`${path}.${index}`}
      />
    {/each}
  </svelte:element>
{:else if node.type === "strong"}
  <strong>
    {#each renderChildren(node.children, path) as child, index (childKey(child, path, index))}
      <MarkdownNodeView
        node={child}
        streaming={streaming}
        path={`${path}.${index}`}
      />
    {/each}
  </strong>
{:else if node.type === "emphasis"}
  <em>
    {#each renderChildren(node.children, path) as child, index (childKey(child, path, index))}
      <MarkdownNodeView
        node={child}
        streaming={streaming}
        path={`${path}.${index}`}
      />
    {/each}
  </em>
{:else if node.type === "delete"}
  <del>
    {#each renderChildren(node.children, path) as child, index (childKey(child, path, index))}
      <MarkdownNodeView
        node={child}
        streaming={streaming}
        path={`${path}.${index}`}
      />
    {/each}
  </del>
{:else if node.type === "list"}
  {#if node.ordered}
    <ol start={node.start ?? undefined}>
      {#each renderChildren(node.children, path) as child, index (childKey(child, path, index))}
        <MarkdownNodeView
          node={child}
          streaming={streaming}
          path={`${path}.${index}`}
        />
      {/each}
    </ol>
  {:else}
    <ul>
      {#each renderChildren(node.children, path) as child, index (childKey(child, path, index))}
        <MarkdownNodeView
          node={child}
          streaming={streaming}
          path={`${path}.${index}`}
        />
      {/each}
    </ul>
  {/if}
{:else if node.type === "listItem"}
  <li>
    {#each renderChildren(listItemChildren, path) as child, index (childKey(child, path, index))}
      <MarkdownNodeView
        node={child}
        streaming={streaming}
        path={`${path}.${index}`}
      />
    {/each}
  </li>
{:else if node.type === "link" && node.url && isHttpUrl(node.url)}
  <a class="markdown-link-button" href={normalizeLinkHref(node.url)} target="_blank" rel="noreferrer">Ver</a>
{:else if node.type === "break"}
  <br />
{:else if node.type === "thematicBreak"}
  <hr />
{:else if node.type === "table"}
  <table class:recommendation-table={hasRecommendationRows}>
    {#if tableHeader}
      <thead>
        <tr>
          {#each tableHeader.children ?? [] as cell, cellIndex (childKey(cell, `${path}.head`, cellIndex))}
            <svelte:element this={tableCellTag(true)}>
              {#each renderChildren(cell.children, `${path}.head.${cellIndex}`) as child, childIndex (childKey(child, `${path}.head.${cellIndex}`, childIndex))}
                <MarkdownNodeView
                  node={child}
                  streaming={false}
                  path={`${path}.head.${cellIndex}.${childIndex}`}
                />
              {/each}
            </svelte:element>
          {/each}
        </tr>
      </thead>
    {/if}
    <tbody>
      {#each tableBody as row, rowIndex (childKey(row, `${path}.body`, rowIndex))}
        <tr class={row.data?.recommendationClass ?? undefined}>
          {#each row.children ?? [] as cell, cellIndex (childKey(cell, `${path}.body.${rowIndex}`, cellIndex))}
            <svelte:element this={tableCellTag(false)}>
              {#if row.data?.recommendationRank && cellIndex === 0}
                <span class="recommendation-badge">#{row.data.recommendationRank}</span>
                <span class="recommendation-original-rank">
                  {#each renderChildren(cell.children, `${path}.body.${rowIndex}.${cellIndex}`) as child, childIndex (childKey(child, `${path}.body.${rowIndex}.${cellIndex}`, childIndex))}
                    <MarkdownNodeView
                      node={child}
                      streaming={false}
                      path={`${path}.body.${rowIndex}.${cellIndex}.${childIndex}`}
                    />
                  {/each}
                </span>
              {:else}
                {#each renderChildren(cell.children, `${path}.body.${rowIndex}.${cellIndex}`) as child, childIndex (childKey(child, `${path}.body.${rowIndex}.${cellIndex}`, childIndex))}
                  <MarkdownNodeView
                    node={child}
                    streaming={false}
                    path={`${path}.body.${rowIndex}.${cellIndex}.${childIndex}`}
                  />
                {/each}
              {/if}
            </svelte:element>
          {/each}
        </tr>
      {/each}
    </tbody>
  </table>
{:else if node.value}
  {#each textTokens as token (token.key)}
    {@const className = tokenClass(token)}
    {#if className}
      <span
        class={className}
        style={`--stream-token-delay: ${token.delay}ms`}
      >{token.text}</span>
    {:else}
      {token.text}
    {/if}
  {/each}
{:else}
  {#each renderChildren(node.children, path) as child, index (childKey(child, path, index))}
    <MarkdownNodeView
      node={child}
      streaming={streaming}
      path={`${path}.${index}`}
    />
  {/each}
{/if}
