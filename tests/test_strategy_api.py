from unittest.mock import Mock

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from api.main import app
from resume_agent.models.agent_models import JobStrategyBrief, StrategyDirective
from resume_agent.services.resume_workflow import TailorResumeResult
from resume_agent.models.resume import FitEvaluation


def _mock_local_user(_request):
    return {"id": 1, "email": "tester@example.com"}


def _mock_no_session(_request):
    return {}


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

    final_result = TailorResumeResult(
        strategy_brief=strategy_brief,
        strategy_brief_id=42,
        evaluation=FitEvaluation(
            score=7,
            should_apply=True,
            matching_areas=["Python"],
            missing_areas=[],
            recommendations=[],
            confidence=0.8,
        ),
    )

    service = Mock()
    service.execute_workflow_step.side_effect = [TailorResumeResult(), TailorResumeResult(), TailorResumeResult(), final_result]
    monkeypatch.setattr("api.main.MultiAgentWorkflowService", lambda *args, **kwargs: service)

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

    service = Mock()
    service.execute_workflow_step.side_effect = [TailorResumeResult(parsed_resume=Mock(), analyzed_jd=Mock(), evaluation=FitEvaluation(score=6, should_apply=True, matching_areas=[], missing_areas=[], recommendations=[], confidence=0.7))] * 3
    service.strategy_brief_service.regenerate_section.return_value = regenerated
    service.strategy_brief_service.persist_brief.return_value = regenerated
    monkeypatch.setattr("api.main.MultiAgentWorkflowService", lambda *args, **kwargs: service)

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
    monkeypatch.setattr(
        "api.main.StrategyBriefService",
        lambda _llm: Mock(persist_brief=Mock(return_value=duplicated)),
    )

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

    service = Mock()
    step_result = TailorResumeResult(
        parsed_resume=Mock(),
        analyzed_jd=Mock(),
        evaluation=FitEvaluation(score=7, should_apply=True, matching_areas=[], missing_areas=[], recommendations=[], confidence=0.8),
    )
    service.execute_workflow_step.side_effect = [step_result, step_result, step_result]
    service.strategy_brief_service.build_brief.return_value = rebuilt
    service.strategy_brief_service.persist_brief.return_value = rebuilt
    monkeypatch.setattr("api.main.MultiAgentWorkflowService", lambda *args, **kwargs: service)

    response = client.post(
        "/api/job-strategy/3/rebaseline",
        json={"jd_text": "Updated JD"},
    )

    assert response.status_code == 200
    assert response.json()["strategy_brief"]["role_summary"] == "Rebuilt summary"
