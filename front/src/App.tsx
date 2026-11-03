import { useEffect, useRef } from "react";
import { ChatTranscript } from "./components/ChatTranscript";
import { ConversationHistory } from "./components/ConversationHistory";
import { useAgentChat } from "./hooks/useAgentChat";
import { useConversations } from "./hooks/useConversations";
import { agentUrl } from "./lib/conversationApi";
import "./App.css";

export function App() {
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const history = useConversations();
  const chat = useAgentChat({ onHistoryUpdated: history.refreshList, inputRef });

  useEffect(() => {
    if (history.activeConversation) chat.restoreConversation(history.activeConversation);
  }, [history.activeConversation, chat.restoreConversation]);

  useEffect(() => {
    if (!chat.remoteRunning || !history.activeId || chat.busy || history.loading) return;
    let disposed = false;
    const timer = window.setInterval(async () => {
      try {
        const detail = await history.refreshDetail(history.activeId!);
        if (disposed) return;
        history.activate(detail);
        if (detail.status !== "running") await history.refreshList();
      } catch (cause) {
        if (!disposed) chat.setError(cause instanceof Error ? cause.message : "历史刷新失败");
      }
    }, 2000);
    return () => { disposed = true; window.clearInterval(timer); };
  }, [
    chat.busy,
    chat.remoteRunning,
    chat.setError,
    history.activeId,
    history.activate,
    history.loading,
    history.refreshDetail,
    history.refreshList,
  ]);

  const send = (text: string) => void chat.send(text, history.activeId, history.loading);
  const error = history.error || chat.error;

  return (
    <div className="workspace">
      <ConversationHistory
        conversations={history.conversations}
        activeId={history.activeId}
        disabled={chat.busy || history.loading}
        onCreate={() => void history.open()}
        onSelect={(id) => void history.open(id)}
        onDelete={(conversation) => void history.remove(conversation)}
      />
      <div className="page">
        <header className="top">
          <div><p className="eyebrow">AG-UI</p><h1>学习助手</h1></div>
          <p className="endpoint">{agentUrl}</p>
        </header>

        <ChatTranscript
          conversationId={history.activeId}
          entries={chat.entries}
          examples={chat.examples}
          busy={chat.busy}
          loading={history.loading}
          remoteRunning={chat.remoteRunning}
          cancelled={chat.canContinue}
          cancelling={chat.cancelling}
          confirm={chat.confirm}
          mcpConfirm={chat.mcpConfirm}
          error={error}
          onSend={send}
          onAnswer={(approved) => void chat.answer(approved, history.loading)}
          onAnswerMcp={(action) => void chat.answerMcp(action)}
          onReload={history.reload}
        />

        <form
          className="composer"
          onSubmit={(event) => {
            event.preventDefault();
            send(chat.draft);
          }}
        >
          <textarea
            ref={inputRef}
            value={chat.draft}
            disabled={history.loading || chat.remoteRunning || !history.activeId}
            rows={2}
            placeholder="输入问题，Enter 发送，Shift+Enter 换行"
            onChange={(event) => chat.setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                send(chat.draft);
              }
            }}
          />
          {chat.busy || chat.remoteRunning ? (
            <button type="button" disabled={!chat.canCancel || chat.cancelling || history.loading}
              onClick={() => void chat.stop()}>{chat.cancelling ? "取消中…" : "取消"}</button>
          ) : chat.canContinue ? (
            <div className="composer-actions">
              <button type="button" disabled={history.loading} onClick={() => void chat.resume(history.loading)}>
                继续回答
              </button>
              <button type="submit" disabled={history.loading || !chat.draft.trim()}>发送新问题</button>
            </div>
          ) : (
            <button
              type="submit"
              disabled={!chat.draft.trim() || chat.confirm !== null || history.loading || chat.remoteRunning || !history.activeId}
            >
              发送
            </button>
          )}
        </form>
      </div>
    </div>
  );
}
