"""Versioned, additive application migrations; never rebuild vector indexes."""

from pathlib import Path


def migrate(pool) -> None:
    with pool.connection() as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(2026110301)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS agent_schema_migrations (version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        for path in sorted(Path(__file__).with_name("migrations").glob("*.sql")):
            if conn.execute(
                "SELECT 1 FROM agent_schema_migrations WHERE version=%s", (path.name,)
            ).fetchone():
                continue
            conn.execute(path.read_text(encoding="utf-8"), prepare=False)
            conn.execute(
                "INSERT INTO agent_schema_migrations(version) VALUES (%s)", (path.name,)
            )


if __name__ == "__main__":
    from ..memory.postgres import database_url, open_pool

    with open_pool(database_url(), min_size=1, max_size=1) as pool:
        migrate(pool)
    print("Application migrations applied")
