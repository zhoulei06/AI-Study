import { HttpAgent, type AgentSubscriber } from "@ag-ui/client";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

type CardField = { label: string; value: string };

type PendingConfirm = {
  interruptId: string;
  toolCallId: string;
  message: string;
};

type ChatEntry =
  | { kind: "user"; id: string; text: string }
  | { kind: "step"; id: string; name: string; running: boolean }
  | { kind: "reasoning"; id: string; text: string; streaming: boolean }
  | { kind: "assistant"; id: string; text: string; streaming: boolean }
  | { kind: "tool"; id: string; name: string; args: string; result: string }
  | { kind: "card"; id: string; title: string; subtitle: string; fields: CardField[] };

const EXAMPLES = [
  "现在几点了？北京天气怎么样？再帮我算 23*17",
  "记一条笔记，标题是 Loop，正文是 Agent 会反复调用工具",
  "删除笔记 n3",
  "读取 notes://catalog，告诉我有哪些标题",
];

const agentUrl = import.meta.env.VITE_AGENT_URL ?? "http://127.0.0.1:8010/agent";

export function App() {
  const agent = useMemo(() => new HttpAgent({ url: agentUrl }), []);
  const [entries, setEntries] = useState<ChatEntry[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [confirm, setConfirm] = useState<PendingConfirm | null>(null);
  const [mcpConfirm, setMcpConfirm] = useState<{ id: string; message: string } | null>(null);
  const waitingRef = useRef(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [entries, busy, error, confirm, mcpConfirm]);

  function listen(): AgentSubscriber {
    return {
      onTextMessageStartEvent({ event }) {
        setEntries((prev) => [
          ...prev,
          { kind: "assistant", id: event.messageId, text: "", streaming: true },
        ]);
      },
      onTextMessageEndEvent({ event }) {
        setEntries((prev) =>
          prev.map((item) =>
            item.kind === "assistant" && item.id === event.messageId
              ? { ...item, streaming: false }
              : item,
          ),
        );
      },
      onTextMessageContentEvent({ event }) {
        setEntries((prev) =>
          prev.map((item) =>
            item.kind === "assistant" && item.id === event.messageId
              ? { ...item, text: item.text + event.delta }
              : item,
          ),
        );
      },
      onStepStartedEvent({ event }) {
        setEntries((prev) => [
          ...prev,
          { kind: "step", id: crypto.randomUUID(), name: event.stepName, running: true },
        ]);
      },
      onStepFinishedEvent({ event }) {
        setEntries((prev) => {
          const index = lastRunningStep(prev, event.stepName);
          if (index < 0) return prev;
          return prev.map((item, itemIndex) =>
            itemIndex === index && item.kind === "step" ? { ...item, running: false } : item,
          );
        });
      },
      onReasoningMessageContentEvent({ event }) {
        if (!event.delta) return;
        setEntries((prev) => {
          const exists = prev.some((item) => item.kind === "reasoning" && item.id === event.messageId);
          if (!exists) {
            return [
              ...prev,
              { kind: "reasoning", id: event.messageId, text: event.delta, streaming: true },
            ];
          }
          return prev.map((item) =>
            item.kind === "reasoning" && item.id === event.messageId
              ? { ...item, text: item.text + event.delta }
              : item,
          );
        });
      },
      onReasoningMessageEndEvent({ event }) {
        setEntries((prev) =>
          prev.map((item) =>
            item.kind === "reasoning" && item.id === event.messageId
              ? { ...item, streaming: false }
              : item,
          ),
        );
      },
      onToolCallStartEvent({ event }) {
        setEntries((prev) => [
          ...prev,
          {
            kind: "tool",
            id: event.toolCallId,
            name: event.toolCallName,
            args: "",
            result: "",
          },
        ]);
      },
      onToolCallArgsEvent({ event }) {
        setEntries((prev) =>
          prev.map((item) =>
            item.kind === "tool" && item.id === event.toolCallId
              ? { ...item, args: item.args + event.delta }
              : item,
          ),
        );
      },
      onToolCallResultEvent({ event }) {
        setEntries((prev) =>
          prev.map((item) =>
            item.kind === "tool" && item.id === event.toolCallId
              ? { ...item, result: asText(event.content) }
              : item,
          ),
        );
      },
      onActivitySnapshotEvent({ event }) {
        if (event.activityType === "mcp-confirm") {
          const record = event.content;
          if (
            record &&
            typeof record === "object" &&
            "id" in record &&
            "message" in record &&
            typeof record.id === "string" &&
            typeof record.message === "string"
          ) {
            setMcpConfirm({ id: record.id, message: record.message });
          }
          return;
        }
        if (event.activityType !== "card") return;
        const card = asCard(event.content);
        if (!card) return;
        setEntries((prev) => [...prev, { kind: "card", id: event.messageId, ...card }]);
      },
      onRunFinishedEvent(params) {
        if (params.outcome === "interrupt") {
          const item = params.interrupts[0];
          if (item?.toolCallId) {
            waitingRef.current = true;
            setConfirm({
              interruptId: item.id,
              toolCallId: item.toolCallId,
              message: item.message ?? "需要确认这次调用",
            });
            return;
          }
        }
        waitingRef.current = false;
        setConfirm(null);
      },
      onRunErrorEvent({ event }) {
        setError(event.message);
      },
      onRunFailed({ error: runError }) {
        if (runError.name === "AbortError") return;
        setError(runError.message);
      },
    };
  }

  async function send(text: string) {
    const content = text.trim();
    if (!content || busy || confirm) return;

    const id = crypto.randomUUID();
    setEntries((prev) => [...prev, { kind: "user", id, text: content }]);
    setDraft("");
    setError("");
    setBusy(true);
    agent.addMessage({ id, role: "user", content });
    await execute(listen());
  }

  async function answer(approved: boolean) {
    if (!confirm || busy) return;
    const current = confirm;
    waitingRef.current = false;
    setConfirm(null);
    setBusy(true);
    setError("");
    await execute(listen(), {
      resume: [
        {
          interruptId: current.interruptId,
          status: approved ? "resolved" : "cancelled",
          payload: { approved, toolCallId: current.toolCallId },
        },
      ],
    });
  }

  async function execute(
    subscriber: AgentSubscriber,
    parameters?: Parameters<HttpAgent["runAgent"]>[0],
  ) {
    try {
      await agent.runAgent(parameters, subscriber);
    } catch (err) {
      if (!(err instanceof Error) || err.name !== "AbortError") {
        setError(err instanceof Error ? err.message : "请求失败");
      }
    } finally {
      setBusy(false);
      setMcpConfirm(null);
      setEntries((prev) => settleEntries(prev));
      inputRef.current?.focus();
    }
  }

  async function answerMcp(action: "accept" | "decline") {
    if (!mcpConfirm) return;
    const current = mcpConfirm;
    setMcpConfirm(null);
    try {
      const response = await fetch(`${agentUrl}/elicit`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ id: current.id, action }),
      });
      if (!response.ok) setError("确认没有送回 MCP client");
    } catch (err) {
      setError(err instanceof Error ? err.message : "确认失败");
    }
  }

  function stop() {
    agent.abortRun();
  }

  return (
    <div className="page">
      <header className="top">
        <div>
          <p className="eyebrow">AG-UI</p>
          <h1>学习助手</h1>
        </div>
        <p className="endpoint">{agentUrl}</p>
      </header>

      <main className="transcript">
        {entries.length === 0 ? (
          <div className="empty">
            <p>可以直接问时间、天气、计算，也可以记笔记、读目录。记笔记会在调用前确认，删除笔记由 MCP server 在调用中确认。</p>
            <div className="examples">
              {EXAMPLES.map((example) => (
                <button key={example} type="button" onClick={() => send(example)} disabled={busy || confirm !== null}>
                  {example}
                </button>
              ))}
            </div>
          </div>
        ) : (
          entries.map((entry) => (
            <EntryView
              key={entry.id}
              entry={entry}
              waiting={confirm?.toolCallId === entry.id}
            />
          ))
        )}
        {confirm ? (
          <article className="confirm">
            <header>
              <span>需要确认</span>
              <strong>add_note</strong>
            </header>
            <pre>{confirm.message}</pre>
            <div className="confirm-actions">
              <button type="button" onClick={() => void answer(true)} disabled={busy}>
                接受
              </button>
              <button type="button" onClick={() => void answer(false)} disabled={busy}>
                拒绝
              </button>
            </div>
          </article>
        ) : null}
        {mcpConfirm ? (
          <article className="confirm">
            <header>
              <span>MCP 确认</span>
              <strong>delete_note</strong>
            </header>
            <pre>{mcpConfirm.message}</pre>
            <div className="confirm-actions">
              <button type="button" onClick={() => void answerMcp("accept")}>
                接受
              </button>
              <button type="button" onClick={() => void answerMcp("decline")}>
                拒绝
              </button>
            </div>
          </article>
        ) : null}
        {showTyping(entries, busy) ? <p className="pending">正在思考…</p> : null}
        {error ? <p className="error">{error}</p> : null}
        <div ref={bottomRef} />
      </main>

      <form
        className="composer"
        onSubmit={(event) => {
          event.preventDefault();
          void send(draft);
        }}
      >
        <textarea
          ref={inputRef}
          value={draft}
          rows={2}
          placeholder="输入问题，Enter 发送，Shift+Enter 换行"
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void send(draft);
            }
          }}
        />
        {busy ? (
          <button type="button" onClick={stop}>
            停止
          </button>
        ) : (
          <button type="submit" disabled={!draft.trim() || confirm !== null}>
            发送
          </button>
        )}
      </form>
    </div>
  );
}

function settleEntries(entries: ChatEntry[]) {
  return entries
    .map((item): ChatEntry | null => {
      if (item.kind === "assistant") return { ...item, streaming: false };
      if (item.kind === "step") return { ...item, running: false };
      if (item.kind === "reasoning") {
        if (!item.text.trim()) return null;
        return { ...item, streaming: false };
      }
      return item;
    })
    .filter((item): item is ChatEntry => item !== null);
}

function lastRunningStep(entries: ChatEntry[], name: string) {
  for (let index = entries.length - 1; index >= 0; index -= 1) {
    const item = entries[index];
    if (item.kind === "step" && item.name === name && item.running) return index;
  }
  return -1;
}

function showTyping(entries: ChatEntry[], busy: boolean) {
  if (!busy) return false;
  const last = entries[entries.length - 1];
  if (!last || last.kind === "user") return true;
  if (last.kind === "step" && last.running) return false;
  if (last.kind === "reasoning" && last.streaming) return false;
  if (last.kind === "assistant" && last.streaming) return false;
  if (last.kind === "tool" && !last.result) return false;
  return true;
}

function EntryView({ entry, waiting }: { entry: ChatEntry; waiting: boolean }) {
  if (entry.kind === "step") {
    return (
      <p className={entry.running ? "step running" : "step"}>
        <span className="step-dot" />
        <span>{entry.name}</span>
        <span className="muted">{entry.running ? "进行中" : "完成"}</span>
      </p>
    );
  }

  if (entry.kind === "reasoning") {
    return (
      <article className="reasoning">
        <header>
          <span>思考</span>
          {entry.streaming ? <span className="caret" /> : null}
        </header>
        {entry.text ? <pre>{entry.text}</pre> : null}
      </article>
    );
  }

  if (entry.kind === "tool") {
    return (
      <article className="tool">
        <header>
          <span>工具</span>
          <strong>{entry.name}</strong>
        </header>
        {entry.args ? <pre>{formatArgs(entry.args)}</pre> : null}
        {entry.result ? <p>{entry.result}</p> : <p className="muted">{waiting ? "等待确认" : "执行中…"}</p>}
      </article>
    );
  }

  if (entry.kind === "card") {
    return (
      <article className="weather-card">
        <p className="card-kicker">天气</p>
        <h2>{entry.title}</h2>
        {entry.subtitle ? <p className="card-condition">{entry.subtitle}</p> : null}
        {entry.fields.length > 0 ? (
          <dl>
            {entry.fields.map((field) => (
              <div key={field.label}>
                <dt>{field.label}</dt>
                <dd>{field.value}</dd>
              </div>
            ))}
          </dl>
        ) : null}
      </article>
    );
  }

  if (entry.kind === "user") {
    return (
      <article className="row user">
        <p>{entry.text}</p>
      </article>
    );
  }

  return (
    <article className="row assistant">
      <div className="markdown">
        {entry.text ? (
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={{ a: MarkdownLink }}>
            {entry.text}
          </ReactMarkdown>
        ) : null}
        {entry.streaming ? <span className="caret" /> : null}
      </div>
    </article>
  );
}

function MarkdownLink({ href, children }: { href?: string; children?: ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noreferrer">
      {children}
    </a>
  );
}

function formatArgs(raw: string) {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}

function asCard(content: unknown): { title: string; subtitle: string; fields: CardField[] } | null {
  if (!content || typeof content !== "object") return null;
  const record = content as Record<string, unknown>;
  if (typeof record.title !== "string" || !record.title) return null;
  const fields = Array.isArray(record.fields)
    ? record.fields.flatMap((field) => {
        if (!field || typeof field !== "object") return [];
        const item = field as Record<string, unknown>;
        if (typeof item.label !== "string" || typeof item.value !== "string") return [];
        return [{ label: item.label, value: item.value }];
      })
    : [];
  return {
    title: record.title,
    subtitle: typeof record.subtitle === "string" ? record.subtitle : "",
    fields,
  };
}

function asText(content: unknown) {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .map((part) =>
      typeof part === "object" && part && "text" in part ? String(part.text) : "",
    )
    .filter(Boolean)
    .join("\n");
}
