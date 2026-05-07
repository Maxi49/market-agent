<script lang="ts">
  import {
    Bot,
    Check,
    CircleDot,
    Loader2,
    PanelLeft,
    Plus,
    SendHorizontal,
    Sparkles,
    SquareTerminal,
  } from "@lucide/svelte";
  import { onDestroy, onMount, tick } from "svelte";
  import {
    CONVERSATION_STORAGE_KEY,
    createConversation,
    createMessage,
    hydrateConversations,
    summarizeTitle,
    type ChatMessage,
    type Conversation,
  } from "./lib/conversations";
  import { streamAgentResponse } from "./lib/mastraClient";
  import {
    appendTextDelta,
    compactTraceSteps,
    extractSafeContent,
    hasUnsafeAgentOutput,
    mergeTraceStep,
    type TraceStep,
  } from "./lib/mastraStream";
  import MarkdownResponse from "./lib/MarkdownResponse.svelte";

  type UiMessage = ChatMessage & {
    traces?: TraceStep[];
    pending?: boolean;
    error?: string;
  };

  type UiConversation = Omit<Conversation, "messages"> & {
    messages: UiMessage[];
  };

  const agentId = import.meta.env.VITE_AGENT_ID || "market-shopping-agent";
  const mastraBaseUrl = import.meta.env.VITE_MASTRA_BASE_URL || "";
  const resourceId = "front-agent-local-user";
  const suggestions = [
    "Shampoo y cuidado personal",
    "Notebooks para estudiantes < USD 500",
    "Smart TVs: variacion de precios ultimo mes",
    "Comparar iPhone 15 en tiendas",
  ];
  const bottomThreshold = 96;

  let conversations: UiConversation[] = [];
  let activeConversationId = "";
  let prompt = "";
  let isSending = false;
  let sidebarOpen = false;
  let storageReady = false;
  let chatScroll: HTMLElement | null = null;
  let messagesList: HTMLElement | null = null;
  let observedMessagesList: HTMLElement | null = null;
  let resizeObserver: ResizeObserver | null = null;
  let stickToBottom = true;
  let userPausedAutoScroll = false;
  let scrollFrame = 0;
  let lastScrollTop = 0;

  $: activeConversation =
    conversations.find((conversation) => conversation.id === activeConversationId) ??
    conversations[0];

  $: if (storageReady) {
    localStorage.setItem(CONVERSATION_STORAGE_KEY, JSON.stringify(conversations));
  }

  onMount(() => {
    const hydrated = hydrateConversations(localStorage.getItem(CONVERSATION_STORAGE_KEY));
    conversations = hydrated as UiConversation[];
    activeConversationId = conversations[0]?.id ?? "";
    storageReady = true;
    scheduleChatScroll();
  });

  onDestroy(() => {
    cancelAnimationFrame(scrollFrame);
    resizeObserver?.disconnect();
  });

  $: if (messagesList !== observedMessagesList) {
    observeMessagesList(messagesList);
  }

  function startConversation() {
    const conversation = createConversation() as UiConversation;
    conversations = [conversation, ...conversations];
    activeConversationId = conversation.id;
    prompt = "";
    sidebarOpen = false;
    stickToBottom = true;
    userPausedAutoScroll = false;
    scheduleChatScroll();
  }

  function selectConversation(id: string) {
    activeConversationId = id;
    sidebarOpen = false;
    stickToBottom = true;
    userPausedAutoScroll = false;
    scheduleChatScroll();
  }

  async function submitPrompt(value = prompt) {
    const trimmed = value.trim();
    if (!trimmed || isSending || !activeConversation) {
      return;
    }

    const userMessage = createMessage("user", trimmed);
    const assistantMessage: UiMessage = {
      ...createMessage("assistant", ""),
      traces: [{ id: "reasoning-start", label: "Razonamiento interno", status: "running" }],
      pending: true,
    };

    const title =
      activeConversation.messages.length === 0 ? summarizeTitle(trimmed) : activeConversation.title;

    updateActiveConversation({
      title,
      updatedAt: Date.now(),
      messages: [...activeConversation.messages, userMessage, assistantMessage],
    });
    stickToBottom = true;
    userPausedAutoScroll = false;
    scheduleChatScroll();

    prompt = "";
    isSending = true;
    let needsResponseSeparator = false;

    try {
      await streamAgentResponse(
        {
          prompt: trimmed,
          threadId: activeConversation.id,
          resourceId,
          agentId,
          baseUrl: mastraBaseUrl,
          maxSteps: 5,
        },
        {
          onTextDelta: (delta) => {
            if (!delta) {
              return;
            }

            patchMessage(assistantMessage.id, (message) => ({
              ...message,
              content: appendTextDelta(message.content, delta, needsResponseSeparator),
            }));
            needsResponseSeparator = false;
          },
          onTrace: (trace) => {
            needsResponseSeparator = true;
            patchMessage(assistantMessage.id, (message) => ({
              ...message,
              traces: mergeTraceStep(message.traces ?? [], trace),
            }));
          },
          onContentReset: () => {
            needsResponseSeparator = false;
            patchMessage(assistantMessage.id, (message) => ({
              ...message,
              content: "",
            }));
          },
        },
      );

      patchMessage(assistantMessage.id, (message) => {
        const isUnsafe = hasUnsafeAgentOutput(message.content);
        const finalContent = isUnsafe ? extractSafeContent(message.content) : message.content;
        const unrecoverable = isUnsafe && !finalContent;

        return {
          ...message,
          content: finalContent,
          pending: false,
          error: unrecoverable
            ? "No se pudo obtener una respuesta válida. Intentá reformular la consulta."
            : message.error,
          traces: unrecoverable
            ? (message.traces ?? []).map((trace) =>
                trace.status === "running" ? { ...trace, status: "error" as const } : trace,
              )
            : [
                ...(message.traces ?? []).map((trace) =>
                  trace.status === "running" ? { ...trace, status: "done" as const } : trace,
                ),
                { id: "answer-finished", label: "Respuesta lista", status: "done" },
              ],
        };
      });
    } catch (error) {
      patchMessage(assistantMessage.id, (message) => ({
        ...message,
        pending: false,
        error: error instanceof Error ? error.message : "No se pudo conectar con Mastra.",
        traces: [
          ...(message.traces ?? []).map((trace) =>
            trace.status === "running" ? { ...trace, status: "error" as const } : trace,
          ),
        ],
      }));
    } finally {
      isSending = false;
    }
  }

  function updateActiveConversation(update: Partial<UiConversation>) {
    conversations = conversations.map((conversation) =>
      conversation.id === activeConversationId ? { ...conversation, ...update } : conversation,
    );
  }

  function patchMessage(messageId: string, patch: (message: UiMessage) => UiMessage) {
    conversations = conversations.map((conversation) => ({
      ...conversation,
      updatedAt:
        conversation.id === activeConversationId ? Date.now() : conversation.updatedAt,
      messages: conversation.messages.map((message) =>
        message.id === messageId ? patch(message) : message,
      ),
    }));
  }

  function handleChatScroll() {
    if (!chatScroll) {
      return;
    }

    const bottomDistance = distanceFromBottom(chatScroll);
    const scrollingUp = chatScroll.scrollTop < lastScrollTop;
    lastScrollTop = chatScroll.scrollTop;

    if (scrollingUp && bottomDistance > 4) {
      stickToBottom = false;
      userPausedAutoScroll = true;
      return;
    }

    if (bottomDistance <= bottomThreshold) {
      stickToBottom = true;
      userPausedAutoScroll = false;
    }
  }

  function handleChatWheel(event: WheelEvent) {
    if (event.deltaY < 0) {
      pauseAutoScroll();
      return;
    }

    if (chatScroll && distanceFromBottom(chatScroll) <= bottomThreshold) {
      stickToBottom = true;
      userPausedAutoScroll = false;
    }
  }

  function handleComposerWheel(event: WheelEvent) {
    if (!chatScroll) {
      return;
    }

    pauseAutoScroll();
    chatScroll.scrollTop += event.deltaY;
    lastScrollTop = chatScroll.scrollTop;

    if (distanceFromBottom(chatScroll) <= bottomThreshold) {
      stickToBottom = true;
      userPausedAutoScroll = false;
    }
  }

  function pauseAutoScroll() {
    stickToBottom = false;
    userPausedAutoScroll = true;
    cancelAnimationFrame(scrollFrame);
  }

  function distanceFromBottom(element: HTMLElement) {
    return element.scrollHeight - element.scrollTop - element.clientHeight;
  }

  async function scheduleChatScroll() {
    await tick();
    requestChatScroll();
  }

  function requestChatScroll() {
    if (!chatScroll || !stickToBottom || userPausedAutoScroll) {
      return;
    }

    cancelAnimationFrame(scrollFrame);
    scrollFrame = requestAnimationFrame(() => {
      if (!chatScroll || !stickToBottom || userPausedAutoScroll) {
        return;
      }

      chatScroll.scrollTop = chatScroll.scrollHeight;
      lastScrollTop = chatScroll.scrollTop;
    });
  }

  function observeMessagesList(element: HTMLElement | null) {
    resizeObserver?.disconnect();
    observedMessagesList = element;

    if (!element) {
      return;
    }

    resizeObserver = new ResizeObserver(() => {
      requestChatScroll();
    });
    resizeObserver.observe(element);
    requestChatScroll();
  }

  function handleKeydown(event: KeyboardEvent) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void submitPrompt();
    }
  }

  function statusIcon(status: TraceStep["status"]) {
    if (status === "running") {
      return SquareTerminal;
    }

    if (status === "error") {
      return CircleDot;
    }

    return Check;
  }
</script>

<svelte:head>
  <title>Comprate Algo</title>
</svelte:head>

<div class:sidebar-open={sidebarOpen} class="app-shell">
  <aside class="sidebar">
    <div class="brand-row">
      <div class="brand-mark">C</div>
      <strong>CompraGPT</strong>
    </div>

    <div class="sidebar-section">
      <p class="sidebar-kicker">Conversaciones</p>
      <div class="conversation-list">
        {#each conversations as conversation}
          <button
            class:active={conversation.id === activeConversationId}
            class="conversation-item"
            type="button"
            on:click={() => selectConversation(conversation.id)}
          >
            <span></span>
            {conversation.title}
          </button>
        {/each}
      </div>
    </div>

    <button class="new-chat" type="button" on:click={startConversation}>
      <Plus size={18} />
      Nueva consulta
    </button>
  </aside>

  <main class="workspace">
    <header class="topbar">
      <button class="icon-button menu-button" type="button" on:click={() => (sidebarOpen = true)}>
        <PanelLeft size={18} />
      </button>
      <div class="title-group">
        <h1>Comprate Algo</h1>
        <span class="local-badge">localhost:8000</span>
      </div>
      <span class="demo-badge">DEMO</span>
    </header>

    <section
      bind:this={chatScroll}
      class="chat-scroll"
      aria-live="polite"
      on:scroll={handleChatScroll}
      on:wheel={handleChatWheel}
    >
      {#if activeConversation?.messages.length}
        <div bind:this={messagesList} class="messages">
          {#each activeConversation.messages as message}
            {#if message.role === "user"}
              <article class="message user-message">
                <div class="query-pill">{message.content}</div>
              </article>
            {:else}
              <article class="message assistant-message">
                <div class="agent-label">
                  <div class="agent-avatar">E</div>
                  <strong>CompraGPT</strong>
                  <span>DEMO</span>
                </div>

                {#if message.traces?.length}
                  <div class="trace-list">
                    {#each compactTraceSteps(message.traces) as trace}
                      <div class:error={trace.status === "error"} class:running={trace.status === "running"} class="trace-row">
                        <svelte:component this={statusIcon(trace.status)} size={15} />
                        <span class:running-text={trace.status === "running"}>{trace.label}</span>
                        {#if trace.detail}
                          <small>{trace.detail}</small>
                        {/if}
                      </div>
                    {/each}
                  </div>
                {/if}

                {#if message.content && !hasUnsafeAgentOutput(message.content)}
                  <div class="assistant-copy">
                    <MarkdownResponse
                      markdown={message.content}
                      streaming={Boolean(message.pending)}
                      enhanceRecommendations={!message.pending}
                    />
                  </div>
                {/if}

                {#if message.error}
                  <div class="error-box">
                    <strong>No pude hablar con Mastra.</strong>
                    <span>{message.error}</span>
                  </div>
                {/if}
              </article>
            {/if}
          {/each}
        </div>
      {:else}
        <div class="empty-state">
          <div class="empty-icon"><Bot size={26} /></div>
          <h2>Consultá precios reales con el agente</h2>
          <p>Buscá productos, compará tiendas argentinas y pedí contexto de precio.</p>
        </div>
      {/if}
    </section>

    <footer class="composer-area" on:wheel={handleComposerWheel}>
      <div class="suggestions">
        {#each suggestions as suggestion}
          <button type="button" on:click={() => submitPrompt(suggestion)} disabled={isSending}>
            {suggestion}
          </button>
        {/each}
      </div>

      <form class="composer" on:submit|preventDefault={() => submitPrompt()}>
        <SquareTerminal size={18} />
        <textarea
          bind:value={prompt}
          on:keydown={handleKeydown}
          placeholder="Consultá productos, precios, comparativas... (modo DEMO)"
          rows="1"
        ></textarea>
        <button class="send-button" type="submit" disabled={isSending || !prompt.trim()}>
          {#if isSending}
            <Loader2 size={18} />
          {:else}
            <SendHorizontal size={18} />
          {/if}
        </button>
      </form>

      <div class="composer-hint">
        <span>Enter para enviar</span>
        <span>Shift+Enter nueva linea</span>
        <strong><Sparkles size={12} /> Modo DEMO - datos simulados por el agente</strong>
      </div>
    </footer>
  </main>
</div>

{#if sidebarOpen}
  <button class="backdrop" type="button" aria-label="Cerrar conversaciones" on:click={() => (sidebarOpen = false)}></button>
{/if}
