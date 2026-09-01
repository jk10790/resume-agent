from __future__ import annotations

import pytest

from resume_agent.agent import mcp_discovery


class _FakeRows(list):
    pass


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows
        self.queries: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        self.queries.append((sql, params))
        if "lower(email)" in sql:
            wanted = params[0]
            matches = [row for row in self._rows if row["email"].lower() == wanted]
            return _Cursor(matches)
        return _Cursor(self._rows)


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


def _patch_conn(monkeypatch, rows):
    conn = _FakeConn(rows)
    monkeypatch.setattr(mcp_discovery, "get_db_connection", lambda: conn)
    return conn


def test_single_local_user_is_resolved_without_configuration(monkeypatch):
    monkeypatch.delenv("RESUME_AGENT_USER_EMAIL", raising=False)
    _patch_conn(monkeypatch, [{"id": 8, "email": "a@example.com"}])

    assert mcp_discovery.resolve_user_id() == 8


def test_several_users_refuse_to_guess(monkeypatch):
    """Acting for the wrong user would silently write to someone else's inbox."""
    monkeypatch.delenv("RESUME_AGENT_USER_EMAIL", raising=False)
    _patch_conn(monkeypatch, [{"id": 1, "email": "a@example.com"}, {"id": 2, "email": "b@example.com"}])

    with pytest.raises(mcp_discovery.UserResolutionError) as excinfo:
        mcp_discovery.resolve_user_id()
    assert "RESUME_AGENT_USER_EMAIL" in str(excinfo.value)


def test_configured_email_selects_the_user(monkeypatch):
    monkeypatch.setenv("RESUME_AGENT_USER_EMAIL", "B@Example.com")
    _patch_conn(monkeypatch, [{"id": 1, "email": "a@example.com"}, {"id": 2, "email": "b@example.com"}])

    assert mcp_discovery.resolve_user_id() == 2


def test_unknown_email_is_an_error_not_a_fallback(monkeypatch):
    monkeypatch.setenv("RESUME_AGENT_USER_EMAIL", "nobody@example.com")
    _patch_conn(monkeypatch, [{"id": 1, "email": "a@example.com"}])

    with pytest.raises(mcp_discovery.UserResolutionError):
        mcp_discovery.resolve_user_id()


def test_no_users_reports_the_sign_in_step(monkeypatch):
    monkeypatch.delenv("RESUME_AGENT_USER_EMAIL", raising=False)
    _patch_conn(monkeypatch, [])

    with pytest.raises(mcp_discovery.UserResolutionError) as excinfo:
        mcp_discovery.resolve_user_id()
    assert "Sign in" in str(excinfo.value)


def test_listing_stays_compact_and_keeps_the_id_for_follow_up():
    """The whole point of the server is not spending tokens on the feed, so a
    listing is one line per role plus facts — never the job description."""
    rendered = mcp_discovery._render(
        [
            {
                "id": 423,
                "company": "Temporal",
                "job_title": "Staff Software Engineer",
                "remote_mode": "remote",
                "posted_label": "5 days ago",
                "compensation": "$169,600 - $278,250",
                "location": "United States",
                "raw_text": "x" * 5000,
                "possible_blockers": [],
            }
        ],
        "active roles",
    )

    assert "[423]" in rendered
    assert "$169,600 - $278,250" in rendered
    assert "xxxx" not in rendered
    assert len(rendered) < 300


def test_blockers_are_surfaced_in_the_listing():
    rendered = mcp_discovery._render(
        [{"id": 1, "company": "C", "job_title": "T", "possible_blockers": ["location not verified"]}],
        "active roles",
    )

    assert "blockers: location not verified" in rendered


def test_empty_listing_says_so():
    assert mcp_discovery._render([], "shortlisted roles") == "shortlisted roles: none."
