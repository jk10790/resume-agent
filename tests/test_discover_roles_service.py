from __future__ import annotations

from pathlib import Path

import pytest

from resume_agent.config import settings
from resume_agent.services.discover_roles_service import DiscoverRolesService, DiscoverSearchCriteria
from resume_agent.storage import user_store


FIXTURES = Path(__file__).parent / "fixtures" / "discovery"


class FakeATSProvider:
    name = "ats_api"

    def __init__(self, stubs, catalog_hash="catalog-v1"):
        self._stubs = stubs
        self.catalog_hash = catalog_hash

    def fetch_stubs(self, _criteria, max_results):
        return self._stubs[:max_results]


def test_ats_api_status_is_configured_when_catalog_is_valid(monkeypatch):
    monkeypatch.setattr(settings, "discover_enabled", True)
    monkeypatch.setattr(settings, "discover_provider", "ats_api")
    monkeypatch.setattr(settings, "discover_source_config_path", str(FIXTURES / "valid_catalog.yml"))

    service = DiscoverRolesService()
    status = service.get_status()

    assert status["enabled"] is True
    assert status["configured"] is True
    assert status["provider"] == "ats_api"


def test_query_cache_key_changes_when_catalog_hash_changes():
    normalized = DiscoverSearchCriteria(search_intent="backend", role_families=["software_engineering"]).normalized()

    service_a = DiscoverRolesService(provider=FakeATSProvider([], catalog_hash="hash-a"))
    service_b = DiscoverRolesService(provider=FakeATSProvider([], catalog_hash="hash-b"))

    assert service_a._query_cache_key(1, normalized) != service_b._query_cache_key(1, normalized)


def test_seniority_affects_ranking():
    service = DiscoverRolesService(provider=FakeATSProvider([]))
    normalized = DiscoverSearchCriteria(
        search_intent="backend",
        role_families=["software_engineering"],
        seniority="staff",
    ).normalized()
    junior_role = {
        "job_title": "Junior Backend Engineer",
        "matched_title_variant": "backend engineer",
        "archetype": "software_engineering",
        "remote_mode": "unknown",
        "location": "Remote",
        "raw_text": "Junior backend engineer role",
        "source_quality": "public_ats",
        "extraction_confidence": 0.9,
        "posted_at": "2026-04-20T12:00:00Z",
    }
    staff_role = {
        **junior_role,
        "job_title": "Staff Backend Engineer",
        "raw_text": "Staff backend engineer role",
    }

    junior_score, _, junior_blockers = service._rank_role(junior_role, normalized)
    staff_score, matched_filters, _ = service._rank_role(staff_role, normalized)

    assert staff_score > junior_score
    assert "staff" in matched_filters
    assert "junior seniority" in junior_blockers


def test_dismissed_roles_stay_dismissed_on_rediscovery(monkeypatch):
    monkeypatch.setattr(settings, "discover_enabled", True)
    monkeypatch.setattr(settings, "discover_max_fetches_per_search", 5)
    monkeypatch.setattr(settings, "discover_min_extracted_text_chars", 20)
    user = user_store.upsert_google_user(
        google_sub="discover-rediscovery-user",
        email="rediscovery@example.com",
        name="Rediscovery User",
        picture_url=None,
    )
    stub = {
        "title": "Senior Backend Engineer",
        "url": "https://example.com/jobs/1",
        "company": "Example GH",
        "location": "Remote - United States",
        "source_domain": "example.com",
        "source_quality": "public_ats",
        "posted_at": "2026-04-20T12:00:00Z",
        "employment_type": "Full-time",
        "apply_url": "https://example.com/jobs/1/apply",
        "description_text": "Build APIs and backend systems. " * 20,
        "remote_mode": "remote",
        "matched_filters": ["software_engineering"],
    }
    service = DiscoverRolesService(provider=FakeATSProvider([stub]))
    criteria = DiscoverSearchCriteria(search_intent="backend", role_families=["software_engineering"])

    first = service.search_roles(user["id"], criteria)
    role_id = first["roles"][0]["id"]
    user_store.update_discovered_role_inbox_state_for_user(user["id"], role_id, "dismissed")

    second = service.search_roles(
        user["id"],
        DiscoverSearchCriteria(search_intent="backend", role_families=["software_engineering"], refresh=True),
    )

    assert second["roles"][0]["id"] == role_id
    assert second["roles"][0]["inbox_state"] == "dismissed"


def _seed_role_for_fit(user_id: int, raw_text: str = "Build applied AI systems in Python."):
    return user_store.save_or_merge_discovered_role_for_user(
        user_id,
        {
            "canonical_url": "https://jobs.example.com/role/fit",
            "source_urls": ["https://jobs.example.com/role/fit"],
            "source_domain": "jobs.example.com",
            "company": "Acme",
            "job_title": "Applied AI Engineer",
            "location": "Remote (US)",
            "remote_mode": "remote",
            "employment_type": "full_time",
            "apply_url": "https://jobs.example.com/role/fit/apply",
            "posted_label": "2 days ago",
            "archetype": "applied_ai_llmops",
            "extraction_confidence": 0.91,
            "raw_text": raw_text,
            "short_tldr": "Build AI systems.",
            "matched_filters": ["remote"],
            "possible_blockers": [],
            "compensation": "$180k - $210k",
            "rank_score": 88,
        },
    )


def _fit_user(sub: str):
    return user_store.upsert_google_user(
        google_sub=sub,
        email=f"{sub}@example.com",
        name="Discover User",
        picture_url=None,
    )


def test_evaluate_role_fit_uses_stored_jd_and_persists_result(monkeypatch):
    user = _fit_user("discover-service-fit")
    role = _seed_role_for_fit(user["id"])
    service = DiscoverRolesService(provider=FakeATSProvider([]))

    calls = {}

    def fake_load_resume_text(google_services, doc_ids):
        calls["doc_ids"] = list(doc_ids)
        return "Resume text"

    def fake_evaluate(**kwargs):
        calls["kwargs"] = kwargs
        return {
            "score": 8,
            "should_apply": True,
            "confidence": "high",
            "matching_areas": ["python"],
            "missing_areas": ["kubernetes"],
            "recommendations": ["Lead with the LLM work"],
        }

    monkeypatch.setattr("resume_agent.services.fit_evaluation_service.load_resume_text", fake_load_resume_text)
    monkeypatch.setattr("resume_agent.services.fit_evaluation_service.evaluate_fit_for_jd", fake_evaluate)

    result = service.evaluate_role_fit(user["id"], role["id"], resume_doc_id="doc-123")

    # The stored posting text is what gets scored: no re-fetch of the apply URL.
    assert calls["kwargs"]["jd_text"] == "Build applied AI systems in Python."
    assert calls["kwargs"]["resume_text"] == "Resume text"
    assert calls["doc_ids"][0] == "doc-123"
    assert result["evaluation"]["score"] == 8
    assert result["role"]["fit_score"] == 8
    assert result["role"]["fit_should_apply"] is True

    stored = user_store.get_discovered_role_for_user(user["id"], role["id"])
    assert stored["fit_evaluation"]["recommendations"] == ["Lead with the LLM work"]

    feedback = user_store.list_discovered_role_feedback_for_user(user["id"], role["id"], limit=5)
    assert feedback[0]["decision"] == "fit_evaluated"

    analytics = service.get_analytics(user["id"])
    assert analytics["funnel"]["fit_evaluated_roles"] == 1
    # Scoring a role is not preference feedback, so it must not unlock suggestions.
    assert analytics["feedback_total"] == 0


def test_evaluate_role_fit_rejects_a_role_without_stored_text(monkeypatch):
    from resume_agent.services.fit_evaluation_service import FitEvaluationError

    user = _fit_user("discover-service-fit-empty")
    role = _seed_role_for_fit(user["id"], raw_text="")
    service = DiscoverRolesService(provider=FakeATSProvider([]))

    monkeypatch.setattr(
        "resume_agent.services.fit_evaluation_service.load_resume_text",
        lambda *_args, **_kwargs: "Resume text",
    )

    with pytest.raises(FitEvaluationError) as excinfo:
        service.evaluate_role_fit(user["id"], role["id"])
    assert excinfo.value.code == "no_jd_text"


def test_evaluate_role_fit_raises_key_error_for_unknown_role():
    user = _fit_user("discover-service-fit-missing")
    service = DiscoverRolesService(provider=FakeATSProvider([]))

    with pytest.raises(KeyError):
        service.evaluate_role_fit(user["id"], 987654)


def test_open_in_tailor_seed_carries_fit_and_role_context():
    user = _fit_user("discover-service-seed")
    role = _seed_role_for_fit(user["id"])
    user_store.save_discovered_role_fit_for_user(
        user["id"],
        role["id"],
        {"score": 7, "should_apply": True, "matching_areas": ["python"], "missing_areas": []},
    )
    service = DiscoverRolesService(provider=FakeATSProvider([]))

    seed = service.open_in_tailor(user["id"], role["id"])["discover_seed"]

    assert seed["discovered_role_id"] == role["id"]
    assert seed["jd_text"] == "Build applied AI systems in Python."
    assert seed["location"] == "Remote (US)"
    assert seed["remote_mode"] == "remote"
    assert seed["compensation"] == "$180k - $210k"
    assert seed["fit_evaluation"]["score"] == 7
    assert seed["fit_evaluation"]["should_apply"] is True


def test_open_in_tailor_seed_has_no_fit_before_evaluation():
    user = _fit_user("discover-service-seed-unscored")
    role = _seed_role_for_fit(user["id"])
    service = DiscoverRolesService(provider=FakeATSProvider([]))

    seed = service.open_in_tailor(user["id"], role["id"])["discover_seed"]

    assert seed["fit_evaluation"] is None
