"""
SQLite-backed local user/profile store.

This module is the first step in moving user-scoped state out of local JSON
files and into a durable, authenticated local data store.
"""

from __future__ import annotations

import json
import sqlite3
from hashlib import sha256
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from ..config import settings
from ..utils.logger import logger


DB_PATH = settings.resolved_application_db_path


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value or [])


def _json_loads(value: Optional[str], fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS job_strategy_briefs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            company TEXT NOT NULL,
            job_title TEXT NOT NULL,
            job_url TEXT,
            archetype TEXT DEFAULT 'general',
            status TEXT DEFAULT 'pending',
            strategy_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS job_strategy_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            strategy_brief_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            event_payload_json TEXT DEFAULT '{}',
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(strategy_brief_id) REFERENCES job_strategy_briefs(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_evidence_inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            tags_json TEXT DEFAULT '[]',
            source TEXT DEFAULT 'user_manual',
            state TEXT DEFAULT 'confirmed',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_target_archetypes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            archetype TEXT NOT NULL,
            tier TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, archetype),
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS discovered_roles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            canonical_url TEXT NOT NULL,
            source_urls_json TEXT NOT NULL DEFAULT '[]',
            source_domain TEXT NOT NULL,
            company TEXT NOT NULL,
            job_title TEXT NOT NULL,
            matched_title_variant TEXT,
            location TEXT,
            remote_mode TEXT NOT NULL DEFAULT 'unknown',
            employment_type TEXT NOT NULL DEFAULT 'unknown',
            apply_url TEXT,
            posted_at TEXT,
            posted_label TEXT,
            date_confidence TEXT NOT NULL DEFAULT 'unknown',
            archetype TEXT NOT NULL DEFAULT 'unknown',
            extraction_confidence REAL NOT NULL DEFAULT 0.0,
            raw_text TEXT NOT NULL DEFAULT '',
            raw_text_hash TEXT,
            short_tldr TEXT NOT NULL DEFAULT '',
            matched_filters_json TEXT NOT NULL DEFAULT '[]',
            possible_blockers_json TEXT NOT NULL DEFAULT '[]',
            rank_score REAL NOT NULL DEFAULT 0.0,
            inbox_state TEXT NOT NULL DEFAULT 'discovered',
            opened_in_tailor_at TEXT,
            opened_strategy_brief_id INTEGER,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            last_scraped_at TEXT,
            last_ranked_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, canonical_url),
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(opened_strategy_brief_id) REFERENCES job_strategy_briefs(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS discovered_role_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            discovered_role_id INTEGER NOT NULL,
            decision TEXT NOT NULL,
            reasons_json TEXT NOT NULL DEFAULT '[]',
            comment TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(discovered_role_id) REFERENCES discovered_roles(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS discovery_saved_searches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            criteria_json TEXT NOT NULL DEFAULT '{}',
            is_default INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_used_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS discovery_user_preferences (
            user_id INTEGER PRIMARY KEY,
            defaults_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS discovery_suggestion_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            suggestion_key TEXT NOT NULL,
            action TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_users_google_sub ON users(google_sub)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_user_skills_user_id ON user_skill_inventory(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_user_metrics_user_id ON user_metric_inventory(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_quality_reports_user_doc ON user_quality_reports(user_id, doc_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_improved_resumes_user_doc ON user_improved_resumes(user_id, doc_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_strategy_briefs_user_job ON job_strategy_briefs(user_id, company, job_title)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_strategy_events_brief ON job_strategy_events(strategy_brief_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_user_evidence_user_state ON user_evidence_inventory(user_id, state, kind)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_user_target_archetypes_user ON user_target_archetypes(user_id, tier)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_discovered_roles_user_state ON discovered_roles(user_id, inbox_state)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_discovered_roles_user_rank ON discovered_roles(user_id, rank_score DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_discovered_roles_user_seen ON discovered_roles(user_id, last_seen_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_discovered_roles_user_posted ON discovered_roles(user_id, posted_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_discovered_roles_hash ON discovered_roles(raw_text_hash)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_discovered_feedback_user_role ON discovered_role_feedback(user_id, discovered_role_id, created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_discovery_saved_searches_user ON discovery_saved_searches(user_id, updated_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_discovery_suggestions_user_key ON discovery_suggestion_events(user_id, suggestion_key, created_at DESC)")
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


DISCOVERED_ROLE_INBOX_STATES = {"discovered", "shortlisted", "dismissed"}


def _normalize_discovered_role_row(row: sqlite3.Row) -> Dict[str, Any]:
    payload = dict(row)
    payload["source_urls"] = _json_loads(payload.pop("source_urls_json", "[]"), [])
    payload["matched_filters"] = _json_loads(payload.pop("matched_filters_json", "[]"), [])
    payload["possible_blockers"] = _json_loads(payload.pop("possible_blockers_json", "[]"), [])
    return payload


def _normalize_discovered_feedback_row(row: sqlite3.Row) -> Dict[str, Any]:
    payload = dict(row)
    payload["reasons"] = _json_loads(payload.pop("reasons_json", "[]"), [])
    return payload


def save_or_merge_discovered_role_for_user(user_id: int, role_payload: Dict[str, Any]) -> Dict[str, Any]:
    now = _utcnow()
    canonical_url = str(role_payload.get("canonical_url") or "").strip()
    if not canonical_url:
        raise ValueError("canonical_url is required")

    source_urls = sorted({str(url).strip() for url in (role_payload.get("source_urls") or []) if str(url).strip()})
    if canonical_url not in source_urls:
        source_urls.append(canonical_url)

    raw_text = str(role_payload.get("raw_text") or "")
    raw_text_hash = role_payload.get("raw_text_hash") or (sha256(raw_text.encode("utf-8")).hexdigest() if raw_text else None)

    conn = get_db_connection()
    try:
        existing = conn.execute(
            """
            SELECT id, inbox_state, source_urls_json, first_seen_at, opened_in_tailor_at, opened_strategy_brief_id
            FROM discovered_roles
            WHERE user_id = ? AND canonical_url = ?
            """,
            (user_id, canonical_url),
        ).fetchone()

        if existing:
            merged_source_urls = sorted(
                {
                    *(_json_loads(existing["source_urls_json"], []) or []),
                    *source_urls,
                }
            )
            conn.execute(
                """
                UPDATE discovered_roles
                SET source_urls_json = ?, source_domain = ?, company = ?, job_title = ?, matched_title_variant = ?,
                    location = ?, remote_mode = ?, employment_type = ?, apply_url = ?, posted_at = ?, posted_label = ?,
                    date_confidence = ?, archetype = ?, extraction_confidence = ?, raw_text = ?, raw_text_hash = ?,
                    short_tldr = ?, matched_filters_json = ?, possible_blockers_json = ?, rank_score = ?,
                    last_seen_at = ?, last_scraped_at = ?, last_ranked_at = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (
                    _json_dumps(merged_source_urls),
                    str(role_payload.get("source_domain") or ""),
                    str(role_payload.get("company") or ""),
                    str(role_payload.get("job_title") or ""),
                    role_payload.get("matched_title_variant"),
                    role_payload.get("location"),
                    str(role_payload.get("remote_mode") or "unknown"),
                    str(role_payload.get("employment_type") or "unknown"),
                    role_payload.get("apply_url"),
                    role_payload.get("posted_at"),
                    role_payload.get("posted_label"),
                    str(role_payload.get("date_confidence") or "unknown"),
                    str(role_payload.get("archetype") or "unknown"),
                    float(role_payload.get("extraction_confidence") or 0.0),
                    raw_text,
                    raw_text_hash,
                    str(role_payload.get("short_tldr") or ""),
                    _json_dumps(role_payload.get("matched_filters") or []),
                    _json_dumps(role_payload.get("possible_blockers") or []),
                    float(role_payload.get("rank_score") or 0.0),
                    now,
                    role_payload.get("last_scraped_at") or now,
                    role_payload.get("last_ranked_at") or now,
                    now,
                    existing["id"],
                    user_id,
                ),
            )
            role_id = existing["id"]
        else:
            cursor = conn.execute(
                """
                INSERT INTO discovered_roles (
                    user_id, canonical_url, source_urls_json, source_domain, company, job_title, matched_title_variant,
                    location, remote_mode, employment_type, apply_url, posted_at, posted_label, date_confidence,
                    archetype, extraction_confidence, raw_text, raw_text_hash, short_tldr, matched_filters_json,
                    possible_blockers_json, rank_score, inbox_state, opened_in_tailor_at, opened_strategy_brief_id,
                    first_seen_at, last_seen_at, last_scraped_at, last_ranked_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'discovered', NULL, NULL, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    canonical_url,
                    _json_dumps(source_urls),
                    str(role_payload.get("source_domain") or ""),
                    str(role_payload.get("company") or ""),
                    str(role_payload.get("job_title") or ""),
                    role_payload.get("matched_title_variant"),
                    role_payload.get("location"),
                    str(role_payload.get("remote_mode") or "unknown"),
                    str(role_payload.get("employment_type") or "unknown"),
                    role_payload.get("apply_url"),
                    role_payload.get("posted_at"),
                    role_payload.get("posted_label"),
                    str(role_payload.get("date_confidence") or "unknown"),
                    str(role_payload.get("archetype") or "unknown"),
                    float(role_payload.get("extraction_confidence") or 0.0),
                    raw_text,
                    raw_text_hash,
                    str(role_payload.get("short_tldr") or ""),
                    _json_dumps(role_payload.get("matched_filters") or []),
                    _json_dumps(role_payload.get("possible_blockers") or []),
                    float(role_payload.get("rank_score") or 0.0),
                    now,
                    now,
                    role_payload.get("last_scraped_at") or now,
                    role_payload.get("last_ranked_at") or now,
                    now,
                    now,
                ),
            )
            role_id = cursor.lastrowid

        conn.commit()
        row = conn.execute("SELECT * FROM discovered_roles WHERE user_id = ? AND id = ?", (user_id, role_id)).fetchone()
        return _normalize_discovered_role_row(row) if row else {}
    finally:
        conn.close()


def get_discovered_role_for_user(user_id: int, role_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT * FROM discovered_roles WHERE user_id = ? AND id = ?",
            (user_id, role_id),
        ).fetchone()
        return _normalize_discovered_role_row(row) if row else None
    finally:
        conn.close()


def list_discovered_roles_for_user(
    user_id: int,
    inbox_state: str = "active",
    search: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    limit = max(1, min(int(limit or 50), 100))
    normalized_state = (inbox_state or "active").strip().lower()
    clauses = ["user_id = ?"]
    params: List[Any] = [user_id]

    if normalized_state == "active":
        clauses.append("inbox_state IN ('discovered', 'shortlisted')")
        order_by = "CASE WHEN inbox_state = 'shortlisted' THEN 0 ELSE 1 END, rank_score DESC, posted_at DESC, last_seen_at DESC"
    elif normalized_state == "all":
        order_by = "CASE WHEN inbox_state = 'shortlisted' THEN 0 WHEN inbox_state = 'discovered' THEN 1 ELSE 2 END, rank_score DESC, posted_at DESC, last_seen_at DESC"
    elif normalized_state == "dismissed":
        clauses.append("inbox_state = 'dismissed'")
        order_by = """
            (
                SELECT MAX(created_at)
                FROM discovered_role_feedback feedback
                WHERE feedback.user_id = discovered_roles.user_id
                  AND feedback.discovered_role_id = discovered_roles.id
            ) DESC,
            updated_at DESC
        """
    elif normalized_state in {"discovered", "shortlisted"}:
        clauses.append("inbox_state = ?")
        params.append(normalized_state)
        order_by = "rank_score DESC, posted_at DESC, last_seen_at DESC"
    else:
        raise ValueError("Unsupported inbox_state")

    if search and search.strip():
        token = f"%{search.strip().lower()}%"
        clauses.append(
            "(LOWER(company) LIKE ? OR LOWER(job_title) LIKE ? OR LOWER(location) LIKE ? OR LOWER(short_tldr) LIKE ?)"
        )
        params.extend([token, token, token, token])

    conn = get_db_connection()
    try:
        rows = conn.execute(
            f"""
            SELECT *
            FROM discovered_roles
            WHERE {' AND '.join(clauses)}
            ORDER BY {order_by}
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        return [_normalize_discovered_role_row(row) for row in rows]
    finally:
        conn.close()


def record_discovered_role_feedback_for_user(
    user_id: int,
    role_id: int,
    decision: str,
    reasons: Optional[Sequence[str]],
    comment: Optional[str],
) -> Dict[str, Any]:
    now = _utcnow()
    conn = get_db_connection()
    try:
        cursor = conn.execute(
            """
            INSERT INTO discovered_role_feedback (
                user_id, discovered_role_id, decision, reasons_json, comment, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, role_id, decision, _json_dumps(list(reasons or [])), (comment or None), now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM discovered_role_feedback WHERE id = ? AND user_id = ?",
            (cursor.lastrowid, user_id),
        ).fetchone()
        return _normalize_discovered_feedback_row(row) if row else {}
    finally:
        conn.close()


def list_discovered_role_feedback_for_user(user_id: int, role_id: int, limit: int = 5) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM discovered_role_feedback
            WHERE user_id = ? AND discovered_role_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (user_id, role_id, limit),
        ).fetchall()
        return [_normalize_discovered_feedback_row(row) for row in rows]
    finally:
        conn.close()


def update_discovered_role_inbox_state_for_user(user_id: int, role_id: int, inbox_state: str) -> Dict[str, Any]:
    normalized_state = (inbox_state or "").strip().lower()
    if normalized_state not in DISCOVERED_ROLE_INBOX_STATES:
        raise ValueError("Unsupported inbox_state")
    now = _utcnow()
    conn = get_db_connection()
    try:
        conn.execute(
            """
            UPDATE discovered_roles
            SET inbox_state = ?, updated_at = ?
            WHERE user_id = ? AND id = ?
            """,
            (normalized_state, now, user_id, role_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM discovered_roles WHERE user_id = ? AND id = ?", (user_id, role_id)).fetchone()
        return _normalize_discovered_role_row(row) if row else {}
    finally:
        conn.close()


def mark_discovered_role_opened_in_tailor_for_user(user_id: int, role_id: int) -> Dict[str, Any]:
    now = _utcnow()
    conn = get_db_connection()
    try:
        conn.execute(
            """
            UPDATE discovered_roles
            SET opened_in_tailor_at = ?, updated_at = ?
            WHERE user_id = ? AND id = ?
            """,
            (now, now, user_id, role_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM discovered_roles WHERE user_id = ? AND id = ?", (user_id, role_id)).fetchone()
        return _normalize_discovered_role_row(row) if row else {}
    finally:
        conn.close()


def link_discovered_role_strategy_brief_for_user(user_id: int, role_id: int, strategy_brief_id: int) -> None:
    now = _utcnow()
    conn = get_db_connection()
    try:
        conn.execute(
            """
            UPDATE discovered_roles
            SET opened_strategy_brief_id = ?, updated_at = ?
            WHERE user_id = ? AND id = ?
            """,
            (strategy_brief_id, now, user_id, role_id),
        )
        conn.commit()
    finally:
        conn.close()


def _normalize_saved_search_row(row: sqlite3.Row) -> Dict[str, Any]:
    payload = dict(row)
    payload["criteria"] = _json_loads(payload.pop("criteria_json", "{}"), {})
    payload["is_default"] = bool(payload.get("is_default"))
    return payload


def save_discovery_saved_search_for_user(
    user_id: int,
    *,
    name: str,
    criteria: Dict[str, Any],
    search_id: Optional[int] = None,
    is_default: bool = False,
) -> Dict[str, Any]:
    now = _utcnow()
    conn = get_db_connection()
    try:
        if is_default:
            conn.execute("UPDATE discovery_saved_searches SET is_default = 0 WHERE user_id = ?", (user_id,))
        if search_id:
            conn.execute(
                """
                UPDATE discovery_saved_searches
                SET name = ?, criteria_json = ?, is_default = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (name.strip(), json.dumps(criteria or {}), 1 if is_default else 0, now, search_id, user_id),
            )
            saved_id = search_id
        else:
            cursor = conn.execute(
                """
                INSERT INTO discovery_saved_searches (
                    user_id, name, criteria_json, is_default, created_at, updated_at, last_used_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, name.strip(), json.dumps(criteria or {}), 1 if is_default else 0, now, now, None),
            )
            saved_id = cursor.lastrowid
        conn.commit()
        row = conn.execute("SELECT * FROM discovery_saved_searches WHERE user_id = ? AND id = ?", (user_id, saved_id)).fetchone()
        return _normalize_saved_search_row(row) if row else {}
    finally:
        conn.close()


def list_discovery_saved_searches_for_user(user_id: int) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM discovery_saved_searches
            WHERE user_id = ?
            ORDER BY is_default DESC, updated_at DESC, id DESC
            """,
            (user_id,),
        ).fetchall()
        return [_normalize_saved_search_row(row) for row in rows]
    finally:
        conn.close()


def get_discovery_saved_search_for_user(user_id: int, search_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT * FROM discovery_saved_searches WHERE user_id = ? AND id = ?",
            (user_id, search_id),
        ).fetchone()
        return _normalize_saved_search_row(row) if row else None
    finally:
        conn.close()


def delete_discovery_saved_search_for_user(user_id: int, search_id: int) -> bool:
    conn = get_db_connection()
    try:
        cursor = conn.execute(
            "DELETE FROM discovery_saved_searches WHERE user_id = ? AND id = ?",
            (user_id, search_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def mark_discovery_saved_search_used_for_user(user_id: int, search_id: int) -> None:
    now = _utcnow()
    conn = get_db_connection()
    try:
        conn.execute(
            """
            UPDATE discovery_saved_searches
            SET last_used_at = ?, updated_at = ?
            WHERE user_id = ? AND id = ?
            """,
            (now, now, user_id, search_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_discovery_user_preferences_for_user(user_id: int) -> Dict[str, Any]:
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT defaults_json, created_at, updated_at FROM discovery_user_preferences WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            return {"defaults": {}, "created_at": None, "updated_at": None}
        return {
            "defaults": _json_loads(row["defaults_json"], {}),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    finally:
        conn.close()


def save_discovery_user_preferences_for_user(user_id: int, defaults: Dict[str, Any]) -> Dict[str, Any]:
    now = _utcnow()
    conn = get_db_connection()
    try:
        conn.execute(
            """
            INSERT INTO discovery_user_preferences (user_id, defaults_json, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id)
            DO UPDATE SET defaults_json = excluded.defaults_json, updated_at = excluded.updated_at
            """,
            (user_id, json.dumps(defaults or {}), now, now),
        )
        conn.commit()
        return get_discovery_user_preferences_for_user(user_id)
    finally:
        conn.close()


def record_discovery_suggestion_event_for_user(
    user_id: int,
    *,
    suggestion_key: str,
    action: str,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    now = _utcnow()
    conn = get_db_connection()
    try:
        cursor = conn.execute(
            """
            INSERT INTO discovery_suggestion_events (
                user_id, suggestion_key, action, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, suggestion_key, action, json.dumps(payload or {}), now),
        )
        conn.commit()
        return {
            "id": cursor.lastrowid,
            "suggestion_key": suggestion_key,
            "action": action,
            "payload": payload or {},
            "created_at": now,
        }
    finally:
        conn.close()


def get_latest_discovery_suggestion_event_for_user(user_id: int, suggestion_key: str) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        row = conn.execute(
            """
            SELECT id, suggestion_key, action, payload_json, created_at
            FROM discovery_suggestion_events
            WHERE user_id = ? AND suggestion_key = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (user_id, suggestion_key),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "suggestion_key": row["suggestion_key"],
            "action": row["action"],
            "payload": _json_loads(row["payload_json"], {}),
            "created_at": row["created_at"],
        }
    finally:
        conn.close()


def save_job_strategy_brief_for_user(
    user_id: int,
    brief: Dict[str, Any],
    *,
    brief_id: Optional[int] = None,
) -> Dict[str, Any]:
    now = _utcnow()
    payload = dict(brief)
    payload["updated_at"] = now
    payload.setdefault("created_at", now)
    conn = get_db_connection()
    try:
        if brief_id:
            conn.execute(
                """
                UPDATE job_strategy_briefs
                SET company = ?, job_title = ?, job_url = ?, archetype = ?, status = ?, strategy_json = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (
                    str(payload.get("company") or ""),
                    str(payload.get("job_title") or ""),
                    payload.get("job_url"),
                    str(payload.get("archetype") or "general"),
                    str(payload.get("approval_status") or payload.get("status") or "pending"),
                    json.dumps(payload),
                    now,
                    brief_id,
                    user_id,
                ),
            )
            stored_id = brief_id
        else:
            cursor = conn.execute(
                """
                INSERT INTO job_strategy_briefs (
                    user_id, company, job_title, job_url, archetype, status, strategy_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    str(payload.get("company") or ""),
                    str(payload.get("job_title") or ""),
                    payload.get("job_url"),
                    str(payload.get("archetype") or "general"),
                    str(payload.get("approval_status") or payload.get("status") or "pending"),
                    json.dumps(payload),
                    now,
                    now,
                ),
            )
            stored_id = cursor.lastrowid
        conn.commit()
        payload["id"] = stored_id
        return payload
    finally:
        conn.close()


def get_job_strategy_brief_for_user(user_id: int, brief_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        row = conn.execute(
            """
            SELECT strategy_json, created_at, updated_at
            FROM job_strategy_briefs
            WHERE user_id = ? AND id = ?
            """,
            (user_id, brief_id),
        ).fetchone()
        if not row:
            return None
        payload = json.loads(row["strategy_json"])
        payload["id"] = brief_id
        payload.setdefault("created_at", row["created_at"])
        payload.setdefault("updated_at", row["updated_at"])
        return payload
    finally:
        conn.close()


def find_job_strategy_brief_for_user(
    user_id: int,
    *,
    company: str,
    job_title: str,
) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        row = conn.execute(
            """
            SELECT id, strategy_json, created_at, updated_at
            FROM job_strategy_briefs
            WHERE user_id = ? AND LOWER(company) = LOWER(?) AND LOWER(job_title) = LOWER(?)
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (user_id, company.strip(), job_title.strip()),
        ).fetchone()
        if not row:
            return None
        payload = json.loads(row["strategy_json"])
        payload["id"] = row["id"]
        payload.setdefault("created_at", row["created_at"])
        payload.setdefault("updated_at", row["updated_at"])
        return payload
    finally:
        conn.close()


def list_job_strategy_briefs_for_user(user_id: int, *, limit: int = 50) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        rows = conn.execute(
            """
            SELECT id, strategy_json, created_at, updated_at
            FROM job_strategy_briefs
            WHERE user_id = ?
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
        results: List[Dict[str, Any]] = []
        for row in rows:
            payload = json.loads(row["strategy_json"])
            payload["id"] = row["id"]
            payload.setdefault("created_at", row["created_at"])
            payload.setdefault("updated_at", row["updated_at"])
            results.append(payload)
        return results
    finally:
        conn.close()


def update_job_strategy_brief_status_for_user(user_id: int, brief_id: int, status: str) -> Optional[Dict[str, Any]]:
    brief = get_job_strategy_brief_for_user(user_id, brief_id)
    if not brief:
        return None
    brief["approval_status"] = status
    return save_job_strategy_brief_for_user(user_id, brief, brief_id=brief_id)


def add_job_strategy_event_for_user(
    user_id: int,
    *,
    strategy_brief_id: int,
    event_type: str,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    now = _utcnow()
    payload_json = json.dumps(payload or {})
    conn = get_db_connection()
    try:
        cursor = conn.execute(
            """
            INSERT INTO job_strategy_events (
                user_id, strategy_brief_id, event_type, event_payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, strategy_brief_id, event_type, payload_json, now),
        )
        conn.commit()
        return {
            "id": cursor.lastrowid,
            "strategy_brief_id": strategy_brief_id,
            "event_type": event_type,
            "payload": payload or {},
            "created_at": now,
        }
    finally:
        conn.close()


def list_job_strategy_events_for_user(user_id: int, strategy_brief_id: int) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        rows = conn.execute(
            """
            SELECT id, event_type, event_payload_json, created_at
            FROM job_strategy_events
            WHERE user_id = ? AND strategy_brief_id = ?
            ORDER BY id ASC
            """,
            (user_id, strategy_brief_id),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "strategy_brief_id": strategy_brief_id,
                "event_type": row["event_type"],
                "payload": json.loads(row["event_payload_json"] or "{}"),
                "created_at": row["created_at"],
            }
            for row in rows
        ]
    finally:
        conn.close()


def replace_user_evidence_records(
    user_id: int,
    records: Sequence[Dict[str, Any]],
    *,
    state: str = "confirmed",
    source: str = "user_manual",
) -> List[Dict[str, Any]]:
    now = _utcnow()
    conn = get_db_connection()
    try:
        conn.execute(
            "DELETE FROM user_evidence_inventory WHERE user_id = ? AND state = ?",
            (user_id, state),
        )
        prepared = []
        for record in records:
            kind = str(record.get("kind") or "").strip()
            title = str(record.get("title") or "").strip()
            content = str(record.get("content") or "").strip()
            if not kind or not title or not content:
                continue
            tags = [str(tag).strip() for tag in record.get("tags", []) if str(tag).strip()]
            prepared.append(
                (
                    user_id,
                    kind,
                    title,
                    content,
                    json.dumps(tags),
                    str(record.get("source") or source),
                    state,
                    now,
                    now,
                )
            )
        conn.executemany(
            """
            INSERT INTO user_evidence_inventory (
                user_id, kind, title, content, tags_json, source, state, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            prepared,
        )
        conn.commit()
    finally:
        conn.close()
    return get_user_evidence_records(user_id, state=state)


def get_user_evidence_records(user_id: int, *, state: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        if state is None:
            rows = conn.execute(
                """
                SELECT id, kind, title, content, tags_json, source, state, created_at, updated_at
                FROM user_evidence_inventory
                WHERE user_id = ?
                ORDER BY updated_at DESC, id DESC
                """,
                (user_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, kind, title, content, tags_json, source, state, created_at, updated_at
                FROM user_evidence_inventory
                WHERE user_id = ? AND state = ?
                ORDER BY updated_at DESC, id DESC
                """,
                (user_id, state),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "kind": row["kind"],
                "title": row["title"],
                "content": row["content"],
                "tags": json.loads(row["tags_json"] or "[]"),
                "source": row["source"],
                "state": row["state"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]
    finally:
        conn.close()


def replace_user_target_archetypes(user_id: int, records: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    now = _utcnow()
    conn = get_db_connection()
    try:
        conn.execute("DELETE FROM user_target_archetypes WHERE user_id = ?", (user_id,))
        prepared = []
        seen: set[str] = set()
        for record in records:
            archetype = str(record.get("archetype") or "").strip()
            tier = str(record.get("tier") or "").strip().lower()
            if not archetype or tier not in {"primary", "secondary", "adjacent"} or archetype in seen:
                continue
            prepared.append((user_id, archetype, tier, now, now))
            seen.add(archetype)
        conn.executemany(
            """
            INSERT INTO user_target_archetypes (user_id, archetype, tier, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            prepared,
        )
        conn.commit()
    finally:
        conn.close()
    return get_user_target_archetypes(user_id)


def get_user_target_archetypes(user_id: int) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        rows = conn.execute(
            """
            SELECT id, archetype, tier, created_at, updated_at
            FROM user_target_archetypes
            WHERE user_id = ?
            ORDER BY
                CASE tier
                    WHEN 'primary' THEN 0
                    WHEN 'secondary' THEN 1
                    WHEN 'adjacent' THEN 2
                    ELSE 3
                END,
                archetype ASC
            """,
            (user_id,),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "archetype": row["archetype"],
                "tier": row["tier"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]
    finally:
        conn.close()


def migrate_legacy_skills_to_user(user_id: int, skills: List[str]) -> None:
    """Import legacy file-backed skills into SQLite if the user has no stored skills yet."""
    existing = get_user_skills(user_id)
    if existing or not skills:
        return
    replace_user_skills(user_id, skills, state="confirmed", source="legacy_memory")
    logger.info("Migrated legacy skills into SQLite user profile", user_id=user_id, skill_count=len(skills))
