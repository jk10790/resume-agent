"""
SQLite-backed local user/profile store.

This module is the first step in moving user-scoped state out of local JSON
files and into a durable, authenticated local data store.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from ..config import settings
from ..utils.logger import logger


DB_PATH = settings.resolved_application_db_path


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_db_connection() -> sqlite3.Connection:
    """Get a SQLite connection and ensure user/profile tables exist."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    _initialize_schema(conn)
    return conn


def _initialize_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            google_sub TEXT UNIQUE,
            email TEXT,
            name TEXT,
            picture_url TEXT,
            preferred_resume_doc_id TEXT,
            preferred_resume_name TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_login_at TEXT
        )
        """
    )
    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(users)").fetchall()
    }
    if "preferred_resume_doc_id" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN preferred_resume_doc_id TEXT")
    if "preferred_resume_name" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN preferred_resume_name TEXT")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_skill_inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            canonical_name TEXT NOT NULL,
            display_name TEXT NOT NULL,
            category TEXT DEFAULT 'general',
            state TEXT DEFAULT 'confirmed',
            source TEXT DEFAULT 'user_manual',
            confidence REAL DEFAULT 1.0,
            evidence_snippet TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, canonical_name, state),
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_quality_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            doc_id TEXT NOT NULL,
            report_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, doc_id),
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_metric_inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            raw_value TEXT NOT NULL,
            normalized_value TEXT NOT NULL,
            category TEXT DEFAULT 'number',
            evidence_line TEXT,
            source TEXT DEFAULT 'user_confirmed',
            state TEXT DEFAULT 'confirmed',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, normalized_value, state),
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_improved_resumes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            doc_id TEXT NOT NULL,
            resume_text TEXT NOT NULL,
            score INTEGER DEFAULT 0,
            metadata_json TEXT DEFAULT '{}',
            version INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, doc_id),
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_users_google_sub ON users(google_sub)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_user_skills_user_id ON user_skill_inventory(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_user_metrics_user_id ON user_metric_inventory(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_quality_reports_user_doc ON user_quality_reports(user_id, doc_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_improved_resumes_user_doc ON user_improved_resumes(user_id, doc_id)")
    conn.commit()


def upsert_google_user(*, google_sub: str, email: str, name: Optional[str], picture_url: Optional[str]) -> Dict[str, Any]:
    """Create or update a local user record from Google identity data."""
    now = _utcnow()
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT * FROM users WHERE google_sub = ?", (google_sub,)).fetchone()
        if row:
            conn.execute(
                """
                UPDATE users
                SET email = ?, name = ?, picture_url = ?, updated_at = ?, last_login_at = ?
                WHERE google_sub = ?
                """,
                (email, name, picture_url, now, now, google_sub),
            )
        else:
            conn.execute(
                """
                INSERT INTO users (google_sub, email, name, picture_url, created_at, updated_at, last_login_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (google_sub, email, name, picture_url, now, now, now),
            )
        conn.commit()
        updated = conn.execute("SELECT * FROM users WHERE google_sub = ?", (google_sub,)).fetchone()
        return dict(updated) if updated else {}
    finally:
        conn.close()


def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def set_user_preferred_resume(user_id: int, doc_id: str, name: Optional[str]) -> Optional[Dict[str, Any]]:
    now = _utcnow()
    conn = get_db_connection()
    try:
        conn.execute(
            """
            UPDATE users
            SET preferred_resume_doc_id = ?, preferred_resume_name = ?, updated_at = ?
            WHERE id = ?
            """,
            (doc_id, name, now, user_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _normalize_skill(skill: str) -> tuple[str, str]:
    display_name = (skill or "").strip()
    canonical_name = " ".join(display_name.lower().split())
    return canonical_name, display_name


def get_user_skills(user_id: int, state: str = "confirmed") -> List[str]:
    conn = get_db_connection()
    try:
        rows = conn.execute(
            """
            SELECT display_name
            FROM user_skill_inventory
            WHERE user_id = ? AND state = ?
            ORDER BY LOWER(display_name)
            """,
            (user_id, state),
        ).fetchall()
        return [row["display_name"] for row in rows]
    finally:
        conn.close()


def get_user_skill_records(user_id: int, state: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        if state is None:
            rows = conn.execute(
                """
                SELECT display_name, canonical_name, category, state, source, confidence, evidence_snippet
                FROM user_skill_inventory
                WHERE user_id = ?
                ORDER BY state, LOWER(display_name)
                """,
                (user_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT display_name, canonical_name, category, state, source, confidence, evidence_snippet
                FROM user_skill_inventory
                WHERE user_id = ? AND state = ?
                ORDER BY LOWER(display_name)
                """,
                (user_id, state),
            ).fetchall()
        return [
            {
                "name": row["display_name"],
                "canonical_name": row["canonical_name"],
                "category": row["category"],
                "state": row["state"],
                "source": row["source"],
                "confidence": row["confidence"],
                "evidence_snippet": row["evidence_snippet"],
            }
            for row in rows
        ]
    finally:
        conn.close()


def add_user_skill(user_id: int, skill: str, *, state: str = "confirmed", source: str = "user_manual") -> List[str]:
    canonical_name, display_name = _normalize_skill(skill)
    if not display_name:
        return get_user_skills(user_id, state=state)
    now = _utcnow()
    conn = get_db_connection()
    try:
        conn.execute(
            """
            INSERT INTO user_skill_inventory (
                user_id, canonical_name, display_name, category, state, source, confidence, created_at, updated_at
            ) VALUES (?, ?, ?, 'general', ?, ?, 1.0, ?, ?)
            ON CONFLICT(user_id, canonical_name, state)
            DO UPDATE SET display_name = excluded.display_name, source = excluded.source, updated_at = excluded.updated_at
            """,
            (user_id, canonical_name, display_name, state, source, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return get_user_skills(user_id, state=state)


def remove_user_skill(user_id: int, skill: str, *, state: str = "confirmed") -> bool:
    canonical_name, _ = _normalize_skill(skill)
    conn = get_db_connection()
    try:
        cursor = conn.execute(
            "DELETE FROM user_skill_inventory WHERE user_id = ? AND canonical_name = ? AND state = ?",
            (user_id, canonical_name, state),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def update_user_skill(user_id: int, old_skill: str, new_skill: str, *, state: str = "confirmed") -> bool:
    old_canonical, _ = _normalize_skill(old_skill)
    new_canonical, new_display = _normalize_skill(new_skill)
    if not new_display:
        return False
    now = _utcnow()
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT id FROM user_skill_inventory WHERE user_id = ? AND canonical_name = ? AND state = ?",
            (user_id, old_canonical, state),
        ).fetchone()
        if not row:
            return False
        conn.execute(
            """
            UPDATE user_skill_inventory
            SET canonical_name = ?, display_name = ?, updated_at = ?
            WHERE id = ?
            """,
            (new_canonical, new_display, now, row["id"]),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def replace_user_skills(user_id: int, skills: List[str], *, state: str = "confirmed", source: str = "user_manual") -> List[str]:
    now = _utcnow()
    deduped: List[tuple[str, str]] = []
    seen = set()
    for raw_skill in skills:
        canonical_name, display_name = _normalize_skill(raw_skill)
        if not display_name or canonical_name in seen:
            continue
        seen.add(canonical_name)
        deduped.append((canonical_name, display_name))

    conn = get_db_connection()
    try:
        conn.execute(
            "DELETE FROM user_skill_inventory WHERE user_id = ? AND state = ?",
            (user_id, state),
        )
        conn.executemany(
            """
            INSERT INTO user_skill_inventory (
                user_id, canonical_name, display_name, category, state, source, confidence, created_at, updated_at
            ) VALUES (?, ?, ?, 'general', ?, ?, 1.0, ?, ?)
            """,
            [(user_id, canonical, display, state, source, now, now) for canonical, display in deduped],
        )
        conn.commit()
    finally:
        conn.close()
    return [display for _, display in deduped]


def replace_user_skill_records(
    user_id: int,
    records: Sequence[Dict[str, Any]],
    *,
    state: str,
    source: str,
) -> List[Dict[str, Any]]:
    now = _utcnow()
    prepared: List[tuple[str, str, str, str, float, Optional[str]]] = []
    seen = set()
    for record in records:
        canonical_name, display_name = _normalize_skill(str(record.get("name", "")))
        if not display_name or canonical_name in seen:
            continue
        seen.add(canonical_name)
        prepared.append(
            (
                canonical_name,
                display_name,
                str(record.get("category") or "general"),
                str(record.get("source") or source),
                float(record.get("confidence") or 0.5),
                record.get("evidence_snippet"),
            )
        )

    conn = get_db_connection()
    try:
        conn.execute(
            "DELETE FROM user_skill_inventory WHERE user_id = ? AND state = ?",
            (user_id, state),
        )
        conn.executemany(
            """
            INSERT INTO user_skill_inventory (
                user_id, canonical_name, display_name, category, state, source, confidence, evidence_snippet, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (user_id, canonical, display, category, state, item_source, confidence, evidence, now, now)
                for canonical, display, category, item_source, confidence, evidence in prepared
            ],
        )
        conn.commit()
    finally:
        conn.close()
    return get_user_skill_records(user_id, state=state)


def get_user_metric_records(user_id: int, state: str = "confirmed") -> List[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        rows = conn.execute(
            """
            SELECT raw_value, normalized_value, category, evidence_line, source, state
            FROM user_metric_inventory
            WHERE user_id = ? AND state = ?
            ORDER BY LOWER(raw_value)
            """,
            (user_id, state),
        ).fetchall()
        return [
            {
                "raw": row["raw_value"],
                "normalized": row["normalized_value"],
                "category": row["category"],
                "line": row["evidence_line"],
                "source": row["source"],
                "state": row["state"],
            }
            for row in rows
        ]
    finally:
        conn.close()


def replace_user_metric_records(
    user_id: int,
    records: Sequence[Dict[str, Any]],
    *,
    state: str = "confirmed",
    source: str = "user_confirmed",
) -> List[Dict[str, Any]]:
    now = _utcnow()
    prepared: List[tuple[str, str, str, Optional[str], str]] = []
    seen = set()
    for record in records:
        raw_value = str(record.get("raw") or "").strip()
        normalized = str(record.get("normalized") or "").strip()
        if not raw_value or not normalized or normalized in seen:
            continue
        seen.add(normalized)
        prepared.append(
            (
                raw_value,
                normalized,
                str(record.get("category") or "number"),
                record.get("line"),
                str(record.get("source") or source),
            )
        )

    conn = get_db_connection()
    try:
        conn.execute(
            "DELETE FROM user_metric_inventory WHERE user_id = ? AND state = ?",
            (user_id, state),
        )
        conn.executemany(
            """
            INSERT INTO user_metric_inventory (
                user_id, raw_value, normalized_value, category, evidence_line, source, state, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (user_id, raw_value, normalized, category, line, metric_source, state, now, now)
                for raw_value, normalized, category, line, metric_source in prepared
            ],
        )
        conn.commit()
    finally:
        conn.close()
    return get_user_metric_records(user_id, state=state)


def save_quality_report_for_user(user_id: int, doc_id: Optional[str], report: Dict[str, Any]) -> Dict[str, Any]:
    key = doc_id or "latest"
    now = _utcnow()
    payload = json.dumps(report)
    conn = get_db_connection()
    try:
        for row_key in {key, "latest"}:
            conn.execute(
                """
                INSERT INTO user_quality_reports (user_id, doc_id, report_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, doc_id)
                DO UPDATE SET report_json = excluded.report_json, updated_at = excluded.updated_at
                """,
                (user_id, row_key, payload, now, now),
            )
        conn.commit()
        return {"report": report, "doc_id": doc_id, "updated_at": now}
    finally:
        conn.close()


def get_quality_report_for_user(user_id: int, doc_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    key = doc_id or "latest"
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT report_json, updated_at FROM user_quality_reports WHERE user_id = ? AND doc_id = ?",
            (user_id, key),
        ).fetchone()
        if not row:
            return None
        return {
            "report": json.loads(row["report_json"]),
            "doc_id": doc_id,
            "updated_at": row["updated_at"],
        }
    finally:
        conn.close()


def clear_quality_report_for_user(user_id: int, doc_id: Optional[str] = None) -> None:
    conn = get_db_connection()
    try:
        if doc_id:
            conn.execute(
                "DELETE FROM user_quality_reports WHERE user_id = ? AND doc_id IN (?, 'latest')",
                (user_id, doc_id),
            )
        else:
            conn.execute(
                "DELETE FROM user_quality_reports WHERE user_id = ?",
                (user_id,),
            )
        conn.commit()
    finally:
        conn.close()


def save_improved_resume_for_user(
    user_id: int,
    resume_text: str,
    *,
    original_doc_id: Optional[str] = None,
    score: int = 0,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    key = original_doc_id or "latest"
    now = _utcnow()
    metadata = metadata or {}
    conn = get_db_connection()
    try:
        existing = conn.execute(
            "SELECT version FROM user_improved_resumes WHERE user_id = ? AND doc_id = ?",
            (user_id, key),
        ).fetchone()
        version = (existing["version"] if existing else 0) + 1
        for row_key in {key, "latest"}:
            conn.execute(
                """
                INSERT INTO user_improved_resumes (
                    user_id, doc_id, resume_text, score, metadata_json, version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, doc_id)
                DO UPDATE SET
                    resume_text = excluded.resume_text,
                    score = excluded.score,
                    metadata_json = excluded.metadata_json,
                    version = excluded.version,
                    updated_at = excluded.updated_at
                """,
                (user_id, row_key, resume_text, score, json.dumps(metadata), version, now, now),
            )
        conn.commit()
        return {
            "text": resume_text,
            "score": score,
            "original_doc_id": original_doc_id,
            "metadata": metadata,
            "updated_at": now,
            "version": version,
        }
    finally:
        conn.close()


def get_improved_resume_for_user(user_id: int, doc_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    key = doc_id or "latest"
    conn = get_db_connection()
    try:
        row = conn.execute(
            """
            SELECT resume_text, score, metadata_json, version, updated_at
            FROM user_improved_resumes
            WHERE user_id = ? AND doc_id = ?
            """,
            (user_id, key),
        ).fetchone()
        if not row:
            return None
        return {
            "text": row["resume_text"],
            "score": row["score"],
            "original_doc_id": doc_id,
            "metadata": json.loads(row["metadata_json"] or "{}"),
            "updated_at": row["updated_at"],
            "version": row["version"],
        }
    finally:
        conn.close()


def clear_improved_resume_for_user(user_id: int, doc_id: Optional[str] = None) -> None:
    conn = get_db_connection()
    try:
        if doc_id:
            conn.execute(
                "DELETE FROM user_improved_resumes WHERE user_id = ? AND doc_id IN (?, 'latest')",
                (user_id, doc_id),
            )
        else:
            conn.execute(
                "DELETE FROM user_improved_resumes WHERE user_id = ?",
                (user_id,),
            )
        conn.commit()
    finally:
        conn.close()


def migrate_legacy_skills_to_user(user_id: int, skills: List[str]) -> None:
    """Import legacy file-backed skills into SQLite if the user has no stored skills yet."""
    existing = get_user_skills(user_id)
    if existing or not skills:
        return
    replace_user_skills(user_id, skills, state="confirmed", source="legacy_memory")
    logger.info("Migrated legacy skills into SQLite user profile", user_id=user_id, skill_count=len(skills))
