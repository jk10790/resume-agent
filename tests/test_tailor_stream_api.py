"""The streaming tailor endpoint, end to end over the real SSE contract.

The stream is what the web UI drives, so these assert the event shapes the
frontend reads: per-step progress with counts, an approval event carrying the
draft, and a cost figure on the way out.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from api.main import app


def _events(response) -> list[dict]:
    return [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def _evaluation(score: int = 8) -> SimpleNamespace:
    return SimpleNamespace(
        score=score,
        should_apply=score >= 6,
        confidence=0.9,
        matching_areas=["Python"],
        missing_areas=[],
        recommendations=[],
        reasoning="",
    )


def _fit_outcome() -> SimpleNamespace:
    return SimpleNamespace(
        evaluation=_evaluation(),
        parsed_resume=Mock(),
        analyzed_jd=Mock(),
        profile_context=None,
        resume_text="ORIGINAL",
        usage={"cost_usd": 0.02, "calls": 3},
        steps=[],
    )


def _tailor_outcome() -> SimpleNamespace:
    return SimpleNamespace(
        tailored_resume="TAILORED DRAFT",
        evaluation=_evaluation(),
        strategy_brief=None,
        strategy_brief_id=None,
        validation=None,
        review_bundle=None,
        ats_score=82,
        ats_score_object=None,
        original_resume_text="ORIGINAL",
        tailored_doc_id=None,
        doc_url=None,
        diff_path=None,
        application_id=None,
        usage={"cost_usd": 0.11, "calls": 4},
        steps=[{"name": "tailor_resume", "status": "completed", "usage": {"cost_usd": 0.09}}],
    )


@pytest.fixture
def orchestrator(monkeypatch):
    monkeypatch.setattr("api.main.get_google_services_from_request", lambda _request: None)
    monkeypatch.setattr("api.main.get_session_data", lambda _request: None)

    stub = Mock()
    stub.evaluate_fit.side_effect = lambda _request, **kwargs: _run_progress(kwargs, _fit_outcome())
    stub.tailor.side_effect = lambda _request, **kwargs: _run_progress(kwargs, _tailor_outcome())
    monkeypatch.setattr("api.main.ResumeOrchestrator", lambda *args, **kwargs: stub)
    return stub


def _run_progress(kwargs, outcome):
    """Drive the progress callback the way a real pipeline run would."""
    progress = kwargs.get("progress")
    if progress:
        for step in ("load_resume", "understand_inputs", "evaluate_fit"):
            progress(step)
    return outcome


def test_the_stream_reports_progress_with_step_counts(orchestrator):
    client = TestClient(app)

    response = client.post("/api/tailor-resume", json={"jd_text": "Python role"})

    assert response.status_code == 200
    starts = [e for e in _events(response) if e["type"] == "step_start"]
    assert starts, "expected per-step progress events"
    for event in starts:
        assert event["total_steps"] > 0
        assert 0 <= event["progress"] <= 1
        assert event["message"], "each step needs a human-readable label"


def test_a_draft_comes_back_awaiting_approval(orchestrator):
    """Nothing is saved to Drive until the user approves."""
    client = TestClient(app)

    response = client.post("/api/tailor-resume", json={"jd_text": "Python role"})

    approvals = [e for e in _events(response) if e["type"] == "approval_required"]
    assert len(approvals) == 1
    approval = approvals[0]
    assert approval["approval_id"]
    assert approval["result"]["tailored_resume"] == "TAILORED DRAFT"
    assert approval["result"]["approval_required"] is True
    assert approval["result"]["approval_stage"] == "final_resume"


def test_the_stream_reports_what_the_run_cost(orchestrator):
    client = TestClient(app)

    response = client.post("/api/tailor-resume", json={"jd_text": "Python role"})

    approval = next(e for e in _events(response) if e["type"] == "approval_required")
    assert approval["result"]["usage"]["cost_usd"] == pytest.approx(0.11)
    # Per-step attribution travels with it, so spend can be traced to a stage.
    assert approval["result"]["steps"][0]["name"] == "tailor_resume"


def test_evaluate_only_stops_after_the_judgement(orchestrator):
    """The cheap path must not tailor, and must not ask for approval."""
    client = TestClient(app)

    response = client.post(
        "/api/tailor-resume", json={"jd_text": "Python role", "evaluate_only": True}
    )

    events = _events(response)
    assert not [e for e in events if e["type"] == "approval_required"]
    complete = next(e for e in events if e["type"] == "complete")
    assert complete["result"]["fit_score"] == 8
    orchestrator.tailor.assert_not_called()


def test_a_low_fit_draft_carries_a_warning(orchestrator):
    orchestrator.tailor.side_effect = lambda _request, **kwargs: _run_progress(
        kwargs, SimpleNamespace(**{**vars(_tailor_outcome()), "evaluation": _evaluation(score=3)})
    )
    client = TestClient(app)

    response = client.post("/api/tailor-resume", json={"jd_text": "Python role"})

    approval = next(e for e in _events(response) if e["type"] == "approval_required")
    assert approval["fit_warning"]["score"] == 3
    assert "may not be a good match" in approval["fit_warning"]["message"]


def test_a_pipeline_failure_is_reported_as_an_error_event(orchestrator, monkeypatch):
    from resume_agent.llm.pipeline import PipelineError

    orchestrator.evaluate_fit.side_effect = PipelineError(
        "provider unreachable", step="evaluate_fit"
    )
    client = TestClient(app)

    response = client.post("/api/tailor-resume", json={"jd_text": "Python role"})

    errors = [e for e in _events(response) if e["type"] == "error"]
    assert errors
    assert errors[0]["step"] == "evaluate_fit"
    assert "provider unreachable" in errors[0]["error"]


def test_a_missing_resume_is_reported_with_its_code(orchestrator):
    from resume_agent.services.resume_source import ResumeUnavailable

    orchestrator.evaluate_fit.side_effect = ResumeUnavailable(
        "Google sign-in required.", code="no_google_session"
    )
    client = TestClient(app)

    response = client.post("/api/tailor-resume", json={"jd_text": "Python role"})

    errors = [e for e in _events(response) if e["type"] == "error"]
    assert errors
    assert errors[0]["code"] == "no_google_session"
