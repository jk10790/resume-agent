from __future__ import annotations

from pathlib import Path

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
