import { useLayoutEffect, useRef, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { formatToolResult } from "../lib/errors";
import type { ChatEntry, McpConfirm, PendingConfirm } from "../types/chat";
import "./ChatTranscript.css";

type ChatTranscriptProps = {
  conversationId: string | null;
  entries: ChatEntry[];
  examples: string[];
  busy: boolean;
  loading: boolean;
  remoteRunning: boolean;
  cancelled: boolean;
  cancelling: boolean;
  confirm: PendingConfirm | null;
  mcpConfirm: McpConfirm | null;
  error: string;
  onSend: (text: string) => void;
  onAnswer: (approved: boolean) => void;
  onAnswerMcp: (action: "accept" | "decline") => void;
  onReload: () => void;
};

export function ChatTranscript({
  conversationId,
  entries,
  examples,
  busy,
  loading,
  remoteRunning,
  cancelled,
  cancelling,
  confirm,
  mcpConfirm,
  error,
  onSend,
  onAnswer,
  onAnswerMcp,
  onReload,
}: ChatTranscriptProps) {
  const scrollRef = useRef<HTMLElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const followBottom = useRef(true);
  const latestUserId = entries.filter((entry) => entry.kind === "user").at(-1)?.id;

  useLayoutEffect(() => {
    followBottom.current = true;
  }, [conversationId, latestUserId]);

  useLayoutEffect(() => {
    if (busy) followBottom.current = true;
  }, [busy]);

  useLayoutEffect(() => {
    const viewport = scrollRef.current;
    const content = contentRef.current;
    if (!viewport || !content) return;
    const scrollToBottom = () => {
      if (followBottom.current) viewport.scrollTop = viewport.scrollHeight;
    };
    // Observe Markdown/image layout and viewport changes as well as token updates.
    const observer = new ResizeObserver(scrollToBottom);
    observer.observe(content);
    observer.observe(viewport);
    scrollToBottom();
    return () => observer.disconnect();
  }, []);

  useLayoutEffect(() => {
    const viewport = scrollRef.current;
    if (viewport && followBottom.current) viewport.scrollTop = viewport.scrollHeight;
  }, [conversationId, latestUserId, entries, busy, loading, error, confirm, mcpConfirm,
    remoteRunning, cancelled, cancelling]);

  return (
    <main className="transcript" ref={scrollRef} onScroll={(event) => {
      const viewport = event.currentTarget;
      followBottom.current = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight < 48;
    }}>
      <div className="transcript-content" ref={contentRef}>
      {loading ? <p className="pending">正在加载会话…</p> : null}
      {remoteRunning ? <p className="pending">此会话仍在后台执行，正在同步历史…</p> : null}
      {cancelling ? <p className="pending" role="status">正在取消，等待当前步骤安全结束…</p> : null}
      {cancelled ? <p className="pending" role="status">回答已中断，可继续生成并追加到原回答，也可以直接发送新问题。</p> : null}
      {entries.length === 0 ? (
        <div className="empty">
          <p>可以直接问时间、天气、计算，也可以记笔记、读目录。记笔记会在调用前确认，删除笔记由 MCP server 在调用中确认。</p>
          <div className="examples">
            {examples.map((example) => (
              <button key={example} type="button" onClick={() => onSend(example)} disabled={busy || loading || remoteRunning || confirm !== null}>
                {example}
              </button>
            ))}
          </div>
        </div>
      ) : entries.map((entry) => (
        <EntryView key={entry.id} entry={entry} waiting={confirm?.toolCallId === entry.id} />
      ))}

      {confirm ? (
        <article className="confirm">
          <header><span>需要确认</span><strong>add_note</strong></header>
          <pre>{confirm.message}</pre>
          <div className="confirm-actions">
            <button type="button" onClick={() => onAnswer(true)} disabled={busy || loading || remoteRunning}>接受</button>
            <button type="button" onClick={() => onAnswer(false)} disabled={busy || loading || remoteRunning}>拒绝</button>
          </div>
        </article>
      ) : null}
      {mcpConfirm ? (
        <article className="confirm">
          <header><span>MCP 确认</span><strong>delete_note</strong></header>
          <pre>{mcpConfirm.message}</pre>
          <div className="confirm-actions">
            <button type="button" onClick={() => onAnswerMcp("accept")}>接受</button>
            <button type="button" onClick={() => onAnswerMcp("decline")}>拒绝</button>
          </div>
        </article>
      ) : null}
      {showTyping(entries, busy) ? <p className="pending">正在思考…</p> : null}
      {error ? (
        <div className="error" role="alert">
          <p>{error}</p>
          <button type="button" disabled={busy || loading} onClick={onReload}>重新加载会话</button>
        </div>
      ) : null}
      </div>
    </main>
  );
}

function EntryView({ entry, waiting }: { entry: ChatEntry; waiting: boolean }) {
  if (entry.kind === "step") return (
    <p className={entry.running ? "step running" : "step"}>
      <span className="step-dot" /><span>{entry.name}</span>
      <span className="muted">{entry.running ? "进行中" : "完成"}</span>
    </p>
  );
  if (entry.kind === "reasoning") return (
    <article className="reasoning">
      <header><span>思考</span>{entry.streaming ? <span className="caret" /> : null}</header>
      {entry.text ? <pre>{entry.text}</pre> : null}
    </article>
  );
  if (entry.kind === "tool") return (
    <article className="tool">
      <header><span>工具</span><strong>{entry.name}</strong></header>
      {entry.args ? <pre>{formatArgs(entry.args)}</pre> : null}
      {entry.result ? <p>{formatToolResult(entry.result)}</p> : <p className="muted">{waiting ? "等待确认" : "执行中…"}</p>}
    </article>
  );
  if (entry.kind === "card") return (
    <article className="weather-card">
      <p className="card-kicker">天气</p><h2>{entry.title}</h2>
      {entry.subtitle ? <p className="card-condition">{entry.subtitle}</p> : null}
      {entry.fields.length > 0 ? (
        <dl>{entry.fields.map((field) => <div key={field.label}><dt>{field.label}</dt><dd>{field.value}</dd></div>)}</dl>
      ) : null}
    </article>
  );
  if (entry.kind === "user") return <article className="row user"><p>{entry.text}</p></article>;
  return (
    <article className="row assistant">
      <div className="markdown">
        {entry.text ? <ReactMarkdown remarkPlugins={[remarkGfm]} components={{ a: MarkdownLink }}>{entry.text}</ReactMarkdown> : null}
        {entry.streaming ? <span className="caret" /> : null}
      </div>
    </article>
  );
}

function MarkdownLink({ href, children }: { href?: string; children?: ReactNode }) {
  return <a href={href} target="_blank" rel="noreferrer">{children}</a>;
}

function formatArgs(raw: string) {
  try { return JSON.stringify(JSON.parse(raw), null, 2); } catch { return raw; }
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
