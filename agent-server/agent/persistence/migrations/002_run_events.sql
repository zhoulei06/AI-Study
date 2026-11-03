CREATE TABLE IF NOT EXISTS chat_conversations (
    thread_id TEXT PRIMARY KEY, owner TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '新会话',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    document JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS chat_conversations_owner_updated
    ON chat_conversations(owner, updated_at DESC);
CREATE TABLE IF NOT EXISTS agent_run_events (
    owner TEXT NOT NULL,
    thread_id TEXT NOT NULL REFERENCES chat_conversations(thread_id) ON DELETE CASCADE,
    run_id TEXT NOT NULL,
    seq BIGINT NOT NULL,
    event JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (owner, thread_id, run_id, seq)
);
