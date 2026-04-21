# application_tracker.py
# SQLite-based application tracking system

import json
import sqlite3
from datetime import datetime
from typing import List, Dict, Optional
from ..config import settings

# Use resolved path from settings
DB_PATH = settings.resolved_application_db_path

def get_db_connection():
    """Get database connection, creating tables if needed."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    # Create tables if they don't exist
    conn.execute("""
        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            job_title TEXT NOT NULL,
            company TEXT NOT NULL,
            job_url TEXT,
            application_date TEXT NOT NULL,
            status TEXT DEFAULT 'applied',
            fit_score INTEGER,
            strategy_brief_id INTEGER,
            resume_doc_id TEXT,
            cover_letter_doc_id TEXT,
            notes TEXT,
            interview_date TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(applications)").fetchall()}
    if "user_id" not in columns:
        conn.execute("ALTER TABLE applications ADD COLUMN user_id INTEGER")
    if "strategy_brief_id" not in columns:
        conn.execute("ALTER TABLE applications ADD COLUMN strategy_brief_id INTEGER")

    conn.commit()
    return conn

def _normalize_for_dedup(s: str) -> str:
    """Normalize company/job title for duplicate detection."""
    return (s or "").strip().lower()


def _user_visibility_clause(user_id: Optional[int]) -> tuple[str, List[object]]:
    if user_id is None:
        return "", []
    return (
        """
        AND (
            applications.user_id = ?
            OR (
                applications.user_id IS NULL
                AND applications.strategy_brief_id IS NOT NULL
                AND EXISTS (
                    SELECT 1
                    FROM job_strategy_briefs
                    WHERE job_strategy_briefs.id = applications.strategy_brief_id
                      AND job_strategy_briefs.user_id = ?
                )
            )
        )
        """,
        [user_id, user_id],
    )


def _serialize_strategy_snapshot(
    strategy_row: Optional[sqlite3.Row],
    latest_event_row: Optional[sqlite3.Row],
) -> Optional[Dict]:
    if not strategy_row:
        return None
    payload = json.loads(strategy_row["strategy_json"] or "{}")
    requirement_evidence = payload.get("requirement_evidence") or []
    gap_assessments = payload.get("gap_assessments") or []
    evidence_sections = sorted(
        {
            item.get("source_section")
            for item in requirement_evidence
            if isinstance(item, dict) and item.get("source_section")
        }
    )
    blocker_reason_codes = sorted(
        {
            item.get("reason_code")
            for item in gap_assessments
            if isinstance(item, dict) and item.get("reason_code")
        }
    )
    return {
        "id": strategy_row["id"],
        "approval_status": payload.get("approval_status") or strategy_row["status"],
        "fit_score": payload.get("fit_score"),
        "archetype": payload.get("archetype") or "general",
        "target_alignment": payload.get("target_alignment") or "unranked",
        "role_summary": payload.get("role_summary") or "",
        "gating_decision": payload.get("gating_decision") or "proceed",
        "should_apply": payload.get("should_apply"),
        "updated_at": strategy_row["updated_at"],
        "last_event_type": latest_event_row["event_type"] if latest_event_row else None,
        "last_event_at": latest_event_row["created_at"] if latest_event_row else None,
        "provenance": {
            "evidence_sections": evidence_sections,
            "blocker_reason_codes": blocker_reason_codes,
            "sample_evidence": [
                {
                    "requirement": item.get("requirement"),
                    "status": item.get("status"),
                    "evidence": item.get("evidence"),
                    "source_section": item.get("source_section"),
                }
                for item in requirement_evidence[:3]
                if isinstance(item, dict)
            ],
            "gap_requirement_count": sum(
                1 for item in requirement_evidence if isinstance(item, dict) and item.get("status") == "gap"
            ),
        },
    }


def _classify_outcome(status: str) -> str:
    normalized = (status or "").strip().lower()
    if normalized in {"interview", "offer", "applied", "responded"}:
        return "positive"
    if normalized in {"rejected", "withdrawn", "discarded"}:
        return "negative"
    if normalized in {"skip"}:
        return "self_filtered"
    return "pending"


def _enrich_application_row(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    user_id: Optional[int] = None,
) -> Dict:
    application = dict(row)
    strategy_brief_id = application.get("strategy_brief_id")
    application["strategy_brief"] = None
    if not strategy_brief_id:
        return application

    params: List[object] = [strategy_brief_id]
    query = """
        SELECT id, status, strategy_json, updated_at
        FROM job_strategy_briefs
        WHERE id = ?
    """
    if user_id is not None:
        query += " AND user_id = ?"
        params.append(user_id)
    strategy_row = conn.execute(query, params).fetchone()
    if not strategy_row:
        return application

    latest_event_params: List[object] = [strategy_brief_id]
    latest_event_query = """
        SELECT event_type, created_at
        FROM job_strategy_events
        WHERE strategy_brief_id = ?
    """
    if user_id is not None:
        latest_event_query += " AND user_id = ?"
        latest_event_params.append(user_id)
    latest_event_query += " ORDER BY id DESC LIMIT 1"
    latest_event_row = conn.execute(latest_event_query, latest_event_params).fetchone()

    application["strategy_brief"] = _serialize_strategy_snapshot(strategy_row, latest_event_row)
    return application


def find_application_by_company_and_title(
    company: str,
    job_title: str,
    *,
    user_id: Optional[int] = None,
) -> Optional[Dict]:
    """Find an existing application with the same company and job title (case-insensitive). Used to avoid duplicates."""
    conn = get_db_connection()
    c_norm = _normalize_for_dedup(company)
    t_norm = _normalize_for_dedup(job_title)
    visibility_clause, visibility_params = _user_visibility_clause(user_id)
    row = conn.execute(
        f"""
        SELECT * FROM applications
        WHERE LOWER(TRIM(company)) = ? AND LOWER(TRIM(job_title)) = ?
        {visibility_clause}
        ORDER BY updated_at DESC LIMIT 1
        """,
        [c_norm, t_norm, *visibility_params],
    ).fetchone()
    try:
        return _enrich_application_row(conn, row, user_id=user_id) if row else None
    finally:
        conn.close()


def update_application_record(
    app_id: int,
    *,
    job_url: Optional[str] = None,
    fit_score: Optional[int] = None,
    strategy_brief_id: Optional[int] = None,
    resume_doc_id: Optional[str] = None,
    notes: Optional[str] = None,
) -> None:
    """Update an existing application's details (e.g. when re-saving for same role/company)."""
    conn = get_db_connection()
    now = datetime.now().isoformat()
    updates = ["application_date = ?", "updated_at = ?"]
    values = [now, now]
    if job_url is not None:
        updates.append("job_url = ?")
        values.append(job_url)
    if fit_score is not None:
        updates.append("fit_score = ?")
        values.append(fit_score)
    if strategy_brief_id is not None:
        updates.append("strategy_brief_id = ?")
        values.append(strategy_brief_id)
    if resume_doc_id is not None:
        updates.append("resume_doc_id = ?")
        values.append(resume_doc_id)
    if notes is not None:
        updates.append("notes = ?")
        values.append(notes)
    values.append(app_id)
    conn.execute(
        f"UPDATE applications SET {', '.join(updates)} WHERE id = ?",
        values,
    )
    conn.commit()
    conn.close()


def add_application(
    job_title: str,
    company: str,
    user_id: Optional[int] = None,
    job_url: Optional[str] = None,
    fit_score: Optional[int] = None,
    strategy_brief_id: Optional[int] = None,
    resume_doc_id: Optional[str] = None,
    status: str = "applied",
    notes: Optional[str] = None
) -> int:
    """Add a new job application to the tracker."""
    conn = get_db_connection()
    now = datetime.now().isoformat()
    
    cursor = conn.execute("""
        INSERT INTO applications 
        (user_id, job_title, company, job_url, application_date, status, fit_score, strategy_brief_id, resume_doc_id, notes, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, job_title, company, job_url, now, status, fit_score, strategy_brief_id, resume_doc_id, notes, now, now))
    
    app_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return app_id


def add_or_update_application(
    job_title: str,
    company: str,
    user_id: Optional[int] = None,
    job_url: Optional[str] = None,
    fit_score: Optional[int] = None,
    strategy_brief_id: Optional[int] = None,
    resume_doc_id: Optional[str] = None,
    status: str = "applied",
    notes: Optional[str] = None,
) -> int:
    """
    Track application: if one already exists for this company + job title, update it
    (e.g. new resume doc, new fit score) and return its id; otherwise add a new record.
    Does not block evaluate or tailor; use when saving/approving to avoid duplicate entries.
    """
    existing = find_application_by_company_and_title(company, job_title, user_id=user_id)
    if existing:
        app_id = existing["id"]
        update_application_record(
            app_id,
            job_url=job_url,
            fit_score=fit_score,
            strategy_brief_id=strategy_brief_id,
            resume_doc_id=resume_doc_id,
        )
        return app_id
    return add_application(
        job_title=job_title,
        company=company,
        user_id=user_id,
        job_url=job_url,
        fit_score=fit_score,
        strategy_brief_id=strategy_brief_id,
        resume_doc_id=resume_doc_id,
        status=status,
        notes=notes,
    )

def update_application_status(app_id: int, status: str, notes: Optional[str] = None, *, user_id: Optional[int] = None):
    """Update the status of an application."""
    conn = get_db_connection()
    now = datetime.now().isoformat()
    visibility_clause, visibility_params = _user_visibility_clause(user_id)
    
    if notes:
        conn.execute("""
            UPDATE applications 
            SET status = ?, notes = ?, updated_at = ?
            WHERE id = ? {}
        """.format(visibility_clause), (status, notes, now, app_id, *visibility_params))
    else:
        conn.execute("""
            UPDATE applications 
            SET status = ?, updated_at = ?
            WHERE id = ? {}
        """.format(visibility_clause), (status, now, app_id, *visibility_params))
    
    conn.commit()
    conn.close()

def get_application(app_id: int, *, user_id: Optional[int] = None) -> Optional[Dict]:
    """Get a specific application by ID."""
    conn = get_db_connection()
    visibility_clause, visibility_params = _user_visibility_clause(user_id)
    row = conn.execute(
        f"SELECT * FROM applications WHERE id = ? {visibility_clause}",
        [app_id, *visibility_params],
    ).fetchone()
    try:
        return _enrich_application_row(conn, row, user_id=user_id) if row else None
    finally:
        conn.close()

def list_applications(
    status: Optional[str] = None,
    limit: int = 50,
    *,
    user_id: Optional[int] = None,
) -> List[Dict]:
    """List all applications, optionally filtered by status."""
    conn = get_db_connection()
    visibility_clause, visibility_params = _user_visibility_clause(user_id)
    if status:
        rows = conn.execute(
            f"SELECT * FROM applications WHERE status = ? {visibility_clause} ORDER BY created_at DESC LIMIT ?",
            [status, *visibility_params, limit]
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT * FROM applications WHERE 1 = 1 {visibility_clause} ORDER BY created_at DESC LIMIT ?",
            [*visibility_params, limit]
        ).fetchall()
    try:
        return [_enrich_application_row(conn, row, user_id=user_id) for row in rows]
    finally:
        conn.close()

def get_statistics(*, user_id: Optional[int] = None) -> Dict:
    """Get application statistics."""
    conn = get_db_connection()
    visibility_clause, visibility_params = _user_visibility_clause(user_id)

    total = conn.execute(
        f"SELECT COUNT(*) FROM applications WHERE 1 = 1 {visibility_clause}",
        visibility_params,
    ).fetchone()[0]
    by_status = {}

    for row in conn.execute(
        f"SELECT status, COUNT(*) as count FROM applications WHERE 1 = 1 {visibility_clause} GROUP BY status",
        visibility_params,
    ):
        by_status[row[0]] = row[1]

    avg_fit_score = conn.execute(
        f"SELECT AVG(fit_score) FROM applications WHERE fit_score IS NOT NULL {visibility_clause}",
        visibility_params,
    ).fetchone()[0]

    conn.close()

    active = sum(
        count for status_name, count in by_status.items()
        if status_name not in {"rejected", "withdrawn"}
    )
    return {
        "total_applications": total,
        "by_status": by_status,
        "average_fit_score": round(avg_fit_score, 2) if avg_fit_score else None,
        "total": total,
        "active": active,
        "avg_fit_score": round(avg_fit_score, 2) if avg_fit_score else None,
        "interview": by_status.get("interview", 0),
    }

def search_applications(query: str, *, user_id: Optional[int] = None) -> List[Dict]:
    """Search applications by job title or company."""
    conn = get_db_connection()
    search_term = f"%{query}%"
    visibility_clause, visibility_params = _user_visibility_clause(user_id)

    rows = conn.execute(f"""
        SELECT * FROM applications 
        WHERE (job_title LIKE ? OR company LIKE ?)
        {visibility_clause}
        ORDER BY created_at DESC
    """, [search_term, search_term, *visibility_params]).fetchall()
    try:
        return [_enrich_application_row(conn, row, user_id=user_id) for row in rows]
    finally:
        conn.close()


def get_pattern_analysis(*, user_id: Optional[int] = None, limit: int = 500) -> Dict:
    applications = list_applications(limit=limit, user_id=user_id)
    if not applications:
        return {
            "total_applications": 0,
            "by_outcome": {},
            "archetype_breakdown": [],
            "target_alignment_breakdown": [],
            "blocker_reason_codes": [],
            "fit_floor_recommendation": None,
            "recommendations": [],
        }

    outcome_counts: Dict[str, int] = {}
    archetype_map: Dict[str, Dict[str, int]] = {}
    target_alignment_map: Dict[str, Dict[str, int]] = {}
    blocker_counts: Dict[str, int] = {}
    positive_fit_scores: List[int] = []
    all_fit_scores: List[int] = []

    for app in applications:
        outcome = _classify_outcome(app.get("status", ""))
        outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
        fit_score = app.get("fit_score")
        if isinstance(fit_score, int):
            all_fit_scores.append(fit_score)
            if outcome == "positive":
                positive_fit_scores.append(fit_score)

        strategy = app.get("strategy_brief") or {}
        archetype = strategy.get("archetype") or "general"
        archetype_entry = archetype_map.setdefault(archetype, {"total": 0, "positive": 0, "negative": 0, "pending": 0, "self_filtered": 0})
        archetype_entry["total"] += 1
        archetype_entry[outcome] = archetype_entry.get(outcome, 0) + 1

        target_alignment = strategy.get("target_alignment") or "unranked"
        target_entry = target_alignment_map.setdefault(target_alignment, {"total": 0, "positive": 0, "negative": 0, "pending": 0, "self_filtered": 0})
        target_entry["total"] += 1
        target_entry[outcome] = target_entry.get(outcome, 0) + 1

        for code in (strategy.get("provenance") or {}).get("blocker_reason_codes", []):
            blocker_counts[code] = blocker_counts.get(code, 0) + 1

    archetype_breakdown = [
        {
            "archetype": archetype,
            **data,
            "conversion_rate": round((data["positive"] / data["total"]) * 100) if data["total"] else 0,
        }
        for archetype, data in sorted(archetype_map.items(), key=lambda item: (-item[1]["total"], item[0]))
    ]
    target_alignment_breakdown = [
        {
            "target_alignment": alignment,
            **data,
            "conversion_rate": round((data["positive"] / data["total"]) * 100) if data["total"] else 0,
        }
        for alignment, data in sorted(target_alignment_map.items(), key=lambda item: (-item[1]["total"], item[0]))
    ]
    blocker_reason_codes = [
        {"reason_code": code, "frequency": count, "percentage": round((count / len(applications)) * 100)}
        for code, count in sorted(blocker_counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    fit_floor = min(positive_fit_scores) if positive_fit_scores else None
    recommendations: List[str] = []
    if fit_floor is not None:
        recommendations.append(f"No positive outcomes landed below fit {fit_floor}/10. Treat that as the current floor.")
    if blocker_reason_codes:
        top_blocker = blocker_reason_codes[0]
        recommendations.append(
            f"Most common blocker is {top_blocker['reason_code'].replace('_', ' ')} ({top_blocker['frequency']} applications)."
        )
    if archetype_breakdown:
        best_archetype = max(archetype_breakdown, key=lambda item: (item["conversion_rate"], item["total"]))
        recommendations.append(
            f"Best current archetype signal: {best_archetype['archetype']} at {best_archetype['conversion_rate']}% conversion."
        )

    average_fit = round(sum(all_fit_scores) / len(all_fit_scores), 2) if all_fit_scores else None
    return {
        "total_applications": len(applications),
        "by_outcome": outcome_counts,
        "average_fit_score": average_fit,
        "archetype_breakdown": archetype_breakdown,
        "target_alignment_breakdown": target_alignment_breakdown,
        "blocker_reason_codes": blocker_reason_codes,
        "fit_floor_recommendation": fit_floor,
        "recommendations": recommendations[:5],
    }
