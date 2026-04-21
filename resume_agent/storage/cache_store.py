"""Versioned SQLite-backed cache store for local-first workflow artifacts."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from ..storage.user_store import _utcnow, get_db_connection


def _initialize_cache_schema() -> None:
    conn = get_db_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cache_entries (
                namespace TEXT NOT NULL,
                cache_key TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                source_hash TEXT,
                schema_version TEXT DEFAULT 'v1',
                prompt_version TEXT DEFAULT 'v1',
                policy_version TEXT DEFAULT 'v1',
                provider TEXT,
                model TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_used_at TEXT NOT NULL,
                expires_at TEXT,
                PRIMARY KEY (namespace, cache_key)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_namespace ON cache_entries(namespace)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_source_hash ON cache_entries(source_hash)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_expires_at ON cache_entries(expires_at)")
        conn.commit()
    finally:
        conn.close()


class SQLiteCacheStore:
    """Persistent cache entries with metadata-aware invalidation."""

    def __init__(self):
        _initialize_cache_schema()

    def get(self, namespace: str, cache_key: str) -> Optional[Dict[str, Any]]:
        entry = self.peek(namespace, cache_key)
        if not entry:
            return None
        return entry["payload"]

    def peek(self, namespace: str, cache_key: str, *, include_expired: bool = False) -> Optional[Dict[str, Any]]:
        now = _utcnow()
        conn = get_db_connection()
        try:
            row = conn.execute(
                """
                SELECT payload_json, expires_at
                FROM cache_entries
                WHERE namespace = ? AND cache_key = ?
                """,
                (namespace, cache_key),
            ).fetchone()
            if not row:
                return None
            expires_at = row["expires_at"]
            is_expired = bool(expires_at and expires_at <= now)
            if is_expired and not include_expired:
                conn.execute(
                    "DELETE FROM cache_entries WHERE namespace = ? AND cache_key = ?",
                    (namespace, cache_key),
                )
                conn.commit()
                return None
            if not is_expired:
                conn.execute(
                    """
                    UPDATE cache_entries
                    SET last_used_at = ?, updated_at = ?
                    WHERE namespace = ? AND cache_key = ?
                    """,
                    (now, now, namespace, cache_key),
                )
                conn.commit()
            return {
                "payload": json.loads(row["payload_json"]),
                "expires_at": expires_at,
                "is_expired": is_expired,
            }
        finally:
            conn.close()

    def put(
        self,
        namespace: str,
        cache_key: str,
        payload: Dict[str, Any],
        *,
        source_hash: Optional[str] = None,
        schema_version: str = "v1",
        prompt_version: str = "v1",
        policy_version: str = "v1",
        provider: Optional[str] = None,
        model: Optional[str] = None,
        expires_at: Optional[str] = None,
    ) -> None:
        now = _utcnow()
        conn = get_db_connection()
        try:
            conn.execute(
                """
                INSERT INTO cache_entries (
                    namespace, cache_key, payload_json, source_hash, schema_version,
                    prompt_version, policy_version, provider, model, created_at,
                    updated_at, last_used_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(namespace, cache_key)
                DO UPDATE SET
                    payload_json = excluded.payload_json,
                    source_hash = excluded.source_hash,
                    schema_version = excluded.schema_version,
                    prompt_version = excluded.prompt_version,
                    policy_version = excluded.policy_version,
                    provider = excluded.provider,
                    model = excluded.model,
                    updated_at = excluded.updated_at,
                    last_used_at = excluded.last_used_at,
                    expires_at = excluded.expires_at
                """,
                (
                    namespace,
                    cache_key,
                    json.dumps(payload),
                    source_hash,
                    schema_version,
                    prompt_version,
                    policy_version,
                    provider,
                    model,
                    now,
                    now,
                    now,
                    expires_at,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def delete_namespace(self, namespace: str) -> None:
        conn = get_db_connection()
        try:
            conn.execute("DELETE FROM cache_entries WHERE namespace = ?", (namespace,))
            conn.commit()
        finally:
            conn.close()

    def invalidate_by_source_hash(self, source_hash: str) -> None:
        conn = get_db_connection()
        try:
            conn.execute("DELETE FROM cache_entries WHERE source_hash = ?", (source_hash,))
            conn.commit()
        finally:
            conn.close()

    def clear_all(self) -> None:
        conn = get_db_connection()
        try:
            conn.execute("DELETE FROM cache_entries")
            conn.commit()
        finally:
            conn.close()


_cache_store: Optional[SQLiteCacheStore] = None


def get_cache_store() -> SQLiteCacheStore:
    global _cache_store
    if _cache_store is None:
        _cache_store = SQLiteCacheStore()
    return _cache_store
