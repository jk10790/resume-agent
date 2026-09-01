from unittest.mock import Mock

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from api.main import app
from resume_agent.services.fit_evaluation_service import (
    FitEvaluationError,
    load_resume_text,
    normalize_resume_doc_ids,
)
from resume_agent.utils.exceptions import GoogleAPIError


def test_normalize_resume_doc_ids_dedupes_and_keeps_priority_order():
    doc_ids = normalize_resume_doc_ids([None, "doc-a", "doc-a", "", "doc-b"])

    assert doc_ids == ["doc-a", "doc-b"]


def test_load_resume_text_requires_a_google_session():
    with pytest.raises(FitEvaluationError) as excinfo:
        load_resume_text(None, ["doc-a"])

    assert excinfo.value.code == "no_google_session"


def test_load_resume_text_requires_a_configured_resume():
    with pytest.raises(FitEvaluationError) as excinfo:
        load_resume_text(("drive", "docs"), [])

    assert excinfo.value.code == "no_resume_configured"


def test_load_resume_text_falls_through_to_the_next_readable_doc(monkeypatch):
    attempts = []

    def fake_read(_drive, _docs, doc_id):
        attempts.append(doc_id)
        if doc_id == "doc-a":
            raise GoogleAPIError("Document not found")
        return "Resume body"

    monkeypatch.setattr("resume_agent.services.fit_evaluation_service.read_resume_file", fake_read)

    assert load_resume_text(("drive", "docs"), ["doc-a", "doc-b"]) == "Resume body"
    assert attempts == ["doc-a", "doc-b"]


def test_load_resume_text_reports_an_inaccessible_document(monkeypatch):
    monkeypatch.setattr(
        "resume_agent.services.fit_evaluation_service.read_resume_file",
        Mock(side_effect=GoogleAPIError("Document not found")),
    )

    with pytest.raises(FitEvaluationError) as excinfo:
        load_resume_text(("drive", "docs"), ["doc-a"])

    assert excinfo.value.code == "resume_forbidden"


def test_evaluate_fit_endpoint_prefers_supplied_text_over_refetching_the_url(monkeypatch):
    """A caller that already holds the posting text must not trigger a second scrape."""
    client = TestClient(app)
    extract = Mock(return_value="scraped text")
    monkeypatch.setattr("resume_agent.agents.jd_extractor.extract_clean_jd", extract)
    monkeypatch.setattr("api.main.LLMService", Mock())
    monkeypatch.setattr("api.main.get_google_services_from_request", lambda _request: ("drive", "docs"))
    monkeypatch.setattr("api.main.get_preferred_resume_doc_id", lambda _request: "doc-a")
    monkeypatch.setattr("api.main.load_resume_text", lambda *_args, **_kwargs: "Resume body")
    captured = {}

    def fake_evaluate(**kwargs):
        captured.update(kwargs)
        return {
            "score": 7,
            "should_apply": True,
            "confidence": "medium",
            "matching_areas": [],
            "missing_areas": [],
            "recommendations": [],
        }

    monkeypatch.setattr("api.main.evaluate_fit_for_jd", fake_evaluate)

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
    monkeypatch.setattr("api.main.LLMService", Mock())
    monkeypatch.setattr("api.main.get_google_services_from_request", lambda _request: ("drive", "docs"))
    monkeypatch.setattr("api.main.get_preferred_resume_doc_id", lambda _request: "doc-a")
    monkeypatch.setattr("api.main.load_resume_text", lambda *_args, **_kwargs: "Resume body")
    monkeypatch.setattr("resume_agent.agents.jd_extractor.extract_clean_jd", Mock(return_value="scraped text"))
    captured = {}

    def fake_evaluate(**kwargs):
        captured.update(kwargs)
        return {
            "score": 4,
            "should_apply": False,
            "confidence": "low",
            "matching_areas": [],
            "missing_areas": [],
            "recommendations": [],
        }

    monkeypatch.setattr("api.main.evaluate_fit_for_jd", fake_evaluate)

    response = client.post("/api/evaluate-fit", json={"job_url": "https://boards.example.com/job/1"})

    assert response.status_code == 200
    assert captured["jd_text"] == "scraped text"


def test_evaluate_fit_endpoint_maps_a_missing_google_session_to_401(monkeypatch):
    client = TestClient(app)
    monkeypatch.setattr("api.main.LLMService", Mock())
    monkeypatch.setattr("api.main.get_google_services_from_request", lambda _request: None)
    monkeypatch.setattr("api.main.get_preferred_resume_doc_id", lambda _request: None)

    response = client.post("/api/evaluate-fit", json={"jd_text": "Some posting"})

    assert response.status_code == 401


def test_fit_evaluator_fallback_forwards_known_skills(monkeypatch):
    """The agent path failing must reach the LLM fallback, not a NameError that masks it."""
    from resume_agent.agents import fit_evaluator
    from resume_agent.services.llm_service import LLMService

    monkeypatch.setattr(
        "resume_agent.agents.resume_parser_agent.ResumeParserAgent",
        Mock(side_effect=RuntimeError("agent unavailable")),
    )
    llm_service = Mock(spec=LLMService)
    llm_service.evaluate_fit_structured.return_value = "fallback-evaluation"

    result = fit_evaluator.evaluate_resume_fit(
        llm_service,
        "Resume body",
        "JD body",
        known_skills=["python", "llm"],
    )

    assert result == "fallback-evaluation"
    assert llm_service.evaluate_fit_structured.call_args.kwargs["known_skills"] == ["python", "llm"]
