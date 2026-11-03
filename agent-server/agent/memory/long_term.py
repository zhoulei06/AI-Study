"""跨会话记忆的读写接口，背后是 PostgreSQL + pgvector。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from langgraph.store.base import BaseStore
from langgraph.store.postgres import PostgresStore

from .embed import build_embeddings
from .postgres import cannot_connect, connection_error, database_url, open_pool
from .types import Candidate, MemoryRecord
from ..persistence.migrate import migrate


class PostgresMemory:
    """进程活着期间占着自己的连接池。setup 会创建 pgvector 和表。"""

    def __init__(self, url: str | None = None) -> None:
        self.url = database_url(url)
        self._pool = None
        try:
            self._pool = open_pool(self.url, min_size=1, max_size=5)
            self.embeddings, self.dims, self.embedding_model = build_embeddings()
            migrate(self._pool)
            _validate_embedding_config(self._pool, self.dims, self.embedding_model)
            self.store = PostgresStore(
                self._pool,
                index={"dims": self.dims, "embed": self.embeddings, "fields": ["text"]},
            )
            self.store.setup()
        except Exception as exc:
            self.close()
            if cannot_connect(exc):
                raise connection_error(self.url) from exc
            raise

    def close(self) -> None:
        pool = self._pool
        if pool is not None:
            self._pool = None
            pool.close()


class MemoryRepository:
    """一个用户一个命名空间。线程之间共用这批记忆。"""

    def __init__(self, store: BaseStore, user_id: str):
        self.store = store
        self.namespace = ("study", user_id or "local")

    def save(self, candidate: Candidate, score: float, *, key: str | None = None) -> MemoryRecord:
        identity = f"{self.namespace}:{candidate.subject or candidate.text}"
        memory_key = key or str(uuid.uuid5(uuid.NAMESPACE_URL, identity))
        value = {
            "text": candidate.text,
            "kind": candidate.kind,
            "subject": candidate.subject,
            "score": score,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self.store.put(self.namespace, memory_key, value)
        return _record(memory_key, value, 1.0)

    def find_subject(self, subject: str) -> MemoryRecord | None:
        if not subject:
            return None
        items = self.store.search(self.namespace, filter={"subject": subject}, limit=1)
        if not items:
            return None
        return _record(items[0].key, items[0].value, float(items[0].score or 0.0))

    def search(self, query: str, *, limit: int = 5) -> list[MemoryRecord]:
        text = query.strip()
        if not text:
            return []
        items = self.store.search(self.namespace, query=text, limit=limit)
        return [_record(item.key, item.value, float(item.score or 0.0)) for item in items]


def _validate_embedding_config(pool: Any, dims: int, model: str) -> None:
    """Baseline legacy indexes once; subsequent model changes need a migration.

    No original model metadata exists on legacy databases: the operator's
    configured model is recorded as the upgrade baseline, never auto-reindexed.
    """
    with pool.connection() as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(2026110302)")
        row = conn.execute("""SELECT atttypmod FROM pg_attribute
            JOIN pg_class ON pg_class.oid=pg_attribute.attrelid
            JOIN pg_namespace ON pg_namespace.oid=pg_class.relnamespace
            WHERE pg_namespace.nspname='public' AND pg_class.relname='store_vectors'
              AND pg_attribute.attname='embedding' AND NOT pg_attribute.attisdropped""").fetchone()
        if row is not None and row["atttypmod"] != dims:
            raise RuntimeError("向量维度与已有索引不一致；请执行显式索引迁移，启动过程不会删除原数据")
        registered = conn.execute("SELECT model,dims FROM agent_embedding_config WHERE id=1").fetchone()
        if registered is not None and (registered["model"] != model or registered["dims"] != dims):
            raise RuntimeError("嵌入模型与已登记索引不一致；同维度换模型也需要显式迁移")
        conn.execute("INSERT INTO agent_embedding_config(id,model,dims) VALUES (1,%s,%s) ON CONFLICT DO NOTHING", (model, dims))


def _record(key: str, value: dict[str, Any], similarity: float) -> MemoryRecord:
    return MemoryRecord(
        key=key,
        text=str(value.get("text") or ""),
        kind=str(value.get("kind") or "fact"),
        subject=str(value.get("subject") or ""),
        score=float(value.get("score") or 0.0),
        similarity=similarity,
    )
