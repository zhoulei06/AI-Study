import { useCallback, useEffect, useRef, useState } from "react";
import { historyRequest } from "../lib/conversationApi";
import type { ConversationDetail } from "../types/chat";
import type { Conversation } from "../components/ConversationHistory";

const activeKey = `active-conversation:${import.meta.env.VITE_AGENT_URL ?? "http://127.0.0.1:8010/agent"}`;

export function useConversations() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConversation, setActiveConversation] = useState<ConversationDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reloadVersion, setReloadVersion] = useState(0);
  const loadVersion = useRef(0);
  const activeId = activeConversation?.id ?? null;

  const refreshList = useCallback(async () => {
    const items = await historyRequest<Conversation[]>();
    setConversations(items);
    return items;
  }, []);

  const refreshDetail = useCallback((id: string) =>
    historyRequest<ConversationDetail>(`/${encodeURIComponent(id)}`), []);

  const activate = useCallback((detail: ConversationDetail) => {
    setActiveConversation(detail);
    localStorage.setItem(activeKey, detail.id);
  }, []);

  useEffect(() => {
    let disposed = false;
    setLoading(true);
    setError("");

    async function initialize() {
      try {
        const items = await refreshList();
        if (disposed) return;
        const saved = localStorage.getItem(activeKey);
        const selected = items.find((item) => item.id === saved) ?? items[0];
        const detail = selected
          ? await refreshDetail(selected.id)
          : await historyRequest<ConversationDetail>("", "POST");
        if (disposed) return;
        activate(detail);
        await refreshList();
      } catch (cause) {
        if (!disposed) setError(cause instanceof Error ? cause.message : "历史加载失败");
      } finally {
        if (!disposed) setLoading(false);
      }
    }

    void initialize();
    return () => { disposed = true; };
  }, [activate, refreshDetail, refreshList, reloadVersion]);

  const open = useCallback(async (id?: string) => {
    if (loading) return;
    const version = ++loadVersion.current;
    setLoading(true);
    setError("");
    try {
      const detail = id
        ? await refreshDetail(id)
        : await historyRequest<ConversationDetail>("", "POST");
      if (version !== loadVersion.current) return;
      activate(detail);
      await refreshList();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "会话加载失败");
    } finally {
      if (version === loadVersion.current) setLoading(false);
    }
  }, [activate, loading, refreshDetail, refreshList]);

  const remove = useCallback(async (item: Conversation) => {
    if (loading || item.status === "running") return;
    if (!window.confirm(`确定删除会话“${item.title}”及其全部聊天历史吗？删除后无法恢复。`)) return;
    setLoading(true);
    setError("");
    try {
      await historyRequest(`/${encodeURIComponent(item.id)}`, "DELETE");
      const remaining = conversations.filter((conversation) => conversation.id !== item.id);
      setConversations(remaining);
      if (item.id === activeId) {
        localStorage.removeItem(activeKey);
        setActiveConversation(null);
        const next = remaining[0]
          ? await refreshDetail(remaining[0].id)
          : await historyRequest<ConversationDetail>("", "POST");
        activate(next);
      }
      await refreshList();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "删除会话失败");
    } finally {
      setLoading(false);
    }
  }, [activate, activeId, conversations, loading, refreshDetail, refreshList]);

  const reload = useCallback(() => setReloadVersion((version) => version + 1), []);

  return {
    conversations,
    activeConversation,
    activeId,
    loading,
    error,
    open,
    remove,
    reload,
    refreshList,
    refreshDetail,
    activate,
  };
}
