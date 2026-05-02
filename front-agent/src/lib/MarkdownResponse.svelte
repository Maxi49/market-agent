<script lang="ts">
  import { buildMarkdownAst } from "./markdownAst";
  import MarkdownNode from "./MarkdownNode.svelte";

  export let markdown = "";
  export let streaming = false;
  export let enhanceRecommendations = true;

  $: ast = buildMarkdownAst(markdown, { enhanceRecommendations });
</script>

<MarkdownNode node={ast} {streaming} path="message" />
