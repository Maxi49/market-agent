import { Mastra } from "@mastra/core/mastra";
import { LibSQLStore } from "@mastra/libsql";
import { marketShoppingAgent } from "./agents/marketShoppingAgent";

export const mastra = new Mastra({
  storage: new LibSQLStore({
    id: "demo-agent-storage",
    url: "file:/Users/maxigimenez/Desktop/dev/market-agent/demo-agent/mastra-memory.db",
  }),
  agents: { marketShoppingAgent },
});
