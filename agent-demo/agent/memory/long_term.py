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


class PostgresMemory:
    """进程活着期间占着自己的连接池。setup 会创建 pgvector 和表。"""

    def __init__(self, url: str | None = None) -> None:
        self.url = database_url(url)
        self._pool = None
        try:
            self._pool = open_pool(self.url, min_size=1, max_size=5)
            self.embeddings, self.dims, self.embedding_model = build_embeddings()
            rebuilt = _align_vector_dims(self._pool, self.dims)
            self.store = PostgresStore(
                self._pool,
                index={"dims": self.dims, "embed": self.embeddings, "fields": ["text"]},
            )
            self.store.setup()
            if rebuilt:
                _reindex(self.store)
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
        memory_key = key or str(uuid.uuid4())
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


def _align_vector_dims(pool: Any, dims: int) -> bool:
    """列维度和当前模型不一致时拆掉向量表，让 setup 按新维度重建。"""
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT atttypmod
            FROM pg_attribute
            JOIN pg_class ON pg_class.oid = pg_attribute.attrelid
            JOIN pg_namespace ON pg_namespace.oid = pg_class.relnamespace
            WHERE pg_namespace.nspname = 'public'
              AND pg_class.relname = 'store_vectors'
              AND pg_attribute.attname = 'embedding'
              AND NOT pg_attribute.attisdropped
            """
        ).fetchone()
        if row is None or row["atttypmod"] == dims:
            return False
        conn.execute("DROP INDEX IF EXISTS store_vectors_embedding_idx")
        conn.execute("DROP TABLE IF EXISTS store_vectors")
        conn.execute("DELETE FROM vector_migrations WHERE v >= 1")
    return True


def _reindex(store: BaseStore) -> None:
    offset = 0
    while True:
        namespaces = store.list_namespaces(limit=100, offset=offset)
        if not namespaces:
            return
        for namespace in namespaces:
            item_offset = 0
            while True:
                items = store.search(namespace, limit=100, offset=item_offset)
                if not items:
                    break
                for item in items:
                    store.put(item.namespace, item.key, item.value)
                if len(items) < 100:
                    break
                item_offset += len(items)
        if len(namespaces) < 100:
            return
        offset += len(namespaces)


def _record(key: str, value: dict[str, Any], similarity: float) -> MemoryRecord:
    return MemoryRecord(
        key=key,
        text=str(value.get("text") or ""),
        kind=str(value.get("kind") or "fact"),
        subject=str(value.get("subject") or ""),
        score=float(value.get("score") or 0.0),
        similarity=similarity,
    )
