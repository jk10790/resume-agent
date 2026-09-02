"""The approval gate: approve, refine, and hand-edit a pending draft.

The gate exists so nothing reaches the user's Drive without them seeing it
first, so these pin that publishing happens only on approval, and that refining
reuses the evaluation already made rather than paying for it again.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from api.main import app, approval_storage
from resume_agent.pipelines import ApprovedDraft, TailorRequest
from resume_agent.pipelines.orchestrator import TailorOutcome


def _evaluation(score: int = 8) -> SimpleNamespace:
    return SimpleNamespace(
        score=score,
        should_apply=True,
        confidence=0.9,
        matching_areas=["Python"],
        missing_areas=[],
        recommendations=[],
        reasoning="",
    )


def _outcome(**overrides) -> TailorOutcome:
    """A real TailorOutcome: the endpoints copy it with dataclasses.replace."""
    base = dict(
        tailored_resume="TAILORED DRAFT",
        evaluation=_evaluation(),
        strategy_brief=None,
        strategy_brief_id=None,
        validation=None,
        review_bundle=None,
        ats_score=82,
        ats_score_object=None,
        original_resume_text="Jane Doe\n- Built payment services in Python",
        tailored_doc_id=None,
        doc_url=None,
        diff_path=None,
        application_id=None,
        usage={},
        steps=[],
    )
    base.update(overrides)
    return TailorOutcome(**base)


@pytest.fixture
def pending_draft(monkeypatch):
    monkeypatch.setattr("api.main.get_google_services_from_request", lambda _request: None)
    monkeypatch.setattr("api.main.get_session_data", lambda _request: None)

    approval_id = "approval-under-test"
    draft = ApprovedDraft(
        request=TailorRequest(jd_text="Python role", company="Acme", job_title="Engineer"),
        outcome=_outcome(),
        fit=SimpleNamespace(evaluation=_evaluation(), analyzed_jd=None, profile_context=None),
    )
    approval_storage.store(approval_id, draft)
    yield approval_id
    approval_storage.delete(approval_id)


def test_rejecting_a_draft_publishes_nothing(pending_draft, monkeypatch):
    orchestrator = Mock()
    monkeypatch.setattr("api.main.ResumeOrchestrator", lambda *a, **k: orchestrator)
    client = TestClient(app)

    response = client.post(
        "/api/approve-resume", json={"approval_id": pending_draft, "approved": False}
    )

    assert response.status_code == 200
    assert response.json()["approved"] is False
    orchestrator.publish.assert_not_called()
    assert approval_storage.get(pending_draft) is None


def test_approving_a_draft_publishes_it(pending_draft, monkeypatch):
    orchestrator = Mock()
    orchestrator.publish.return_value = _outcome(
        doc_url="https://docs.google.com/document/d/abc",
        diff_path="/tmp/diff.md",
        application_id=7,
    )
    monkeypatch.setattr("api.main.ResumeOrchestrator", lambda *a, **k: orchestrator)
    client = TestClient(app)

    response = client.post(
        "/api/approve-resume", json={"approval_id": pending_draft, "approved": True}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["approved"] is True
    assert body["result"]["doc_url"].endswith("/abc")
    assert body["result"]["application_id"] == 7

    published_text = orchestrator.publish.call_args[0][1]
    assert published_text == "TAILORED DRAFT"
    # The gate is single-use.
    assert approval_storage.get(pending_draft) is None


def test_an_unknown_approval_id_is_a_404(monkeypatch):
    monkeypatch.setattr("api.main.get_google_services_from_request", lambda _request: None)
    monkeypatch.setattr("api.main.get_session_data", lambda _request: None)
    client = TestClient(app)

    response = client.post("/api/approve-resume", json={"approval_id": "nope", "approved": True})

    assert response.status_code == 404


def test_an_expired_google_session_gets_an_actionable_message(pending_draft, monkeypatch):
    from resume_agent.llm.pipeline import PipelineError

    orchestrator = Mock()
    orchestrator.publish.side_effect = PipelineError("invalid_grant: token expired")
    monkeypatch.setattr("api.main.ResumeOrchestrator", lambda *a, **k: orchestrator)
    client = TestClient(app)

    response = client.post(
        "/api/approve-resume", json={"approval_id": pending_draft, "approved": True}
    )

    assert response.status_code == 502
    assert "sign in again with Google" in response.json()["detail"]


def test_refining_reuses_the_evaluation_already_made(pending_draft, monkeypatch):
    """Refining is one tailoring call, not a fresh parse-and-judge."""
    orchestrator = Mock()
    orchestrator.tailor.return_value = _outcome(tailored_resume="REFINED DRAFT")
    monkeypatch.setattr("api.main.ResumeOrchestrator", lambda *a, **k: orchestrator)
    client = TestClient(app)

    response = client.post(
        "/api/refine-resume",
        json={"approval_id": pending_draft, "feedback": "Lead with the platform work"},
    )

    assert response.status_code == 200
    assert response.json()["result"]["tailored_resume"] == "REFINED DRAFT"

    kwargs = orchestrator.tailor.call_args.kwargs
    assert kwargs["prior"] is not None, "the existing evaluation must be reused"
    assert kwargs["current_draft_text"] == "TAILORED DRAFT"
    assert orchestrator.tailor.call_args.args[0].refinement_feedback == "Lead with the platform work"

    # The refined draft replaces the pending one under the same approval id.
    assert approval_storage.get(pending_draft).outcome.tailored_resume == "REFINED DRAFT"


def test_a_hand_edited_draft_is_revalidated(pending_draft, monkeypatch):
    """A person can edit a number in too, so the rules must run on their text."""
    monkeypatch.setattr("resume_agent.storage.user_memory.get_verified_metrics", lambda: [])
    client = TestClient(app)

    response = client.post(
        "/api/update-approval-draft",
        json={
            "approval_id": pending_draft,
            "tailored_resume": "Jane Doe\n- Built payment services, cutting latency by 35%",
        },
    )

    assert response.status_code == 200
    validation = response.json()["result"]["validation"]
    assert validation["is_valid"] is False
    assert any("35%" in issue["message"] for issue in validation["issues"])


def test_an_empty_hand_edit_is_rejected(pending_draft):
    client = TestClient(app)

    response = client.post(
        "/api/update-approval-draft", json={"approval_id": pending_draft, "tailored_resume": "   "}
    )

    assert response.status_code == 400
