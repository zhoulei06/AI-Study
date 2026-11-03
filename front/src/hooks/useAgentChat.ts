import { HttpAgent, type AgentSubscriber } from "@ag-ui/client";
import { useCallback, useRef, useState, type RefObject } from "react";
import { agentUrl, historyRequest } from "../lib/conversationApi";
import { readError, formatError, errorMessage, checkedFetch } from "../lib/errors";
import type { ChatEntry, ConversationDetail, McpConfirm, PendingConfirm } from "../types/chat";

const EXAMPLES = [
  "现在几点了？北京天气怎么样？再帮我算 23*17",
  "记一条笔记，标题是 Loop，正文是 Agent 会反复调用工具",
  "删除笔记 n3",
  "读取 notes://catalog，告诉我有哪些标题",
];

type UseAgentChatOptions = {
  onHistoryUpdated: () => Promise<unknown>;
  inputRef: RefObject<HTMLTextAreaElement | null>;
};

export function useAgentChat({ onHistoryUpdated, inputRef }: UseAgentChatOptions) {
  const [agent, setAgent] = useState(() => new HttpAgent({ url: agentUrl, fetch: checkedFetch }));
  const [entries, setEntries] = useState<ChatEntry[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [confirm, setConfirm] = useState<PendingConfirm | null>(null);
  const [mcpConfirm, setMcpConfirm] = useState<McpConfirm | null>(null);
  const [remoteRunning, setRemoteRunning] = useState(false);
  const [status, setStatus] = useState("idle");
  const [runId, setRunId] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const inFlight = useRef(false);
  const currentAgent = useRef(agent);
  const activeGeneration = useRef<string | null>(null);

  const applyDetail = useCallback((detail: ConversationDetail) => {
    const restored = new HttpAgent({ url: agentUrl, fetch: checkedFetch, threadId: detail.id, initialMessages: detail.messages });
    currentAgent.current = restored;
    setAgent(restored);
    setEntries(detail.entries);
    setConfirm(detail.confirm);
    setMcpConfirm(detail.mcpConfirm ?? null);
    setRemoteRunning(detail.status === "running");
    setStatus(detail.status);
    setRunId(detail.runId ?? null);
    setCancelling(detail.status === "running" && !!detail.cancelRequested);
    const structured = readError(detail.errorInfo);
    setError(structured ? formatError(structured) : detail.error ? "上次运行失败，请重新发起请求或联系维护人员。" : "");
  }, []);

  const restoreConversation = useCallback((detail: ConversationDetail) => {
    applyDetail(detail);
    setDraft("");
  }, [applyDetail]);

  const listen = useCallback((generationId: string): AgentSubscriber => {
    const subscriber: AgentSubscriber = {
    onRunStartedEvent({ event }) {
      setRunId(event.runId);
      setStatus("running");
    },
    onTextMessageStartEvent({ event }) {
      setEntries((prev) => prev.some((item) => item.id === event.messageId)
        ? prev.map((item) => item.kind === "assistant" && item.id === event.messageId
          ? { ...item, streaming: true, status: "generating", activeGenerationId: generationId } : item)
        : [...prev, { kind: "assistant", id: event.messageId, text: "", streaming: true,
          status: "generating", activeGenerationId: generationId }]);
    },
    onTextMessageEndEvent({ event }) {
      setEntries((prev) => prev.map((item) => item.kind === "assistant" && item.id === event.messageId
        ? { ...item, streaming: false } : item));
    },
    onTextMessageContentEvent({ event }) {
      setEntries((prev) => prev.map((item) => item.kind === "assistant" && item.id === event.messageId
        ? { ...item, text: item.text + event.delta } : item));
    },
    onStepStartedEvent({ event }) {
      setEntries((prev) => [...prev, { kind: "step", id: crypto.randomUUID(), name: event.stepName, running: true }]);
    },
    onStepFinishedEvent({ event }) {
      setEntries((prev) => {
        const index = lastRunningStep(prev, event.stepName);
        return index < 0 ? prev : prev.map((item, i) => i === index && item.kind === "step"
          ? { ...item, running: false } : item);
      });
    },
    onReasoningMessageContentEvent({ event }) {
      if (!event.delta) return;
      setEntries((prev) => {
        const exists = prev.some((item) => item.kind === "reasoning" && item.id === event.messageId);
        if (!exists) return [...prev, { kind: "reasoning", id: event.messageId, text: event.delta, streaming: true }];
        return prev.map((item) => item.kind === "reasoning" && item.id === event.messageId
          ? { ...item, text: item.text + event.delta } : item);
      });
    },
    onReasoningMessageEndEvent({ event }) {
      setEntries((prev) => prev.map((item) => item.kind === "reasoning" && item.id === event.messageId
        ? { ...item, streaming: false } : item));
    },
    onToolCallStartEvent({ event }) {
      setEntries((prev) => prev.some((item) => item.id === event.toolCallId)
        ? prev.map((item) => item.kind === "tool" && item.id === event.toolCallId ? { ...item, args: "" } : item) :
        [...prev, { kind: "tool", id: event.toolCallId, name: event.toolCallName, args: "", result: "" }]);
    },
    onToolCallArgsEvent({ event }) {
      setEntries((prev) => prev.map((item) => item.kind === "tool" && item.id === event.toolCallId
        ? { ...item, args: item.args + event.delta } : item));
    },
    onToolCallResultEvent({ event }) {
      setEntries((prev) => prev.map((item) => item.kind === "tool" && item.id === event.toolCallId
        ? { ...item, result: asText(event.content) } : item));
    },
    onActivitySnapshotEvent({ event }) {
      if (event.activityType === "mcp-confirm") {
        const record = event.content;
        if (record && typeof record === "object" && "id" in record && "message" in record &&
          typeof record.id === "string" && typeof record.message === "string") {
          setMcpConfirm({ id: record.id, message: record.message });
        }
        return;
      }
      if (event.activityType !== "card") return;
      const card = asCard(event.content);
      if (card) setEntries((prev) => [...prev, { kind: "card", id: event.messageId, ...card }]);
    },
    onRunFinishedEvent(params) {
      if (params.outcome === "interrupt") {
        const item = params.interrupts[0];
        if (item?.toolCallId) {
          setConfirm({ interruptId: item.id, toolCallId: item.toolCallId, message: item.message ?? "需要确认这次调用" });
          return;
        }
      }
      setConfirm(null);
    },
    onRunErrorEvent({ event }) {
      const error = readError(event.metadata?.error);
      setError(error ? formatError(error) : "运行失败，请重新加载会话。");
    },
    onRunFailed({ error: runError }) {
      if (runError.name !== "AbortError") setError(errorMessage(runError));
    },
    };
    // Every callback belongs to one request. A late callback from a previous
    // generation must not mutate the current conversation or answer.
    return Object.fromEntries(Object.entries(subscriber).map(([name, callback]) => [name,
      (...args: unknown[]) => {
        if (activeGeneration.current === generationId) return callback(...args);
        return undefined;
      },
    ])) as AgentSubscriber;
  }, []);

  const execute = useCallback(async (
    parameters?: Parameters<HttpAgent["runAgent"]>[0],
  ) => {
    if (inFlight.current) return;
    inFlight.current = true;
    setRunId(null);
    setCancelling(false);
    const generationId = crypto.randomUUID();
    activeGeneration.current = generationId;
    let requestError = "";
    try {
      await agent.runAgent({ ...parameters, runId: generationId }, listen(generationId));
    } catch (cause) {
      if (!(cause instanceof Error) || cause.name !== "AbortError") {
        requestError = errorMessage(cause);
        setError(requestError);
      }
    } finally {
      setMcpConfirm(null);
      setEntries((prev) => settleEntries(prev));
      inputRef.current?.focus();
      try {
        const detail = await historyRequest<ConversationDetail>(`/${encodeURIComponent(agent.threadId)}`);
        if (currentAgent.current === agent) applyDetail(detail);
        if (requestError) setError(requestError);
        await onHistoryUpdated();
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : "会话列表刷新失败");
      } finally {
        inFlight.current = false;
        if (activeGeneration.current === generationId) activeGeneration.current = null;
        setBusy(false);
      }
    }
  }, [agent, applyDetail, inputRef, listen, onHistoryUpdated]);

  const send = useCallback(async (text: string, activeId: string | null, loading: boolean) => {
    const content = text.trim();
    if (!content || inFlight.current || busy || confirm || loading || remoteRunning || !activeId) return;
    const id = crypto.randomUUID();
    setEntries((prev) => [...prev, { kind: "user", id, text: content }]);
    setDraft("");
    setError("");
    setBusy(true);
    agent.addMessage({ id, role: "user", content });
    await execute();
  }, [agent, busy, confirm, execute, remoteRunning]);

  const answer = useCallback(async (approved: boolean, loading: boolean) => {
    if (!confirm || inFlight.current || busy || loading || remoteRunning) return;
    const current = confirm;
    setConfirm(null);
    setBusy(true);
    setError("");
    await execute({
      resume: [{
        interruptId: current.interruptId,
        status: approved ? "resolved" : "cancelled",
        payload: { approved, toolCallId: current.toolCallId },
      }],
    });
  }, [busy, confirm, execute, remoteRunning]);

  const resume = useCallback(async (loading: boolean) => {
    if (inFlight.current || busy || loading || remoteRunning || !runId ||
      (status !== "cancelled" && status !== "interrupted")) return;
    setBusy(true);
    setError("");
    await execute({ forwardedProps: { action: "continue", resumeOf: runId } });
  }, [busy, execute, remoteRunning, runId, status]);

  const stop = useCallback(async () => {
    if (cancelling || !runId || (!busy && !remoteRunning)) return;
    setCancelling(true);
    setError("");
    try {
      const detail = await historyRequest<ConversationDetail>(
        `/${encodeURIComponent(agent.threadId)}/cancel`, "POST", { runId },
      );
      // The live stream owns local updates. For restored runs, poll until the
      // worker reaches a safe boundary and saves its terminal state.
      if (currentAgent.current === agent && !busy) applyDetail(detail);
    } catch (cause) {
      if (currentAgent.current !== agent) return;
      setCancelling(false);
      setError(cause instanceof Error ? cause.message : "取消失败，请刷新后重试");
    }
  }, [agent, applyDetail, busy, cancelling, remoteRunning, runId]);

  const answerMcp = useCallback(async (action: "accept" | "decline") => {
    if (!mcpConfirm) return;
    const current = mcpConfirm;
    setMcpConfirm(null);
    try {
      await checkedFetch(`${agentUrl}/elicit`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ id: current.id, action }),
      });
    } catch (cause) {
      setError(errorMessage(cause, "确认失败，请重新加载会话。"));
    }
  }, [mcpConfirm]);

  return {
    agent, entries, setEntries, draft, setDraft, busy, setBusy, error, setError,
    confirm, mcpConfirm, remoteRunning, setRemoteRunning, restoreConversation,
    status, cancelling, canCancel: !!runId && (busy || remoteRunning),
    canContinue: !!runId && (status === "cancelled" || status === "interrupted"),
    send, answer, answerMcp, stop, resume, examples: EXAMPLES,
  };
}

function settleEntries(entries: ChatEntry[]) {
  return entries.map((item): ChatEntry | null => {
    if (item.kind === "assistant") return { ...item, streaming: false };
    if (item.kind === "step") return { ...item, running: false };
    if (item.kind === "reasoning") return item.text.trim() ? { ...item, streaming: false } : null;
    return item;
  }).filter((item): item is ChatEntry => item !== null);
}

function lastRunningStep(entries: ChatEntry[], name: string) {
  for (let index = entries.length - 1; index >= 0; index -= 1) {
    const item = entries[index];
    if (item.kind === "step" && item.name === name && item.running) return index;
  }
  return -1;
}

function asCard(content: unknown): { title: string; subtitle: string; fields: { label: string; value: string }[] } | null {
  if (!content || typeof content !== "object") return null;
  const record = content as Record<string, unknown>;
  if (typeof record.title !== "string" || !record.title) return null;
  const fields = Array.isArray(record.fields) ? record.fields.flatMap((field) => {
    if (!field || typeof field !== "object") return [];
    const item = field as Record<string, unknown>;
    if (typeof item.label !== "string" || typeof item.value !== "string") return [];
    return [{ label: item.label, value: item.value }];
  }) : [];
  return { title: record.title, subtitle: typeof record.subtitle === "string" ? record.subtitle : "", fields };
}

function asText(content: unknown) {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content.map((part) => typeof part === "object" && part && "text" in part ? String(part.text) : "")
    .filter(Boolean).join("\n");
}
