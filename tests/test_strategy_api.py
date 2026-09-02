from unittest.mock import Mock

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from api.main import app
from resume_agent.models.agent_models import JobStrategyBrief, StrategyDirective
from resume_agent.models.resume import FitEvaluation


def _mock_local_user(_request):
    return {"id": 1, "email": "tester@example.com"}


def _mock_no_session(_request):
    return {}



def _fit_outcome(score: int = 7) -> Mock:
    """A completed fit evaluation, as the orchestrator hands one back."""
    return Mock(
        evaluation=FitEvaluation(
            score=score,
            should_apply=True,
            matching_areas=["Python"],
            missing_areas=[],
            recommendations=[],
            confidence=0.8,
        ),
        parsed_resume=Mock(),
        analyzed_jd=Mock(),
        profile_context=None,
        resume_text="ORIGINAL",
        usage={},
        steps=[],
    )


def _stub_orchestrator(monkeypatch, **attrs) -> Mock:
    orchestrator = Mock()
    orchestrator.evaluate_fit.return_value = attrs.pop("fit", _fit_outcome())
    for key, value in attrs.items():
        setattr(orchestrator, key, value)
    monkeypatch.setattr("api.main.ResumeOrchestrator", lambda *args, **kwargs: orchestrator)
    return orchestrator


def test_job_strategy_evaluate_endpoint(monkeypatch):
    client = TestClient(app)

    monkeypatch.setattr("api.main.get_local_user_from_request", _mock_local_user)
    monkeypatch.setattr("api.main.get_google_services_from_request", lambda _request: None)
    monkeypatch.setattr("api.main.add_job_strategy_event_for_user", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "api.main._serialize_strategy_detail",
        lambda user_id, brief_id: {"strategy_brief": {"id": brief_id, "company": "Acme"}, "events": []},
    )

    strategy_brief = JobStrategyBrief(
        id=42,
        company="Acme",
        job_title="Platform Engineer",
        fit_score=7,
        should_apply=True,
        confidence=0.8,
        role_summary="Strong backend-platform fit.",
        tailoring_directives=[StrategyDirective(id="dir_1", section="summary", action="Lead with platform work")],
    )

    _stub_orchestrator(
        monkeypatch,
        build_strategy=Mock(
            return_value=Mock(strategy_brief=strategy_brief, strategy_brief_id=42, usage={}, steps=[])
        ),
    )

    response = client.post(
        "/api/job-strategy/evaluate",
        json={"company": "Acme", "job_title": "Platform Engineer", "jd_text": "Python platform role"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["strategy_brief"]["id"] == 42
    assert payload["evaluation"]["score"] == 7


def test_job_strategy_approve_endpoint(monkeypatch):
    client = TestClient(app)

    monkeypatch.setattr("api.main.get_local_user_from_request", _mock_local_user)
    monkeypatch.setattr("api.main.update_job_strategy_brief_status_for_user", lambda user_id, brief_id, status: {"id": brief_id, "approval_status": status})
    monkeypatch.setattr("api.main.add_job_strategy_event_for_user", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "api.main._serialize_strategy_detail",
        lambda user_id, brief_id: {"strategy_brief": {"id": brief_id, "approval_status": "approved"}, "events": []},
    )

    response = client.post("/api/job-strategy/9/approve", json={"reason": "Looks accurate"})

    assert response.status_code == 200
    assert response.json()["strategy_brief"]["approval_status"] == "approved"


def test_job_strategy_regenerate_section_endpoint(monkeypatch):
    client = TestClient(app)

    monkeypatch.setattr("api.main.get_local_user_from_request", _mock_local_user)
    monkeypatch.setattr("api.main.get_google_services_from_request", lambda _request: None)
    monkeypatch.setattr(
        "api.main.get_job_strategy_brief_for_user",
        lambda user_id, brief_id: {
            "id": brief_id,
            "company": "Acme",
            "job_title": "Platform Engineer",
            "fit_score": 6,
            "should_apply": True,
            "confidence": 0.7,
            "role_summary": "Old summary",
            "requirement_evidence": [],
            "gap_assessments": [],
            "positioning_strategy": [],
            "tailoring_directives": [],
            "interview_seeds": [],
            "risk_notes": [],
            "approval_status": "pending",
        },
    )
    monkeypatch.setattr("api.main.add_job_strategy_event_for_user", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "api.main._serialize_strategy_detail",
        lambda user_id, brief_id: {"strategy_brief": {"id": brief_id, "role_summary": "New summary"}, "events": []},
    )

    regenerated = JobStrategyBrief(
        id=3,
        company="Acme",
        job_title="Platform Engineer",
        fit_score=6,
        should_apply=True,
        confidence=0.7,
        role_summary="New summary",
        requirement_evidence=[],
        gap_assessments=[],
        positioning_strategy=[],
        tailoring_directives=[],
        interview_seeds=[],
        risk_notes=[],
        approval_status="pending",
    )

    orchestrator = _stub_orchestrator(monkeypatch)
    orchestrator.services.agent.return_value = Mock(
        regenerate_section=Mock(return_value=regenerated),
        persist_brief=Mock(return_value=regenerated),
    )

    response = client.post(
        "/api/job-strategy/3/regenerate-section",
        json={"jd_text": "Platform role", "section": "role_summary"},
    )

    assert response.status_code == 200
    assert response.json()["strategy_brief"]["role_summary"] == "New summary"


def test_job_strategy_duplicate_endpoint(monkeypatch):
    client = TestClient(app)

    monkeypatch.setattr("api.main.get_local_user_from_request", _mock_local_user)
    monkeypatch.setattr(
        "api.main.get_job_strategy_brief_for_user",
        lambda user_id, brief_id: {
            "id": brief_id,
            "company": "Acme",
            "job_title": "Platform Engineer",
            "jd_text": "JD text",
            "fit_score": 6,
            "should_apply": True,
            "confidence": 0.7,
            "role_summary": "Old summary",
            "requirement_evidence": [],
            "gap_assessments": [],
            "positioning_strategy": [],
            "tailoring_directives": [],
            "interview_seeds": [],
            "risk_notes": [],
            "approval_status": "approved",
        },
    )
    monkeypatch.setattr("api.main.add_job_strategy_event_for_user", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "api.main._serialize_strategy_detail",
        lambda user_id, brief_id: {"strategy_brief": {"id": brief_id, "approval_status": "pending"}, "events": []},
    )

    duplicated = JobStrategyBrief(
        id=12,
        company="Acme",
        job_title="Platform Engineer",
        jd_text="JD text",
        fit_score=6,
        should_apply=True,
        confidence=0.7,
        role_summary="Old summary",
        requirement_evidence=[],
        gap_assessments=[],
        positioning_strategy=[],
        tailoring_directives=[],
        interview_seeds=[],
        risk_notes=[],
        approval_status="pending",
    )
    orchestrator = _stub_orchestrator(monkeypatch)
    orchestrator.services.agent.return_value = Mock(persist_brief=Mock(return_value=duplicated))

    response = client.post("/api/job-strategy/3/duplicate")

    assert response.status_code == 200
    assert response.json()["strategy_brief"]["id"] == 12


def test_job_strategy_rebaseline_endpoint(monkeypatch):
    client = TestClient(app)

    monkeypatch.setattr("api.main.get_local_user_from_request", _mock_local_user)
    monkeypatch.setattr("api.main.get_google_services_from_request", lambda _request: None)
    monkeypatch.setattr(
        "api.main.get_job_strategy_brief_for_user",
        lambda user_id, brief_id: {
            "id": brief_id,
            "company": "Acme",
            "job_title": "Platform Engineer",
            "job_url": "https://example.com",
            "jd_text": "Original JD",
            "fit_score": 6,
            "should_apply": True,
            "confidence": 0.7,
            "role_summary": "Old summary",
            "requirement_evidence": [],
            "gap_assessments": [],
            "positioning_strategy": [],
            "tailoring_directives": [],
            "interview_seeds": [],
            "risk_notes": [],
            "approval_status": "approved",
        },
    )
    monkeypatch.setattr("api.main.add_job_strategy_event_for_user", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "api.main._serialize_strategy_detail",
        lambda user_id, brief_id: {"strategy_brief": {"id": brief_id, "role_summary": "Rebuilt summary"}, "events": []},
    )

    rebuilt = JobStrategyBrief(
        id=3,
        company="Acme",
        job_title="Platform Engineer",
        jd_text="Updated JD",
        fit_score=7,
        should_apply=True,
        confidence=0.8,
        role_summary="Rebuilt summary",
        requirement_evidence=[],
        gap_assessments=[],
        positioning_strategy=[],
        tailoring_directives=[],
        interview_seeds=[],
        risk_notes=[],
        approval_status="pending",
    )

    orchestrator = _stub_orchestrator(monkeypatch)
    orchestrator.services.agent.return_value = Mock(
        build_brief=Mock(return_value=rebuilt),
        persist_brief=Mock(return_value=rebuilt),
    )

    response = client.post(
        "/api/job-strategy/3/rebaseline",
        json={"jd_text": "Updated JD"},
    )

    assert response.status_code == 200
    assert response.json()["strategy_brief"]["role_summary"] == "Rebuilt summary"
