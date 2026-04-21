import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from api.main import app


def _mock_local_user(_request):
    return {"id": 1, "email": "tester@example.com"}


def test_application_patterns_endpoint(monkeypatch):
    client = TestClient(app)

    monkeypatch.setattr("api.routers.applications.ensure_local_user_session", lambda _request: {"id": 1})
    monkeypatch.setattr(
        "resume_agent.tracking.application_tracker.get_pattern_analysis",
        lambda user_id=None: {
            "total_applications": 4,
            "by_outcome": {"positive": 2, "negative": 1, "pending": 1},
            "archetype_breakdown": [{"archetype": "platform_infrastructure", "total": 2, "positive": 1, "negative": 0, "pending": 1, "self_filtered": 0, "conversion_rate": 50}],
            "target_alignment_breakdown": [{"target_alignment": "primary", "total": 2, "positive": 1, "negative": 0, "pending": 1, "self_filtered": 0, "conversion_rate": 50}],
            "blocker_reason_codes": [{"reason_code": "stack_mismatch", "frequency": 2, "percentage": 50}],
            "fit_floor_recommendation": 7,
            "recommendations": ["No positive outcomes landed below fit 7/10."],
        },
    )

    response = client.get("/api/applications/patterns")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_applications"] == 4
    assert payload["fit_floor_recommendation"] == 7
    assert payload["blocker_reason_codes"][0]["reason_code"] == "stack_mismatch"
