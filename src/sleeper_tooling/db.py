from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


class ApiResponseCache:
    def __init__(self, db_path: Path, *, default_ttl_seconds: int = 900) -> None:
        self.db_path = db_path
        self.default_ttl_seconds = default_ttl_seconds
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.db_path, timeout=30)
        self._connection.row_factory = sqlite3.Row
        self._configure_connection()
        self._migrate()

    def close(self) -> None:
        self._connection.close()

    def get(self, cache_key: str) -> Any | None:
        row = self._connection.execute(
            """
            SELECT response_json, fetched_at, ttl_seconds
            FROM api_cache
            WHERE cache_key = ?
            """,
            (cache_key,),
        ).fetchone()
        if row is None:
            return None
        if time.time() - float(row["fetched_at"]) > int(row["ttl_seconds"]):
            return None
        return json.loads(row["response_json"])

    def set(
        self,
        cache_key: str,
        *,
        url: str,
        response: Any,
        ttl_seconds: int | None = None,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO api_cache (
                cache_key,
                url,
                response_json,
                fetched_at,
                ttl_seconds
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                url = excluded.url,
                response_json = excluded.response_json,
                fetched_at = excluded.fetched_at,
                ttl_seconds = excluded.ttl_seconds
            """,
            (
                cache_key,
                url,
                json.dumps(response, sort_keys=True),
                time.time(),
                ttl_seconds or self.default_ttl_seconds,
            ),
        )
        self._connection.commit()

    def clear(self) -> int:
        cursor = self._connection.execute("DELETE FROM api_cache")
        self._connection.commit()
        return cursor.rowcount

    def clear_expired(self) -> int:
        cursor = self._connection.execute(
            """
            DELETE FROM api_cache
            WHERE ? - fetched_at > ttl_seconds
            """,
            (time.time(),),
        )
        self._connection.commit()
        return cursor.rowcount

    def stats(self) -> dict[str, Any]:
        row = self._connection.execute(
            """
            SELECT
                COUNT(*) AS total_entries,
                SUM(CASE WHEN ? - fetched_at <= ttl_seconds THEN 1 ELSE 0 END)
                    AS fresh_entries,
                SUM(CASE WHEN ? - fetched_at > ttl_seconds THEN 1 ELSE 0 END)
                    AS expired_entries,
                MIN(fetched_at) AS oldest_fetched_at,
                MAX(fetched_at) AS newest_fetched_at
            FROM api_cache
            """,
            (time.time(), time.time()),
        ).fetchone()
        return {
            "db_path": str(self.db_path),
            "total_entries": int(row["total_entries"] or 0),
            "fresh_entries": int(row["fresh_entries"] or 0),
            "expired_entries": int(row["expired_entries"] or 0),
            "oldest_fetched_at": row["oldest_fetched_at"],
            "newest_fetched_at": row["newest_fetched_at"],
        }

    def _migrate(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS api_cache (
                cache_key TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                response_json TEXT NOT NULL,
                fetched_at REAL NOT NULL,
                ttl_seconds INTEGER NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_api_cache_fetched_at
            ON api_cache(fetched_at)
            """
        )
        self._connection.commit()

    def _configure_connection(self) -> None:
        self._connection.execute("PRAGMA busy_timeout = 30000")
        self._connection.execute("PRAGMA journal_mode = WAL")
