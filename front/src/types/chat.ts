import type { HttpAgent } from "@ag-ui/client";
import type { ErrorDetails } from "../lib/errors";

export type CardField = { label: string; value: string };

export type PendingConfirm = {
  interruptId: string;
  toolCallId: string;
  message: string;
};

export type McpConfirm = { id: string; message: string };

export type ChatEntry =
  | { kind: "user"; id: string; text: string }
  | { kind: "step"; id: string; name: string; running: boolean }
  | { kind: "reasoning"; id: string; text: string; streaming: boolean }
  | { kind: "assistant"; id: string; text: string; streaming: boolean;
      status?: "generating" | "cancelled" | "completed" | "failed"; activeGenerationId?: string | null }
  | { kind: "tool"; id: string; name: string; args: string; result: string }
  | { kind: "card"; id: string; title: string; subtitle: string; fields: CardField[] };

export type ConversationDetail = {
  id: string;
  entries: ChatEntry[];
  messages: HttpAgent["messages"];
  status: string;
  runId?: string;
  cancelRequested?: boolean;
  confirm: PendingConfirm | null;
  mcpConfirm?: McpConfirm | null;
  error: string;
  errorInfo?: ErrorDetails | null;
};
