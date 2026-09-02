"""Reading the resume, and the evaluate-fit endpoint that depends on it."""

from unittest.mock import Mock

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from api.main import app
from resume_agent.services.resume_source import (
    ResumeUnavailable,
    load_first_readable,
    normalize_doc_ids,
)
from resume_agent.utils.exceptions import GoogleAPIError


def test_normalize_doc_ids_dedupes_and_keeps_priority_order():
    assert normalize_doc_ids([None, "doc-a", "doc-a", "", "doc-b"]) == ["doc-a", "doc-b"]


def test_reading_a_resume_requires_a_google_session():
    with pytest.raises(ResumeUnavailable) as excinfo:
        load_first_readable(None, ["doc-a"])

    assert excinfo.value.code == "no_google_session"


def test_reading_a_resume_requires_a_configured_document():
    with pytest.raises(ResumeUnavailable) as excinfo:
        load_first_readable(("drive", "docs"), [])

    assert excinfo.value.code == "no_resume_configured"


def test_reading_falls_through_to_the_next_readable_document(monkeypatch):
    attempts = []

    def fake_read(_drive, _docs, doc_id):
        attempts.append(doc_id)
        if doc_id == "doc-a":
            raise GoogleAPIError("Document not found")
        return "Resume body"

    monkeypatch.setattr("resume_agent.services.resume_source.read_resume_file", fake_read)

    assert load_first_readable(("drive", "docs"), ["doc-a", "doc-b"]) == "Resume body"
    assert attempts == ["doc-a", "doc-b"]


def test_an_inaccessible_document_is_reported_as_forbidden(monkeypatch):
    monkeypatch.setattr(
        "resume_agent.services.resume_source.read_resume_file",
        Mock(side_effect=GoogleAPIError("Document not found")),
    )

    with pytest.raises(ResumeUnavailable) as excinfo:
        load_first_readable(("drive", "docs"), ["doc-a"])

    assert excinfo.value.code == "resume_forbidden"


def _evaluation(score: int = 7) -> Mock:
    return Mock(
        score=score,
        should_apply=score >= 6,
        confidence=0.8,
        matching_areas=[],
        missing_areas=[],
        recommendations=[],
        reasoning="",
    )


def _stub_orchestrator(monkeypatch, captured: dict) -> Mock:
    orchestrator = Mock()

    def evaluate(fit_request, **_kwargs):
        captured["jd_text"] = fit_request.jd_text
        captured["resume_text"] = fit_request.resume_text
        return Mock(evaluation=_evaluation(), usage={"cost_usd": 0.01}, steps=[])

    orchestrator.evaluate_fit.side_effect = evaluate
    monkeypatch.setattr("api.main.ResumeOrchestrator", lambda *args, **kwargs: orchestrator)
    return orchestrator


def _stub_request_context(monkeypatch):
    monkeypatch.setattr("api.main.get_google_services_from_request", lambda _request: ("drive", "docs"))
    monkeypatch.setattr("api.main.get_preferred_resume_doc_id", lambda _request: "doc-a")
    monkeypatch.setattr("api.main.load_first_readable", lambda *_a, **_k: "Resume body")


def test_evaluate_fit_endpoint_prefers_supplied_text_over_refetching_the_url(monkeypatch):
    """A caller that already holds the posting text must not trigger a second scrape."""
    client = TestClient(app)
    extract = Mock(return_value="scraped text")
    monkeypatch.setattr("resume_agent.agents.jd_extractor.extract_clean_jd", extract)
    _stub_request_context(monkeypatch)
    captured: dict = {}
    _stub_orchestrator(monkeypatch, captured)

    response = client.post(
        "/api/evaluate-fit",
        json={"job_url": "https://boards.example.com/job/1", "jd_text": "Stored posting text"},
    )

    assert response.status_code == 200
    assert response.json()["score"] == 7
    assert captured["jd_text"] == "Stored posting text"
    extract.assert_not_called()


def test_evaluate_fit_endpoint_still_scrapes_when_only_a_url_is_given(monkeypatch):
    client = TestClient(app)
    _stub_request_context(monkeypatch)
    monkeypatch.setattr(
        "resume_agent.agents.jd_extractor.extract_clean_jd", Mock(return_value="scraped text")
    )
    captured: dict = {}
    _stub_orchestrator(monkeypatch, captured)

    response = client.post("/api/evaluate-fit", json={"job_url": "https://boards.example.com/job/1"})

    assert response.status_code == 200
    assert captured["jd_text"] == "scraped text"


def test_evaluate_fit_endpoint_reports_what_the_run_cost(monkeypatch):
    """Cost was collected before but never left the process."""
    client = TestClient(app)
    _stub_request_context(monkeypatch)
    _stub_orchestrator(monkeypatch, {})

    response = client.post("/api/evaluate-fit", json={"jd_text": "Posting text"})

    assert response.status_code == 200
    assert response.json()["usage"] == {"cost_usd": 0.01}
