import "./ConversationHistory.css";

export type Conversation = {
  id: string;
  title: string;
  updated_at: string;
  status: string;
};

type ConversationHistoryProps = {
  conversations: Conversation[];
  activeId: string | null;
  disabled: boolean;
  onCreate: () => void;
  onSelect: (id: string) => void;
  onDelete: (conversation: Conversation) => void;
};

export function ConversationHistory({
  conversations,
  activeId,
  disabled,
  onCreate,
  onSelect,
  onDelete,
}: ConversationHistoryProps) {
  return (
    <aside className="sidebar" aria-label="会话历史">
      <div className="sidebar-top">
        <h2>会话历史</h2>
        <button type="button" onClick={onCreate} disabled={disabled}>
          ＋ 新会话
        </button>
      </div>
      <nav className="conversation-list" aria-label="历史会话">
        {conversations.map((conversation) => {
          const active = conversation.id === activeId;
          const cannotDelete = disabled || conversation.status === "running";
          return (
            <div
              key={conversation.id}
              className={active ? "conversation-row active" : "conversation-row"}
            >
              <button
                type="button"
                className="conversation"
                aria-current={active ? "page" : undefined}
                disabled={disabled}
                onClick={() => onSelect(conversation.id)}
              >
                <strong>{conversation.title}</strong>
                <span>
                  {new Date(conversation.updated_at).toLocaleString("zh-CN", {
                    month: "numeric",
                    day: "numeric",
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </span>
              </button>
              <button
                type="button"
                className="delete-conversation"
                aria-label={`删除会话：${conversation.title}`}
                title={conversation.status === "running" ? "执行中的会话暂不能删除" : "删除会话"}
                disabled={cannotDelete}
                onClick={() => onDelete(conversation)}
              >
                删除
              </button>
            </div>
          );
        })}
      </nav>
    </aside>
  );
}
