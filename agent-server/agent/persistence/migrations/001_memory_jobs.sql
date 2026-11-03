CREATE TABLE IF NOT EXISTS agent_embedding_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    model TEXT NOT NULL,
    dims INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_memory_jobs (
    user_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    user_text TEXT NOT NULL,
    assistant_text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    lease_until TIMESTAMPTZ,
    lease_token TEXT,
    error TEXT,
    usage JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, run_id)
);
CREATE INDEX IF NOT EXISTS agent_memory_jobs_pending
    ON agent_memory_jobs(status, available_at);
