# application_tracker.py
# SQLite-based application tracking system

import sqlite3
import os
from datetime import datetime
from typing import List, Dict, Optional
from pathlib import Path
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
            job_title TEXT NOT NULL,
            company TEXT NOT NULL,
            job_url TEXT,
            application_date TEXT NOT NULL,
            status TEXT DEFAULT 'applied',
            fit_score INTEGER,
            resume_doc_id TEXT,
            cover_letter_doc_id TEXT,
            notes TEXT,
            interview_date TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    
    conn.commit()
    return conn

def add_application(
    job_title: str,
    company: str,
    job_url: Optional[str] = None,
    fit_score: Optional[int] = None,
    resume_doc_id: Optional[str] = None,
    status: str = "applied",
    notes: Optional[str] = None
) -> int:
    """Add a new job application to the tracker."""
    conn = get_db_connection()
    now = datetime.now().isoformat()
    
    cursor = conn.execute("""
        INSERT INTO applications 
        (job_title, company, job_url, application_date, status, fit_score, resume_doc_id, notes, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (job_title, company, job_url, now, status, fit_score, resume_doc_id, notes, now, now))
    
    app_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return app_id

def update_application_status(app_id: int, status: str, notes: Optional[str] = None):
    """Update the status of an application."""
    conn = get_db_connection()
    now = datetime.now().isoformat()
    
    if notes:
        conn.execute("""
            UPDATE applications 
            SET status = ?, notes = ?, updated_at = ?
            WHERE id = ?
        """, (status, notes, now, app_id))
    else:
        conn.execute("""
            UPDATE applications 
            SET status = ?, updated_at = ?
            WHERE id = ?
        """, (status, now, app_id))
    
    conn.commit()
    conn.close()

def get_application(app_id: int) -> Optional[Dict]:
    """Get a specific application by ID."""
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
    conn.close()
    
    if row:
        return dict(row)
    return None

def list_applications(status: Optional[str] = None, limit: int = 50) -> List[Dict]:
    """List all applications, optionally filtered by status."""
    conn = get_db_connection()
    
    if status:
        rows = conn.execute(
            "SELECT * FROM applications WHERE status = ? ORDER BY created_at DESC LIMIT ?",
            (status, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM applications ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
    
    conn.close()
    return [dict(row) for row in rows]

def get_statistics() -> Dict:
    """Get application statistics."""
    conn = get_db_connection()
    
    total = conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
    by_status = {}
    
    for row in conn.execute("SELECT status, COUNT(*) as count FROM applications GROUP BY status"):
        by_status[row[0]] = row[1]
    
    avg_fit_score = conn.execute(
        "SELECT AVG(fit_score) FROM applications WHERE fit_score IS NOT NULL"
    ).fetchone()[0]
    
    conn.close()
    
    return {
        "total_applications": total,
        "by_status": by_status,
        "average_fit_score": round(avg_fit_score, 2) if avg_fit_score else None
    }

def search_applications(query: str) -> List[Dict]:
    """Search applications by job title or company."""
    conn = get_db_connection()
    search_term = f"%{query}%"
    
    rows = conn.execute("""
        SELECT * FROM applications 
        WHERE job_title LIKE ? OR company LIKE ?
        ORDER BY created_at DESC
    """, (search_term, search_term)).fetchall()
    
    conn.close()
    return [dict(row) for row in rows]
