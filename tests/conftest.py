from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def _test_db_path(tmp_path_factory: pytest.TempPathFactory) -> str:
    db_dir = tmp_path_factory.mktemp("db")
    return str(Path(db_dir) / "test_applications.db")


@pytest.fixture(autouse=True, scope="session")
def _patch_global_db_path(_test_db_path: str):
    # Patch the module-level DB_PATH used by get_db_connection() so tests don't
    # mutate the repo's real applications.db.
    from resume_agent.storage import user_store

    user_store.DB_PATH = _test_db_path

    # Some code paths read this env var directly; keep it consistent.
    os.environ.setdefault("APPLICATION_DB_PATH", _test_db_path)


@pytest.fixture(autouse=True)
def _clear_sqlite_state():
    # Clear persisted state between tests for deterministic results.
    from resume_agent.storage.user_store import get_db_connection

    conn = get_db_connection()
    try:
        for table in (
            "users",
            "user_skill_inventory",
            "user_quality_reports",
            "user_metric_inventory",
            "user_improved_resumes",
            "cache_entries",
        ):
            try:
                conn.execute(f"DELETE FROM {table}")
            except Exception:
                # Some tables (e.g. cache_entries) may not be created until first use.
                continue
        conn.commit()
    finally:
        conn.close()
