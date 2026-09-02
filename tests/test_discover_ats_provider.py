from __future__ import annotations

import json
from pathlib import Path

from resume_agent.services.discovery.ats_provider import (
    dedupe_survivors,
    _apply_workday_detail,
    fetch_smartrecruiters_jobs,
    fetch_workday_jobs,
    passes_location_gate,
    prefilter_stub,
    ATSAPIProvider,
    fetch_ashby_jobs,
    fetch_greenhouse_jobs,
    fetch_lever_jobs,
)
from resume_agent.services.discovery.source_catalog import (
    BodyGuardrails,
    TitleGuardrails,
    TrackedCompany,
    load_source_catalog,
)


FIXTURES = Path(__file__).parent / "fixtures" / "discovery"


def _company(name: str, provider: str, careers_url: str, api_url: str) -> TrackedCompany:
    return TrackedCompany(
        name=name,
        enabled=True,
        provider=provider,
        careers_url=careers_url,
        api_url=api_url,
        tags=[],
        sponsorship_policy="unknown",
    )


def test_greenhouse_parser_returns_normalized_stubs():
    payload = json.loads((FIXTURES / "greenhouse_jobs.json").read_text())
    company = _company("Example GH", "greenhouse", "https://job-boards.greenhouse.io/example", "https://boards-api.greenhouse.io/v1/boards/example/jobs")

    jobs = fetch_greenhouse_jobs(payload, company)

    assert len(jobs) == 1
    assert jobs[0]["title"] == "Senior Backend Engineer"
    assert jobs[0]["source_quality"] == "public_ats"
    assert jobs[0]["source_domain"] == "job-boards.greenhouse.io"


def test_ashby_parser_returns_normalized_stubs():
    payload = json.loads((FIXTURES / "ashby_jobs.json").read_text())
    company = _company("Example Ashby", "ashby", "https://jobs.ashbyhq.com/example", "https://api.ashbyhq.com/posting-api/job-board/example?includeCompensation=true")

    jobs = fetch_ashby_jobs(payload, company)

    assert len(jobs) == 1
    assert jobs[0]["employment_type"] == "Full-time"
    assert jobs[0]["location"] == "Remote (US)"
    assert "platform systems" in jobs[0]["description_text"].lower()


def test_lever_parser_returns_normalized_stubs():
    payload = json.loads((FIXTURES / "lever_jobs.json").read_text())
    company = _company("Example Lever", "lever", "https://jobs.lever.co/example", "https://api.lever.co/v0/postings/example?mode=json")

    jobs = fetch_lever_jobs(payload, company)

    assert len(jobs) == 1
    assert jobs[0]["apply_url"].endswith("/xyz")
    assert jobs[0]["employment_type"] == "Full-time"
    assert jobs[0]["source_domain"] == "jobs.lever.co"


def test_source_catalog_loader_rejects_bad_config_cleanly():
    invalid_path = FIXTURES / "invalid_catalog.yml"

    try:
        load_source_catalog(str(invalid_path))
    except RuntimeError as exc:
        assert "must define an integer version" in str(exc)
    else:
        raise AssertionError("Expected invalid catalog to raise")


def test_ats_prefilter_reduces_candidates_before_detail_fetch(monkeypatch):
    catalog = load_source_catalog(str(FIXTURES / "valid_catalog.yml"))
    provider = ATSAPIProvider(catalog)
    long_text = "Distributed systems and APIs. " * 30
    stubs = [
        {
            "title": "Senior Backend Engineer",
            "url": "https://example.com/1",
            "company": "Example GH",
            "location": "Remote - United States",
            "source_domain": "example.com",
            "source_quality": "public_ats",
            "posted_at": "2026-04-20T12:00:00Z",
            "employment_type": "Full-time",
            "apply_url": "https://example.com/1/apply",
            "description_text": long_text,
        },
        {
            "title": "Recruiter",
            "url": "https://example.com/2",
            "company": "Example GH",
            "location": "Remote - United States",
            "source_domain": "example.com",
            "source_quality": "public_ats",
            "posted_at": "2026-04-20T12:00:00Z",
            "employment_type": "Full-time",
            "apply_url": "https://example.com/2/apply",
            "description_text": long_text,
        },
    ]
    monkeypatch.setattr(provider, "_fetch_company_jobs", lambda _company: stubs)

    results = provider.fetch_stubs(
        {
            "search_intent": "backend engineer remote",
            "role_families": ["software_engineering"],
            "seniority": "senior",
            "remote_modes": ["remote"],
            "include_locations": [],
            "exclude_locations": [],
            "must_have_keywords": [],
            "avoid_keywords": [],
        },
        max_results=1,
    )

    assert len(results) == 1
    assert results[0]["title"] == "Senior Backend Engineer"


def _stub(**overrides):
    base = {
        "title": "Staff Site Reliability Engineer",
        "url": "https://example.com/1",
        "company": "Example GH",
        "location": "Remote (US)",
        "locations": ["Remote (US)"],
        "source_domain": "example.com",
        "source_quality": "public_ats",
        "posted_at": "2026-04-20T12:00:00Z",
        "employment_type": "Full-time",
        "apply_url": "https://example.com/1/apply",
        "description_text": "Distributed systems work. " * 30,
    }
    base.update(overrides)
    return base


def _criteria(**overrides):
    base = {
        "search_intent": "",
        "role_families": ["platform_infrastructure"],
        "seniority": "staff",
        "remote_modes": ["remote"],
        "include_locations": [],
        "exclude_locations": [],
        "must_have_keywords": [],
        "avoid_keywords": [],
    }
    base.update(overrides)
    return base


def test_role_family_expansion_admits_titles_missing_from_positive_list():
    """"Site Reliability Engineer" matches no configured positive term, so it
    only survives if the requested role family widens the whitelist."""
    guardrails = TitleGuardrails(positive=["software engineer"], negative=[], seniority_boost=[])

    assert prefilter_stub(_stub(), _criteria(), guardrails) is not None
    assert prefilter_stub(_stub(), _criteria(role_families=["product_technical_product"]), guardrails) is None


def test_first_published_wins_over_bulk_refreshed_updated_at():
    company = _company("Example GH", "greenhouse", "https://job-boards.greenhouse.io/example", "https://boards-api.greenhouse.io/v1/boards/example/jobs")

    jobs = fetch_greenhouse_jobs(
        {"jobs": [{"title": "Staff Engineer", "absolute_url": "https://example.com/1", "first_published": "2025-01-07T00:00:00Z", "updated_at": "2026-08-27T00:00:00Z"}]},
        company,
    )

    assert jobs[0]["posted_at"] == "2025-01-07T00:00:00Z"


def test_lever_epoch_millis_timestamps_become_iso_dates():
    company = _company("Example Lever", "lever", "https://jobs.lever.co/example", "https://api.lever.co/v0/postings/example?mode=json")

    jobs = fetch_lever_jobs([{"text": "Staff Engineer", "hostedUrl": "https://jobs.lever.co/example/xyz", "createdAt": 1711403416463}], company)

    assert jobs[0]["posted_at"].startswith("2024-03-")


def test_secondary_locations_are_kept_so_multi_site_roles_survive():
    company = _company("Example Ashby", "ashby", "https://jobs.ashbyhq.com/example", "https://api.ashbyhq.com/posting-api/job-board/example")

    jobs = fetch_ashby_jobs(
        {"jobs": [{"title": "Staff Engineer", "jobUrl": "https://jobs.ashbyhq.com/example/1", "location": "London", "secondaryLocations": [{"location": "Remote (US)"}]}]},
        company,
    )

    assert jobs[0]["locations"] == ["London", "Remote (US)"]


def test_excluded_location_does_not_discard_a_role_open_elsewhere():
    guardrails = TitleGuardrails(positive=[], negative=[], seniority_boost=[])
    stub = _stub(locations=["London", "Remote (US)"], location="London; Remote (US)")

    result = prefilter_stub(stub, _criteria(exclude_locations=["london"], include_locations=["remote"]), guardrails)

    assert result is not None
    assert result["locations"] == ["London", "Remote (US)"]
    assert result["possible_blockers"] == []


def test_declared_workplace_type_overrides_location_string_matching():
    """Ashby reports `isRemote: true` on plainly hybrid roles, so only the
    explicit workplaceType is trusted."""
    company = _company("Example Ashby", "ashby", "https://jobs.ashbyhq.com/example", "https://api.ashbyhq.com/posting-api/job-board/example")

    jobs = fetch_ashby_jobs(
        {"jobs": [{"title": "Staff Platform Engineer", "jobUrl": "https://jobs.ashbyhq.com/example/1", "location": "New York, NY (HQ)", "isRemote": True, "workplaceType": "Hybrid"}]},
        company,
    )
    guardrails = TitleGuardrails(positive=[], negative=[], seniority_boost=[])

    assert prefilter_stub(jobs[0], _criteria(remote_modes=["hybrid"]), guardrails)["remote_mode"] == "hybrid"


def test_clearance_and_sponsorship_requirements_are_hard_exclusions():
    guardrails = TitleGuardrails(positive=[], negative=[], seniority_boost=[])
    body = BodyGuardrails.from_patterns(["security clearance|\\bTS/SCI\\b", "unable to sponsor"])

    assert prefilter_stub(_stub(description_text="Requires an active TS/SCI clearance."), _criteria(), guardrails, body) is None
    assert prefilter_stub(_stub(description_text="We are unable to sponsor employment visas."), _criteria(), guardrails, body) is None
    assert prefilter_stub(_stub(), _criteria(), guardrails, body) is not None


def test_same_role_cross_posted_to_regional_boards_is_collapsed():
    survivors = [
        {"company": "MongoDB", "title": "Site Reliability Engineer", "url": "https://x/1", "locations": ["Toronto"], "prefilter_score": 10.0},
        {"company": "MongoDB", "title": "Site Reliability Engineer", "url": "https://x/2", "locations": ["Remote (US)"], "prefilter_score": 40.0},
    ]

    merged = dedupe_survivors(survivors)

    assert len(merged) == 1
    assert merged[0]["url"] == "https://x/2"
    assert set(merged[0]["locations"]) == {"Toronto", "Remote (US)"}


def test_workday_list_rows_are_marked_for_hydration():
    """Workday list rows carry no body, no resolved location and only a
    relative date label, so they are unusable until the detail fetch runs."""
    company = _company("Capital One", "workday", "https://capitalone.wd12.myworkdayjobs.com/Capital_One", "https://capitalone.wd12.myworkdayjobs.com/wday/cxs/capitalone/Capital_One/jobs")

    jobs = fetch_workday_jobs(
        [{"title": "Staff Software Engineer", "externalPath": "/job/McLean-VA/Staff_R1", "locationsText": "4 Locations", "postedOn": "Posted Today"}],
        company,
        company.api_url,
    )

    assert jobs[0]["needs_hydration"] is True
    # The detail path hangs off the site root, not off the /jobs list endpoint.
    assert jobs[0]["detail_url"] == "https://capitalone.wd12.myworkdayjobs.com/wday/cxs/capitalone/Capital_One/job/McLean-VA/Staff_R1"


def test_workday_detail_supplies_body_locations_and_a_real_date():
    stub = {"provider": "workday", "title": "Staff Software Engineer", "location": "", "locations": []}

    _apply_workday_detail(
        stub,
        {"jobPostingInfo": {"jobDescription": "<p>Distributed systems.</p>", "location": "McLean, VA", "additionalLocations": ["US Remote"], "startDate": "2026-09-01", "postedOn": "Posted Today", "timeType": "Full time"}},
    )

    assert stub["locations"] == ["McLean, VA", "US Remote"]
    assert stub["posted_at"] == "2026-09-01"
    assert stub["description_text"] == "Distributed systems."


def test_smartrecruiters_marks_remote_region_and_needs_a_body_fetch():
    company = _company("SmartRecruiters", "smartrecruiters", "https://jobs.smartrecruiters.com/smartrecruiters", "https://api.smartrecruiters.com/v1/companies/smartrecruiters/postings")

    jobs = fetch_smartrecruiters_jobs(
        [{"id": "7440", "name": "Staff Platform Engineer", "company": {"identifier": "smartrecruiters"}, "location": {"city": "Krakow", "region": "REMOTE", "country": "pl"}, "releasedDate": "2026-08-12T14:04:56.128Z"}],
        company,
    )

    assert jobs[0]["needs_hydration"] is True
    assert jobs[0]["posted_at"] == "2026-08-12T14:04:56.128Z"
    assert jobs[0]["declared_remote_mode"] == "remote"


def test_hydration_budget_is_shared_across_companies():
    """One large board produces more title-gate survivors than the rest of the
    catalog combined; round-robin keeps it from consuming the whole budget."""
    catalog = load_source_catalog(str(FIXTURES / "valid_catalog.yml"))
    provider = ATSAPIProvider(catalog)
    stubs = [{"title": f"Staff Engineer {i}", "company": "Big Board"} for i in range(50)]
    stubs += [{"title": "Staff Engineer", "company": "Small Board"}]

    ordered = provider._hydration_order(stubs, {"role_families": []})

    assert ordered[1]["company"] == "Small Board"


def test_unhydrated_stub_survives_the_location_filter_but_is_penalised():
    """Past the detail-fetch budget a stub has no location at all. Dropping it
    would make a busy board silently vanish, so it is kept and labelled."""
    guardrails = TitleGuardrails(positive=[], negative=[], seniority_boost=[])
    stub = _stub(locations=[], location="", hydration_skipped=True)

    result = prefilter_stub(stub, _criteria(include_locations=["remote"]), guardrails)

    assert result is not None
    assert result["possible_blockers"] == ["location not verified"]
    assert result["prefilter_score"] < prefilter_stub(_stub(), _criteria(include_locations=["remote"]), guardrails)["prefilter_score"]


def test_refresh_bypasses_the_cached_feed(monkeypatch):
    """Busting only the query cache would re-rank the same stale feed, making
    the Refresh button appear to do nothing until the feed TTL lapses."""
    catalog = load_source_catalog(str(FIXTURES / "valid_catalog.yml"))
    provider = ATSAPIProvider(catalog)
    reads: list[str] = []
    provider.cache = type(
        "SpyCache",
        (),
        {
            "get": lambda _self, ns, key, **kw: (reads.append(key), {"postings": []})[1],
            "put": lambda *a, **kw: None,
        },
    )()

    class _Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"jobPostings": []}

    posts: list[str] = []
    monkeypatch.setattr(
        "resume_agent.services.discovery.ats_provider.requests.post",
        lambda url, **kw: (posts.append(url), _Response())[1],
    )

    provider._refresh = False
    provider._fetch_paged_workday("https://example.com/jobs")
    assert reads == ["https://example.com/jobs"] and posts == []

    provider._refresh = True
    provider._fetch_paged_workday("https://example.com/jobs")
    assert reads == ["https://example.com/jobs"]  # cache not consulted again
    assert posts == ["https://example.com/jobs"]  # went to the network instead


def test_missing_compensation_is_none_not_the_string_none():
    """str(None) is the truthy string "None", which would persist and render
    as a salary on the role card."""
    company = _company("Example Ashby", "ashby", "https://jobs.ashbyhq.com/example", "https://api.ashbyhq.com/posting-api/job-board/example")

    jobs = fetch_ashby_jobs(
        {"jobs": [
            {"title": "Staff Engineer", "jobUrl": "https://x/1", "compensation": {"scrapeableCompensationSalarySummary": None, "compensationTierSummary": None}},
            {"title": "Staff Engineer", "jobUrl": "https://x/2", "compensation": {"scrapeableCompensationSalarySummary": "$190K - $270K"}},
        ]},
        company,
    )

    assert jobs[0]["compensation"] is None
    assert jobs[1]["compensation"] == "$190K - $270K"


def test_geographically_qualified_remote_does_not_satisfy_a_us_filter():
    """"Portugal, Remote" contains "remote", which would otherwise surface
    EU-only roles to a US-only search."""
    guardrails = TitleGuardrails(positive=[], negative=[], seniority_boost=[])
    criteria = _criteria(include_locations=["remote", "united states", "usa"])

    assert prefilter_stub(_stub(locations=["Portugal, Remote", "Lisbon"], location="Portugal, Remote; Lisbon"), criteria, guardrails) is None
    assert prefilter_stub(_stub(locations=["US Remote"], location="US Remote"), criteria, guardrails) is not None
    assert prefilter_stub(_stub(locations=["Remote"], location="Remote"), criteria, guardrails) is not None
    assert prefilter_stub(_stub(locations=["Remote - United States"], location="Remote - United States"), criteria, guardrails) is not None


def test_export_control_requirements_are_hard_exclusions():
    """ITAR/EAR roles are restricted to US persons — citizen, national or
    permanent resident — so an H-1B holder is ineligible regardless of fit.
    Every SpaceX posting carries this clause and none mention a clearance."""
    guardrails = TitleGuardrails(positive=[], negative=[], seniority_boost=[])
    body = BodyGuardrails.from_patterns(
        [r"\bITAR\b|\bexport[- ]control(?:led|s)?\b", r"must be a[n]?(?:\s*\(?[ivx]+\)?)?\s*(?:U\.?S\.?|United States)\s*(?:citizen|person|national)"]
    )

    itar = _stub(description_text="ITAR REQUIREMENTS: To conform to U.S. Government export regulations, applicant must be a (i) U.S. citizen or national.")
    assert prefilter_stub(itar, _criteria(), guardrails, body) is None
    assert prefilter_stub(_stub(), _criteria(), guardrails, body) is not None


def test_discipline_rules_match_the_opening_not_the_whole_body():
    """A backend posting mentions frontend work in passing; a frontend posting
    leads with it. Scanning the whole body would reject good matches."""
    guardrails = TitleGuardrails(positive=[], negative=[], seniority_boost=[])
    body = BodyGuardrails.from_patterns([], [r"\b(is )?looking for an? frontend engineer\b"])

    frontend = _stub(description_text="Perplexity is looking for a frontend engineer to build the design system.")
    backend = _stub(description_text="Build distributed systems. " * 40 + " You will pair with a frontend engineer occasionally.")

    assert prefilter_stub(frontend, _criteria(), guardrails, body) is None
    assert prefilter_stub(backend, _criteria(), guardrails, body) is not None


def test_named_towns_no_longer_exclude_every_remote_posting():
    """Typing a home city used to contradict ticking "remote": "Remote - United
    States" carries no town name, so the include filter dropped it. With remote
    requested, an acceptable remote scope satisfies the filter on its own."""
    guardrails = TitleGuardrails(positive=[], negative=[], seniority_boost=[])
    criteria = _criteria(include_locations=["ashburn", "bethesda", "rockville", "arlington"])

    for location in ("Remote - United States", "US Remote", "Remote", "Remote, Americas"):
        stub = _stub(locations=[location], location=location)
        assert prefilter_stub(stub, criteria, guardrails) is not None, location

    onsite = _stub(locations=["Austin, TX"], location="Austin, TX")
    assert prefilter_stub(onsite, criteria, guardrails) is None

    named_town = _stub(locations=["Bethesda, MD"], location="Bethesda, MD")
    assert prefilter_stub(named_town, criteria, guardrails) is not None


def test_remote_scope_outside_the_accepted_list_is_still_rejected():
    """The scope list is what stops this from re-admitting EU-only remote work."""
    guardrails = TitleGuardrails(positive=[], negative=[], seniority_boost=[])
    criteria = _criteria(include_locations=["arlington"])

    for location in ("Portugal, Remote", "Remote - India", "Remote (EMEA)"):
        stub = _stub(locations=[location], location=location)
        assert prefilter_stub(stub, criteria, guardrails) is None, location


def test_remote_scope_only_applies_when_remote_was_requested():
    guardrails = TitleGuardrails(positive=[], negative=[], seniority_boost=[])
    criteria = _criteria(include_locations=["arlington"], remote_modes=["onsite"])
    stub = _stub(locations=["Remote - United States"], location="Remote - United States")

    assert prefilter_stub(stub, criteria, guardrails) is None


def test_detail_fetches_skip_roles_the_location_filter_already_rejects():
    """The fetch budget is small and shared. Spent on roles the location filter
    is about to drop, the ones that survive arrive with no body and are then
    discarded for being too short — which is how a wide search returned 4 rows."""
    catalog = load_source_catalog(str(FIXTURES / "valid_catalog.yml"))
    provider = ATSAPIProvider(catalog)
    criteria = _criteria(include_locations=["arlington"])

    wanted = {"title": "Staff Engineer", "locations": ["Arlington, VA"], "needs_hydration": True}
    elsewhere = {"title": "Staff Engineer", "locations": ["Austin, TX"], "needs_hydration": True}
    unknown = {"title": "Staff Engineer", "locations": [], "needs_hydration": True}

    assert passes_location_gate(wanted, criteria) is True
    assert passes_location_gate(elsewhere, criteria) is False
    # No location data yet: only the detail fetch can say, so it keeps its slot.
    assert passes_location_gate(unknown, criteria) is True


def test_location_gate_is_a_no_op_without_location_filters():
    catalog = load_source_catalog(str(FIXTURES / "valid_catalog.yml"))
    ATSAPIProvider(catalog)
    criteria = _criteria(include_locations=[], exclude_locations=[])

    assert passes_location_gate({"locations": ["Austin, TX"]}, criteria) is True
