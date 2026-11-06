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
      {cancelled ? <p className="pending" role="status">任务已暂停，可继续未完成的步骤或回答，也可以发送新目标。</p> : null}
      {entries.length === 0 ? (
        <div className="empty">
          <h2>从一个目标开始</h2>
          <p>提问、分析信息、拆解任务或管理笔记。复杂目标会按计划逐步执行并验证结果。</p>
          <p className="muted">当前已接入时间、演示天气、计算及 MCP 笔记工具；实际操作能力取决于已连接的工具。</p>
          <div className="examples">
            {examples.map((example) => (
              <button key={example} type="button" onClick={() => onSend(example)} disabled={busy || loading || remoteRunning || confirm !== null}>
                {example}
              </button>
            ))}
          </div>
        </div>
      ) : entries.map((entry) => (
        <EntryView key={entry.id} entry={entry} waiting={confirm?.toolCallId === entry.id}
          paused={cancelled} stopped={!busy && !remoteRunning && !!error} />
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

function EntryView({ entry, waiting, paused, stopped }: {
  entry: ChatEntry; waiting: boolean; paused: boolean; stopped: boolean;
}) {
  if (entry.kind === "plan") {
    const labels = { pending: "待执行", running: "执行中", succeeded: "已验证", failed: "未通过" };
    const phases = { executing: "正在执行", finalizing: "汇总结果", completed: "已完成", failed: "已停止" };
    const active = entry.plan.phase === "executing" || entry.plan.phase === "finalizing";
    const phase = active && paused ? "已暂停" : active && stopped ? "已停止" : phases[entry.plan.phase];
    return <article className="plan-progress" aria-label="任务计划">
      <header><strong>任务计划</strong><span>{phase} · 第 {entry.plan.version} 版</span></header>
      <p>{entry.plan.goal}</p>
      <ol>{entry.plan.steps.map((step) => <li key={step.id} data-status={step.status}>
        <span>{step.task}</span><small>{step.status === "running" && (paused || stopped) ? phase : labels[step.status]}</small>
      </li>)}</ol>
    </article>;
  }
  if (entry.kind === "step") return (
    <p className={entry.running ? "step running" : "step"}>
      <span className="step-dot" /><span>{entry.name}</span>
      <span className="muted">{entry.running ? "进行中" : "完成"}</span>
    </p>
  );
  if (entry.kind === "reasoning") return (
    <details className="reasoning">
      <summary>
        <span>{entry.streaming ? "正在思考…" : "思考过程"}</span>
        <span className="reasoning-toggle" aria-hidden="true" />
      </summary>
      {entry.text ? <pre>{entry.text}</pre> : null}
    </details>
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
