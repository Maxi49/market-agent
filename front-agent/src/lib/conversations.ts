export type ChatRole = "user" | "assistant";

export type ChatMessage = {
  id: string;
  role: ChatRole;
  content: string;
  createdAt: number;
};

export type Conversation = {
  id: string;
  title: string;
  messages: ChatMessage[];
  createdAt: number;
  updatedAt: number;
};

export const CONVERSATION_STORAGE_KEY = "front-agent.conversations.v1";

export function createConversation(createId = defaultId): Conversation {
  const now = Date.now();
  const id = createId();

  return {
    id,
    title: "Nueva consulta",
    messages: [],
    createdAt: now,
    updatedAt: now,
  };
}

export function hydrateConversations(
  rawValue: string | null,
  createId = defaultId,
): Conversation[] {
  if (!rawValue) {
    return [createConversation(createId)];
  }

  try {
    const parsed = JSON.parse(rawValue) as unknown;
    if (!Array.isArray(parsed)) {
      return [createConversation(createId)];
    }

    const conversations = parsed.filter(isConversation);
    return conversations.length > 0 ? conversations : [createConversation(createId)];
  } catch {
    return [createConversation(createId)];
  }
}

export function summarizeTitle(prompt: string): string {
  const words = prompt.trim().replace(/\s+/g, " ").split(" ").filter(Boolean);
  if (words.length === 0) {
    return "Nueva consulta";
  }

  return words.slice(0, 5).join(" ");
}

export function createMessage(role: ChatRole, content: string, createId = defaultId): ChatMessage {
  return {
    id: createId(),
    role,
    content,
    createdAt: Date.now(),
  };
}

function isConversation(value: unknown): value is Conversation {
  if (!value || typeof value !== "object") {
    return false;
  }

  const candidate = value as Partial<Conversation>;
  return (
    typeof candidate.id === "string" &&
    candidate.id.length > 0 &&
    typeof candidate.title === "string" &&
    Array.isArray(candidate.messages) &&
    typeof candidate.createdAt === "number" &&
    typeof candidate.updatedAt === "number"
  );
}

function defaultId(): string {
  return crypto.randomUUID();
}
