import { HttpAgent, type AgentSubscriber } from "@ag-ui/client";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

type CardField = { label: string; value: string };

type ChatEntry =
  | { kind: "user"; id: string; text: string }
  | { kind: "assistant"; id: string; text: string; streaming: boolean }
  | { kind: "tool"; id: string; name: string; args: string; result: string }
  | { kind: "card"; id: string; title: string; subtitle: string; fields: CardField[] };

const EXAMPLES = [
  "现在几点了？北京天气怎么样？再帮我算 23*17",
  "记一条笔记，标题是 Loop，正文是 Agent 会反复调用工具",
  "读取 notes://catalog，告诉我有哪些标题",
];

const agentUrl = import.meta.env.VITE_AGENT_URL ?? "http://127.0.0.1:8010/agent";

export function App() {
  const agent = useMemo(() => new HttpAgent({ url: agentUrl }), []);
  const [entries, setEntries] = useState<ChatEntry[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [entries, busy, error]);

  async function send(text: string) {
    const content = text.trim();
    if (!content || busy) return;

    const id = crypto.randomUUID();
    setEntries((prev) => [...prev, { kind: "user", id, text: content }]);
    setDraft("");
    setError("");
    setBusy(true);
    agent.addMessage({ id, role: "user", content });

    const subscriber: AgentSubscriber = {
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
        if (event.activityType !== "card") return;
        const card = asCard(event.content);
        if (!card) return;
        setEntries((prev) => [...prev, { kind: "card", id: event.messageId, ...card }]);
      },
      onRunErrorEvent({ event }) {
        setError(event.message);
      },
      onRunFailed({ error: runError }) {
        if (runError.name === "AbortError") return;
        setError(runError.message);
      },
    };

    try {
      await agent.runAgent({}, subscriber);
    } catch (err) {
      if (!(err instanceof Error) || err.name !== "AbortError") {
        setError(err instanceof Error ? err.message : "请求失败");
      }
    } finally {
      setBusy(false);
      setEntries((prev) =>
        prev.map((item) => (item.kind === "assistant" ? { ...item, streaming: false } : item)),
      );
      inputRef.current?.focus();
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
            <p>可以直接问时间、天气、计算，也可以记笔记、读目录。</p>
            <div className="examples">
              {EXAMPLES.map((example) => (
                <button key={example} type="button" onClick={() => send(example)} disabled={busy}>
                  {example}
                </button>
              ))}
            </div>
          </div>
        ) : (
          entries.map((entry) => <EntryView key={entry.id} entry={entry} />)
        )}
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
          <button type="submit" disabled={!draft.trim()}>
            发送
          </button>
        )}
      </form>
    </div>
  );
}

function showTyping(entries: ChatEntry[], busy: boolean) {
  if (!busy) return false;
  const last = entries[entries.length - 1];
  if (!last || last.kind === "user") return true;
  if (last.kind === "assistant" && last.streaming) return false;
  if (last.kind === "tool" && !last.result) return false;
  return true;
}

function EntryView({ entry }: { entry: ChatEntry }) {
  if (entry.kind === "tool") {
    return (
      <article className="tool">
        <header>
          <span>工具</span>
          <strong>{entry.name}</strong>
        </header>
        {entry.args ? <pre>{formatArgs(entry.args)}</pre> : null}
        {entry.result ? <p>{entry.result}</p> : <p className="muted">执行中…</p>}
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
