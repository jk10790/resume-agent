from unittest.mock import Mock

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from api.main import app
from resume_agent.services.resume_workflow import TailorResumeResult, WorkflowStep


def _mock_local_user(_request):
    return {"id": 1, "email": "tester@example.com"}


def test_discover_status_endpoint(monkeypatch):
    client = TestClient(app)
    monkeypatch.setattr(
        "api.routers.discover._service",
        lambda: Mock(get_status=Mock(return_value={"enabled": False, "configured": False, "provider": "none", "reason": "missing"})),
    )

    response = client.get("/api/discover/status")

    assert response.status_code == 200
    assert response.json()["configured"] is False


def test_discover_open_in_tailor_endpoint(monkeypatch):
    client = TestClient(app)
    monkeypatch.setattr("api.routers.discover.get_local_user", _mock_local_user)
    monkeypatch.setattr(
        "api.routers.discover._service",
        lambda: Mock(
            open_in_tailor=Mock(
                return_value={
                    "discover_seed": {
                        "discovered_role_id": 12,
                        "company": "Acme",
                        "job_title": "Applied AI Engineer",
                        "job_url": "https://example.com/job",
                        "jd_text": "Role details",
                    }
                }
            )
        ),
    )

    response = client.post("/api/discover/roles/12/open-in-tailor", json={})

    assert response.status_code == 200
    assert response.json()["discover_seed"]["discovered_role_id"] == 12


def test_discover_saved_searches_endpoint(monkeypatch):
    client = TestClient(app)
    monkeypatch.setattr("api.routers.discover.get_local_user", _mock_local_user)
    monkeypatch.setattr(
        "api.routers.discover._service",
        lambda: Mock(
            list_saved_searches=Mock(return_value=[{"id": 1, "name": "Remote AI", "criteria": {"search_intent": "ai"}}]),
            save_search=Mock(return_value={"id": 1, "name": "Remote AI", "criteria": {"search_intent": "ai"}}),
        ),
    )

    list_response = client.get("/api/discover/saved-searches")
    create_response = client.post(
        "/api/discover/saved-searches",
        json={"name": "Remote AI", "criteria": {"search_intent": "ai"}, "is_default": False},
    )

    assert list_response.status_code == 200
    assert list_response.json()["saved_searches"][0]["name"] == "Remote AI"
    assert create_response.status_code == 200
    assert create_response.json()["id"] == 1


def test_discover_analytics_endpoint(monkeypatch):
    client = TestClient(app)
    monkeypatch.setattr("api.routers.discover.get_local_user", _mock_local_user)
    monkeypatch.setattr(
        "api.routers.discover._service",
        lambda: Mock(
            get_analytics=Mock(
                return_value={
                    "feedback_total": 17,
                    "restore_rate_percent": 12.5,
                    "funnel": {"discovered_roles": 30, "shortlisted_roles": 8},
                }
            )
        ),
    )

    response = client.get("/api/discover/analytics")

    assert response.status_code == 200
    assert response.json()["feedback_total"] == 17


def test_tailor_resume_request_disables_application_tracking_for_discovery(monkeypatch):
    client = TestClient(app)
    monkeypatch.setattr("api.main.get_local_user_from_request", _mock_local_user)
    monkeypatch.setattr("api.main.get_google_services_from_request", lambda _request: None)

    captured = {}
    service = Mock()

    def execute(workflow_request, step, result, progress_callback=None):
        captured["track_application"] = workflow_request.track_application
        captured["discovered_role_id"] = workflow_request.discovered_role_id
        return TailorResumeResult(error="stop", current_step=WorkflowStep.ERROR)

    service.execute_workflow_step.side_effect = execute
    monkeypatch.setattr("api.main.MultiAgentWorkflowService", lambda *args, **kwargs: service)

    response = client.post(
        "/api/tailor-resume",
        json={
            "company": "Acme",
            "job_title": "Applied AI Engineer",
            "jd_text": "Role details",
            "track_application": True,
            "discovered_role_id": 99,
        },
    )

    assert response.status_code == 200
    assert captured == {"track_application": False, "discovered_role_id": 99}
