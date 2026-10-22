"""同一个 PostgreSQL 上的连接池。

短期 checkpoint 和长期记忆各用一个池，避免向量检索占着连接时会话写不进去。
池里的连接开着 autocommit：建索引用了 CREATE INDEX CONCURRENTLY。
"""

from __future__ import annotations

import os

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

DEFAULT_DATABASE_URL = "postgresql://study:study@127.0.0.1:5432/study"

_CONN_KWARGS = {
    "autocommit": True,
    "prepare_threshold": 0,
    "row_factory": dict_row,
}


def database_url(explicit: str | None = None) -> str:
    return (explicit or os.getenv("MEMORY_DATABASE_URL", "")).strip() or DEFAULT_DATABASE_URL


def open_pool(url: str, *, min_size: int, max_size: int) -> ConnectionPool:
    """打开后等到 min_size 条连接可用。连不上就关掉池再抛出。"""
    pool = ConnectionPool(
        conninfo=url,
        min_size=min_size,
        max_size=max_size,
        kwargs=_CONN_KWARGS,
        open=False,
        timeout=10,
        max_lifetime=30 * 60,
        max_idle=5 * 60,
        check=ConnectionPool.check_connection,
    )
    try:
        pool.open(wait=True, timeout=10)
    except Exception:
        pool.close()
        raise
    return pool


def cannot_connect(exc: BaseException) -> bool:
    pending = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if type(current).__name__ in {
            "OperationalError",
            "ConnectionRefusedError",
            "ConnectionError",
            "PoolTimeout",
        }:
            return True
        pending.extend(getattr(current, "exceptions", ()))
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return False


def connection_error(url: str) -> RuntimeError:
    return RuntimeError(
        "连不上 PostgreSQL。"
        "先启动：cd agent-demo; docker compose up -d 。"
        f"当前地址是 {url}"
    )
