from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from html import unescape
from typing import Any, Optional
from urllib.parse import urlparse

import requests

from ...config import settings
from ...storage.cache_store import SQLiteCacheStore, get_cache_store
from ...utils.logger import logger
from .role_families import ROLE_FAMILY_EXPANSIONS
from .source_catalog import (
    SUPPORTED_ATS_PROVIDERS,
    BodyGuardrails,
    SourceCatalog,
    TitleGuardrails,
    TrackedCompany,
)


_USER_AGENT = "Mozilla/5.0 ResumeAgent/1.0"
_WORKDAY_PAGE_SIZE = 20
_SMARTRECRUITERS_PAGE_SIZE = 100


class ATSAPIProvider:
    name = "ats_api"

    def __init__(self, catalog: SourceCatalog, cache: SQLiteCacheStore | None = None):
        self._refresh = False
        self.catalog = catalog
        self.cache = cache or get_cache_store()
        self.catalog_hash = catalog.catalog_hash

    def fetch_stubs(self, criteria: dict[str, Any], max_results: int) -> list[dict[str, Any]]:
        # An explicit refresh has to reach the feed cache. Busting only the
        # query cache re-ranks the same stale feeds, so the button appears to
        # do nothing until the feed TTL lapses.
        self._refresh = bool(criteria.get("refresh"))
        companies = self.catalog.supported_companies()
        stubs: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=min(8, max(1, len(companies)))) as executor:
            futures = {
                executor.submit(self._fetch_company_jobs, company): company
                for company in companies
            }
            for future in as_completed(futures):
                company = futures[future]
                try:
                    stubs.extend(future.result())
                except Exception as exc:
                    logger.warning("ATS company fetch failed", company=company.name, error=str(exc))

        # Phase 1: the title gate only. It is cheap, it discards most of the
        # feed, and for Workday and SmartRecruiters it is the sole check their
        # list endpoints can serve — those return a title and nothing else.
        title_survivors = [
            stub for stub in stubs if passes_title_gate(stub, criteria, self.catalog.title_guardrails)
        ]

        # Phase 2: drop what the location filter can already reject from feed
        # data alone. This runs BEFORE hydration on purpose. The detail-fetch
        # budget is small (discover_max_hydration_fetches) and shared; spending
        # it on roles the location filter is about to reject starves the ones
        # that survive, which then arrive with no body and get discarded for
        # being too short. Stubs with no location data yet are kept — only
        # hydration can tell where they are.
        hydration_candidates = [
            stub
            for stub in title_survivors
            if stub.get("needs_hydration") and passes_location_gate(stub, criteria)
        ]

        # Phase 3: fetch detail pages for the providers that need one, capped so
        # that a wide catalog cannot fan out into thousands of requests.
        self._hydrate(self._hydration_order(hydration_candidates, criteria))

        survivors: list[dict[str, Any]] = []
        for stub in title_survivors:
            filtered = prefilter_stub(
                stub,
                criteria,
                self.catalog.title_guardrails,
                self.catalog.body_guardrails,
            )
            if filtered:
                survivors.append(filtered)
        survivors = dedupe_survivors(survivors)
        survivors.sort(
            key=lambda item: (
                -float(item.get("prefilter_score") or 0.0),
                item.get("posted_at") is None,
                item.get("posted_at") or "",
                str(item.get("company") or "").lower(),
                str(item.get("title") or "").lower(),
            )
        )
        return survivors[:max_results]

    def _fetch_company_jobs(self, company: TrackedCompany) -> list[dict[str, Any]]:
        detected = detect_api(company)
        if not detected:
            return []
        provider = detected["type"]
        api_url = detected["url"]
        if provider == "workday":
            return fetch_workday_jobs(self._fetch_paged_workday(api_url), company, api_url)
        if provider == "smartrecruiters":
            return fetch_smartrecruiters_jobs(self._fetch_paged_smartrecruiters(api_url), company)
        payload = self._fetch_feed_payload(company, api_url, provider)
        if provider == "greenhouse":
            return fetch_greenhouse_jobs(payload, company)
        if provider == "ashby":
            return fetch_ashby_jobs(payload, company)
        if provider == "lever":
            return fetch_lever_jobs(payload, company)
        logger.warning("No ATS adapter for provider", company=company.name, provider=provider)
        return []

    def _fetch_paged_workday(self, api_url: str) -> list[dict[str, Any]]:
        """Workday pages 20 postings at a time and reports no total up front."""
        cached = None if self._refresh else self.cache.get("discover_ats_feed", api_url)
        if cached and "postings" in cached:
            return cached["postings"]

        postings: list[dict[str, Any]] = []
        offset = 0
        while offset < settings.discover_max_provider_postings:
            response = requests.post(
                api_url,
                json={"appliedFacets": {}, "limit": _WORKDAY_PAGE_SIZE, "offset": offset, "searchText": ""},
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": _USER_AGENT,
                },
                timeout=25,
            )
            response.raise_for_status()
            page = response.json().get("jobPostings") or []
            postings.extend(page)
            if len(page) < _WORKDAY_PAGE_SIZE:
                break
            offset += _WORKDAY_PAGE_SIZE

        self.cache.put(
            "discover_ats_feed",
            api_url,
            {"postings": postings},
            expires_at=self._feed_expiry(),
        )
        return postings

    def _fetch_paged_smartrecruiters(self, api_url: str) -> list[dict[str, Any]]:
        cached = None if self._refresh else self.cache.get("discover_ats_feed", api_url)
        if cached and "postings" in cached:
            return cached["postings"]

        postings: list[dict[str, Any]] = []
        offset = 0
        while offset < settings.discover_max_provider_postings:
            separator = "&" if "?" in api_url else "?"
            response = requests.get(
                f"{api_url}{separator}limit={_SMARTRECRUITERS_PAGE_SIZE}&offset={offset}",
                headers={"User-Agent": _USER_AGENT},
                timeout=25,
            )
            response.raise_for_status()
            payload = response.json()
            page = payload.get("content") or []
            postings.extend(page)
            if len(page) < _SMARTRECRUITERS_PAGE_SIZE:
                break
            offset += _SMARTRECRUITERS_PAGE_SIZE

        self.cache.put(
            "discover_ats_feed",
            api_url,
            {"postings": postings},
            expires_at=self._feed_expiry(),
        )
        return postings

    def _feed_expiry(self) -> str:
        return (
            datetime.now(timezone.utc) + timedelta(minutes=settings.discover_ats_feed_ttl_minutes)
        ).isoformat()

    def _hydration_order(
        self,
        stubs: list[dict[str, Any]],
        criteria: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Rank detail fetches by what the title alone already tells us.

        The budget is shared, and a single large Workday board can produce more
        title-gate survivors than the whole rest of the catalog. Spending the
        budget in feed order lets that one board crowd everything else out, so
        the most promising titles go first and companies are interleaved.
        """
        boost = [term.lower() for term in self.catalog.title_guardrails.seniority_boost]
        families = criteria.get("role_families") or []
        wanted = [variant for family in families for variant in ROLE_FAMILY_EXPANSIONS.get(family) or []]

        def title_priority(stub: dict[str, Any]) -> float:
            title = str(stub.get("title") or "").lower()
            score = 0.0
            if any(term in title for term in boost):
                score += 2
            if any(term in title for term in wanted):
                score += 1
            return score

        by_company: dict[str, list[dict[str, Any]]] = {}
        for stub in sorted(stubs, key=title_priority, reverse=True):
            by_company.setdefault(str(stub.get("company") or ""), []).append(stub)

        # Round-robin across companies so no single board consumes the budget.
        ordered: list[dict[str, Any]] = []
        queues = list(by_company.values())
        while queues:
            for queue in list(queues):
                ordered.append(queue.pop(0))
                if not queue:
                    queues.remove(queue)
        return ordered

    def _hydrate(self, stubs: list[dict[str, Any]]) -> None:
        """Fill in body text and resolved locations from a posting's detail page.

        Workday and SmartRecruiters list endpoints return a title and little
        else, so location and body filtering are impossible until this runs.
        Stubs beyond the cap keep their `hydration_skipped` flag: they survive
        the location filter that they could not otherwise pass, take a ranking
        penalty, and carry a "location not verified" blocker.
        """
        if not stubs:
            return
        budget = settings.discover_max_hydration_fetches
        selected, deferred = stubs[:budget], stubs[budget:]
        for stub in deferred:
            stub["hydration_skipped"] = True

        with ThreadPoolExecutor(max_workers=min(8, max(1, len(selected)))) as executor:
            futures = {executor.submit(self._hydrate_one, stub): stub for stub in selected}
            for future in as_completed(futures):
                stub = futures[future]
                try:
                    future.result()
                except Exception as exc:
                    stub["hydration_skipped"] = True
                    logger.warning(
                        "ATS detail fetch failed",
                        company=stub.get("company"),
                        title=stub.get("title"),
                        error=str(exc),
                    )

    def _hydrate_one(self, stub: dict[str, Any]) -> None:
        detail_url = stub.get("detail_url")
        if not detail_url:
            stub["hydration_skipped"] = True
            return

        cached = None if self._refresh else self.cache.get("discover_ats_detail", detail_url)
        payload = cached.get("payload") if cached and "payload" in cached else None
        if payload is None:
            response = requests.get(
                detail_url,
                headers={"Accept": "application/json", "User-Agent": _USER_AGENT},
                timeout=25,
            )
            response.raise_for_status()
            payload = response.json()
            self.cache.put(
                "discover_ats_detail",
                detail_url,
                {"payload": payload},
                expires_at=self._feed_expiry(),
            )

        if stub.get("provider") == "workday":
            _apply_workday_detail(stub, payload)
        else:
            _apply_smartrecruiters_detail(stub, payload)
        stub["needs_hydration"] = False

    def _fetch_feed_payload(self, company: TrackedCompany, api_url: str, provider: str) -> Any:
        cache_key = api_url
        cached = None if self._refresh else self.cache.get("discover_ats_feed", cache_key)
        if cached and "payload" in cached:
            return cached["payload"]

        if provider == "ashby" and "non-user-graphql" in api_url:
            slug = company.careers_url.rstrip("/").split("/")[-1]
            response = requests.post(
                api_url,
                json={
                    "operationName": "ApiJobBoardWithTeams",
                    "variables": {"organizationHostedJobsPageName": slug},
                    "query": "query ApiJobBoardWithTeams($organizationHostedJobsPageName: String!) { jobBoardWithTeams(organizationHostedJobsPageName: $organizationHostedJobsPageName) { jobPostings { id title locationName employmentType isListed descriptionHtml applyUrl publishedDate } } }",
                },
                timeout=20,
            )
        else:
            response = requests.get(api_url, headers={"User-Agent": _USER_AGENT}, timeout=20)
        response.raise_for_status()
        payload = response.json()
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=settings.discover_ats_feed_ttl_minutes)).isoformat()
        self.cache.put("discover_ats_feed", cache_key, {"payload": payload}, expires_at=expires_at)
        return payload


def detect_api(company: TrackedCompany) -> Optional[dict[str, str]]:
    if company.provider in SUPPORTED_ATS_PROVIDERS and company.api_url:
        return {"type": company.provider, "url": company.api_url}

    url = company.careers_url or ""
    # e.g. https://capitalone.wd12.myworkdayjobs.com/Capital_One
    workday_match = re.search(r"https://([^.]+)\.(wd\d+)\.myworkdayjobs\.com/([^/?#]+)", url)
    if workday_match:
        tenant, datacenter, site = workday_match.groups()
        return {
            "type": "workday",
            "url": f"https://{tenant}.{datacenter}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs",
        }
    smartrecruiters_match = re.search(r"jobs\.smartrecruiters\.com/([^/?#]+)", url)
    if smartrecruiters_match:
        return {
            "type": "smartrecruiters",
            "url": f"https://api.smartrecruiters.com/v1/companies/{smartrecruiters_match.group(1)}/postings",
        }
    ashby_match = re.search(r"jobs\.ashbyhq\.com/([^/?#]+)", url)
    if ashby_match:
        return {
            "type": "ashby",
            "url": f"https://api.ashbyhq.com/posting-api/job-board/{ashby_match.group(1)}?includeCompensation=true",
        }
    lever_match = re.search(r"jobs\.lever\.co/([^/?#]+)", url)
    if lever_match:
        return {"type": "lever", "url": f"https://api.lever.co/v0/postings/{lever_match.group(1)}?mode=json"}
    greenhouse_match = re.search(r"job-boards(?:\.eu)?\.greenhouse\.io/([^/?#]+)", url)
    if greenhouse_match:
        return {"type": "greenhouse", "url": f"https://boards-api.greenhouse.io/v1/boards/{greenhouse_match.group(1)}/jobs"}
    return None


def _clean_text(value: Any) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _epoch_ms_to_iso(value: Any) -> str:
    try:
        millis = int(value)
    except (TypeError, ValueError):
        return ""
    if millis <= 0:
        return ""
    return datetime.fromtimestamp(millis / 1000, tz=timezone.utc).isoformat()


def _posted_at(job: dict[str, Any]) -> str:
    """Real first-publication date.

    Greenhouse `updated_at` is a bulk-refresh timestamp — on a typical board it
    reads the same recent day for nearly every posting, including ones first
    published two years ago. It is only ever a last resort.
    """
    for key in ("first_published", "publishedAt", "publishedDate", "posted_at"):
        value = str(job.get(key) or "").strip()
        if value:
            return value
    epoch = _epoch_ms_to_iso(job.get("createdAt"))  # Lever reports epoch millis
    if epoch:
        return epoch
    return str(job.get("updated_at") or "").strip()


def _collect_locations(job: dict[str, Any]) -> list[str]:
    """Every location a posting is open in.

    Each provider reports one primary location plus a separate list of the
    rest. Filtering on the primary alone silently drops multi-site roles.
    """
    found: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip()
        if text:
            found.extend(part.strip() for part in text.split(";") if part.strip())

    raw_location = job.get("location")
    add(raw_location.get("name") if isinstance(raw_location, dict) else raw_location)
    add(job.get("locationName"))

    for office in job.get("offices") or []:
        if isinstance(office, dict):
            add(office.get("location") or office.get("name"))

    for secondary in job.get("secondaryLocations") or []:
        add(secondary.get("location") if isinstance(secondary, dict) else secondary)

    categories = job.get("categories")
    if isinstance(categories, dict):
        add(categories.get("location"))
        for item in categories.get("allLocations") or []:
            add(item)

    return list(dict.fromkeys(found))


_PROVIDER_REMOTE_MODES = {"remote": "remote", "hybrid": "hybrid", "onsite": "onsite"}


def _declared_remote_mode(job: dict[str, Any]) -> str:
    """Explicit workplaceType, when the provider states one.

    Ashby's `isRemote` is deliberately ignored: it reads True on plainly
    hybrid postings, so only `workplaceType` is trustworthy.
    """
    declared = str(job.get("workplaceType") or "").strip().lower()
    return _PROVIDER_REMOTE_MODES.get(declared, "")


def _compensation_summary(job: dict[str, Any]) -> Optional[str]:
    compensation = job.get("compensation")
    if not isinstance(compensation, dict):
        return None
    summary = (
        compensation.get("scrapeableCompensationSalarySummary")
        or compensation.get("compensationTierSummary")
    )
    # Guard the falsy case explicitly: str(None) is the truthy string "None",
    # which would be stored and rendered as a salary.
    if not summary:
        return None
    return str(summary).strip() or None


def normalize_stub(job: dict[str, Any], company: TrackedCompany, provider: str) -> dict[str, Any]:
    title = str(job.get("title") or job.get("text") or "").strip()
    url = str(job.get("url") or job.get("absolute_url") or job.get("jobUrl") or job.get("hostedUrl") or job.get("applyUrl") or "").strip()
    locations = _collect_locations(job)
    location = "; ".join(locations)
    apply_url = str(job.get("apply_url") or job.get("applyUrl") or url).strip() or url
    description_html = str(job.get("descriptionHtml") or job.get("content") or job.get("description") or "").strip()
    description_text = _clean_text(job.get("descriptionText") or job.get("descriptionPlain") or description_html)
    employment_type = (
        str(job.get("employment_type") or "").strip()
        or str(job.get("employmentType") or "").strip()
        or str((job.get("categories") or {}).get("commitment") or "").strip()
        or "unknown"
    )
    posted_at = _posted_at(job)
    source_domain = urlparse(url).netloc.lower() or urlparse(company.careers_url).netloc.lower()
    return {
        "title": title,
        "url": url,
        "company": company.name,
        "location": location,
        "locations": locations,
        "declared_remote_mode": _declared_remote_mode(job),
        "compensation": _compensation_summary(job),
        "source_domain": source_domain,
        "source_quality": "public_ats",
        "posted_at": posted_at or None,
        "employment_type": employment_type,
        "apply_url": apply_url or None,
        "description_html": description_html or None,
        "description_text": description_text or None,
        "provider": provider,
        "sponsorship_policy": company.sponsorship_policy,
    }


def fetch_greenhouse_jobs(payload: Any, company: TrackedCompany) -> list[dict[str, Any]]:
    jobs = payload.get("jobs") if isinstance(payload, dict) else []
    return [normalize_stub(job, company, "greenhouse") for job in jobs or [] if isinstance(job, dict)]


def fetch_ashby_jobs(payload: Any, company: TrackedCompany) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        jobs = payload.get("jobs") or []
        if not jobs:
            jobs = (
                payload.get("data", {})
                .get("jobBoardWithTeams", {})
                .get("jobPostings", [])
            )
    return [normalize_stub(job, company, "ashby") for job in jobs if isinstance(job, dict)]


def fetch_lever_jobs(payload: Any, company: TrackedCompany) -> list[dict[str, Any]]:
    jobs = payload if isinstance(payload, list) else []
    return [normalize_stub(job, company, "lever") for job in jobs if isinstance(job, dict)]


def fetch_workday_jobs(
    postings: list[dict[str, Any]],
    company: TrackedCompany,
    api_url: str,
) -> list[dict[str, Any]]:
    """Workday list rows carry a title and an `externalPath` and nothing usable.

    `locationsText` is often a bare count ("4 Locations") and `postedOn` is a
    relative label ("Posted Today"), so both are left out here and filled in
    during hydration from the detail page.
    """
    # The list endpoint ends in /jobs; detail paths hang off the site root, and
    # `externalPath` already carries its own leading /job/... segment.
    base = re.sub(r"/jobs/?$", "", api_url.rstrip("/"))
    stubs: list[dict[str, Any]] = []
    for posting in postings:
        if not isinstance(posting, dict):
            continue
        external_path = str(posting.get("externalPath") or "").strip()
        if not external_path:
            continue
        stub = normalize_stub({"title": posting.get("title")}, company, "workday")
        stub["url"] = f"{company.careers_url.rstrip('/')}{external_path}"
        stub["apply_url"] = stub["url"]
        stub["source_domain"] = urlparse(stub["url"]).netloc.lower()
        stub["detail_url"] = f"{base}{external_path}"
        stub["needs_hydration"] = True
        stubs.append(stub)
    return stubs


def fetch_smartrecruiters_jobs(
    postings: list[dict[str, Any]],
    company: TrackedCompany,
) -> list[dict[str, Any]]:
    """SmartRecruiters list rows resolve title, location and date but no body."""
    stubs: list[dict[str, Any]] = []
    for posting in postings:
        if not isinstance(posting, dict):
            continue
        posting_id = str(posting.get("id") or "").strip()
        if not posting_id:
            continue
        identifier = str((posting.get("company") or {}).get("identifier") or "").strip()
        location = posting.get("location") or {}
        location_text = ", ".join(
            part
            for part in (
                str(location.get("city") or "").strip(),
                str(location.get("region") or "").strip(),
                str(location.get("country") or "").strip(),
            )
            if part
        )
        stub = normalize_stub(
            {
                "title": posting.get("name"),
                "location": location_text,
                "publishedAt": posting.get("releasedDate"),
                "employmentType": (posting.get("typeOfEmployment") or {}).get("label"),
                "workplaceType": "remote" if str(location.get("region") or "").upper() == "REMOTE" else "",
            },
            company,
            "smartrecruiters",
        )
        stub["url"] = f"https://jobs.smartrecruiters.com/{identifier}/{posting_id}"
        stub["apply_url"] = stub["url"]
        stub["source_domain"] = "jobs.smartrecruiters.com"
        stub["detail_url"] = f"https://api.smartrecruiters.com/v1/companies/{identifier}/postings/{posting_id}"
        stub["needs_hydration"] = True
        stubs.append(stub)
    return stubs


def _apply_workday_detail(stub: dict[str, Any], payload: Any) -> None:
    info = (payload or {}).get("jobPostingInfo") or {}
    description = _clean_text(info.get("jobDescription"))
    if description:
        stub["description_html"] = info.get("jobDescription")
        stub["description_text"] = description

    locations = [
        item
        for item in [str(info.get("location") or "").strip(), *(info.get("additionalLocations") or [])]
        if item
    ]
    if locations:
        stub["locations"] = list(dict.fromkeys(locations))
        stub["location"] = "; ".join(stub["locations"])

    # `startDate` is a real ISO date; `postedOn` is a relative label.
    if info.get("startDate"):
        stub["posted_at"] = str(info["startDate"])
    if info.get("timeType"):
        stub["employment_type"] = str(info["timeType"])
    if info.get("externalUrl"):
        stub["apply_url"] = str(info["externalUrl"])
    stub["remote_mode"] = _infer_remote_mode(stub)


def _apply_smartrecruiters_detail(stub: dict[str, Any], payload: Any) -> None:
    payload = payload or {}
    sections = ((payload.get("jobAd") or {}).get("sections")) or {}
    description = " ".join(
        _clean_text((sections.get(key) or {}).get("text"))
        for key in ("companyDescription", "jobDescription", "qualifications", "additionalInformation")
    ).strip()
    if description:
        stub["description_text"] = description
        stub["description_html"] = description


def _tokenize(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", value.lower()) if len(token) > 1}


def _infer_remote_mode(stub: dict[str, Any]) -> str:
    declared = str(stub.get("declared_remote_mode") or "").strip()
    if declared:
        return declared
    lower = str(stub.get("location") or "").lower()
    if "remote" in lower:
        return "remote"
    if "hybrid" in lower:
        return "hybrid"
    if lower:
        return "onsite"
    return "unknown"


def allowed_title_terms(criteria: dict[str, Any], title_guardrails: TitleGuardrails) -> list[str]:
    """Title whitelist for the requested role families.

    The configured `positive` list on its own is a short set of literal strings
    that rejects most real senior titles ("Staff Site Reliability Engineer",
    "Principal Java Engineer - Distributed Systems"). Expanding the requested
    families here is what makes choosing a role family widen the search, rather
    than only adjust the score of whatever already survived.
    """
    terms = [term.lower() for term in title_guardrails.positive]
    for family in criteria.get("role_families") or list(ROLE_FAMILY_EXPANSIONS):
        terms.extend(ROLE_FAMILY_EXPANSIONS.get(family) or [])
    return list(dict.fromkeys(terms))


def dedupe_survivors(survivors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse one role cross-posted to several regional boards.

    The same opening is frequently filed once per region with a distinct job
    id, so the canonical-url uniqueness the store enforces does not catch it.
    The best-scoring copy wins and absorbs the others' locations.
    """
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for stub in survivors:
        key = (
            str(stub.get("company") or "").strip().lower(),
            re.sub(r"\s+", " ", str(stub.get("title") or "")).strip().lower(),
        )
        existing = merged.get(key)
        if existing is None:
            merged[key] = stub
            continue
        winner, loser = (
            (existing, stub)
            if float(existing.get("prefilter_score") or 0) >= float(stub.get("prefilter_score") or 0)
            else (stub, existing)
        )
        winner["locations"] = list(dict.fromkeys([*(winner.get("locations") or []), *(loser.get("locations") or [])]))
        winner["location"] = "; ".join(winner["locations"])
        winner["duplicate_urls"] = list(
            dict.fromkeys([*(winner.get("duplicate_urls") or []), *(loser.get("duplicate_urls") or []), loser.get("url")])
        )
        merged[key] = winner
    return list(merged.values())


def passes_title_gate(
    stub: dict[str, Any],
    criteria: dict[str, Any],
    title_guardrails: TitleGuardrails,
) -> bool:
    title = str(stub.get("title") or "").strip().lower()
    if not title:
        return False
    positive = allowed_title_terms(criteria, title_guardrails)
    if positive and not any(term in title for term in positive):
        return False
    return not any(term.lower() in title for term in title_guardrails.negative)


def blocking_body_matches(stub: dict[str, Any], body_guardrails: BodyGuardrails) -> list[str]:
    """Requirements that make a role permanently inaccessible.

    Hard exclusions rather than score penalties: no degree of fit makes a
    TS/SCI or citizenship-gated posting applicable.
    """
    body = " ".join(
        part
        for part in (
            str(stub.get("title") or ""),
            str(stub.get("description_text") or ""),
            _clean_text(stub.get("description_html")),
        )
        if part
    )
    if not body:
        return []
    blocked = [pattern.pattern for pattern in body_guardrails.compiled_exclude if pattern.search(body)]

    # Discipline patterns match only the opening of the posting. A backend role
    # mentions "frontend" in passing further down; a frontend role says so up
    # front, so scanning the whole body here would reject good matches.
    opening = f"{stub.get('title') or ''} {str(stub.get('description_text') or '')[:600]}"
    blocked.extend(
        pattern.pattern
        for pattern in body_guardrails.compiled_discipline_exclude
        if pattern.search(opening)
    )
    return blocked


_WORK_MODE_TERMS = {"remote", "hybrid", "onsite"}
_WORK_MODE_RE = re.compile(r"\b(remote|hybrid|onsite|work from home|wfh)\b", re.IGNORECASE)


def _matches_include(location: str, include_locations: list[str], remote_requested: bool = False) -> bool:
    """Whether one location satisfies the include list.

    Matching "remote" as a plain substring treats "Portugal, Remote" as a hit,
    which surfaces EU-only roles to a US-only search. So a location that
    qualifies purely on a work-mode word must also name a place that is wanted:
    "US Remote" passes, "Portugal, Remote" does not, bare "Remote" does.

    When remote is one of the requested work modes, a role whose remote scope is
    in `settings.discover_remote_scope_terms` also passes even though it names
    none of the wanted places. Naming towns otherwise excluded every remote
    posting in the catalog — "Remote - United States" contains no town name — so
    ticking `remote` and typing a home city silently contradicted each other.
    The scope list is what keeps this from re-admitting EU-only remote work.
    """
    lowered = location.lower()
    specific = [term for term in include_locations if term not in _WORK_MODE_TERMS]
    if any(term in lowered for term in specific):
        return True
    if not any(term in lowered for term in include_locations):
        return remote_requested and _is_acceptable_remote_scope(lowered)
    if not specific:
        # The filter names no place at all, so work mode is the whole request.
        return True

    residue = re.sub(r"[^a-z]+", " ", _WORK_MODE_RE.sub(" ", lowered)).strip()
    if not residue:
        return True
    # Substring either way so "US Remote" still satisfies an "usa" filter.
    if any(term in residue or residue in term for term in specific):
        return True
    return remote_requested and _is_acceptable_remote_scope(lowered)


def _is_acceptable_remote_scope(lowered_location: str) -> bool:
    """A remote posting whose scope is one the search is willing to work in.

    Scope is read off the location string: "Remote - United States" scopes to
    the US, "Portugal, Remote" to Portugal. A bare "Remote" names no scope and
    is accepted. Configure the accepted scopes with DISCOVER_REMOTE_SCOPE_TERMS.
    """
    if not _WORK_MODE_RE.search(lowered_location):
        return False
    residue = re.sub(r"[^a-z]+", " ", _WORK_MODE_RE.sub(" ", lowered_location)).strip()
    if not residue:
        return True
    return any(term in residue for term in settings.discover_remote_scope_terms_list)


def passes_location_gate(stub: dict[str, Any], criteria: dict[str, Any]) -> bool:
    """Whether a stub's own location data already rules it out.

    Used to steer the detail-fetch budget before it is spent. A stub with no
    location data cannot be judged yet, so it passes here and is decided later
    in `prefilter_stub` once hydration has (or has not) filled it in.
    """
    locations = [item for item in (stub.get("locations") or []) if item]
    if not locations:
        candidate = str(stub.get("location") or "").strip()
        if not candidate:
            return True
        locations = [candidate]
    include_locations = [term.lower() for term in criteria.get("include_locations") or []]
    exclude_locations = [term.lower() for term in criteria.get("exclude_locations") or []]
    if not include_locations and not exclude_locations:
        return True
    remote_requested = "remote" in set(criteria.get("remote_modes") or [])
    return bool(
        _acceptable_locations(locations, include_locations, exclude_locations, remote_requested=remote_requested)
    )


def _acceptable_locations(
    locations: list[str],
    include_locations: list[str],
    exclude_locations: list[str],
    remote_requested: bool = False,
) -> list[str]:
    """Locations that survive the include/exclude rules.

    Evaluated per location, not against the concatenated blob: a role open in
    both "Remote (US)" and "London" is reachable, and matching "london" across
    the whole string would discard it.
    """
    surviving = [
        candidate
        for candidate in locations
        if not any(term in candidate.lower() for term in exclude_locations)
    ]
    if include_locations:
        surviving = [
            candidate
            for candidate in surviving
            if _matches_include(candidate, include_locations, remote_requested=remote_requested)
        ]
    return surviving


def prefilter_stub(
    stub: dict[str, Any],
    criteria: dict[str, Any],
    title_guardrails: TitleGuardrails,
    body_guardrails: "BodyGuardrails | None" = None,
) -> Optional[dict[str, Any]]:
    title = str(stub.get("title") or "").strip()
    locations = [item for item in (stub.get("locations") or []) if item]
    if not locations:
        locations = [str(stub.get("location") or "").strip()]
    haystack = " ".join([title, str(stub.get("company") or ""), *locations]).lower()

    positive = allowed_title_terms(criteria, title_guardrails)
    negative = [term.lower() for term in title_guardrails.negative]
    if positive and not any(term in title.lower() for term in positive):
        return None
    if any(term in title.lower() for term in negative):
        return None

    if body_guardrails is not None and blocking_body_matches(stub, body_guardrails):
        return None

    include_locations = [term.lower() for term in criteria.get("include_locations") or []]
    exclude_locations = [term.lower() for term in criteria.get("exclude_locations") or []]
    remote_mode = _infer_remote_mode(stub)
    remote_requested = "remote" in set(criteria.get("remote_modes") or [])
    surviving_locations = _acceptable_locations(
        locations, include_locations, exclude_locations, remote_requested=remote_requested
    )
    # Only a role with no location data at all gets the benefit of the doubt.
    # A role that does list locations, none of which are wanted, is genuinely
    # out of scope — being remote does not help when the remote scope is a
    # region the candidate cannot work in (an EU-only "remote" role).
    location_unknown = not any(locations)  # unhydrated, or a provider that omits it
    if not surviving_locations and not location_unknown:
        return None

    score = 0.0
    matched_filters: list[str] = []
    requested_remote = set(criteria.get("remote_modes") or [])
    if requested_remote and remote_mode in requested_remote:
        score += 15
        matched_filters.append(remote_mode)

    for family in criteria.get("role_families") or []:
        variants = ROLE_FAMILY_EXPANSIONS.get(family) or []
        if any(variant in title.lower() for variant in variants):
            score += 30
            matched_filters.append(family)
            break

    requested_seniority = str(criteria.get("seniority") or "any")
    if requested_seniority != "any" and requested_seniority in haystack:
        score += 10
        matched_filters.append(requested_seniority)
    for term in title_guardrails.seniority_boost:
        if term.lower() in title.lower():
            score += 4
            break

    search_tokens = _tokenize(str(criteria.get("search_intent") or ""))
    if search_tokens:
        overlap = len(search_tokens & _tokenize(haystack))
        score += min(12, overlap * 3)

    must_have = criteria.get("must_have_keywords") or []
    avoid = criteria.get("avoid_keywords") or []
    score += min(12, sum(1 for term in must_have if term in haystack) * 4)
    score -= min(20, sum(1 for term in avoid if term in haystack) * 5)
    if location_unknown:
        # Past the detail-fetch budget: kept, but ranked below anything whose
        # location was actually confirmed, and labelled so the reason is visible.
        score -= 25
    return {
        **stub,
        "remote_mode": remote_mode,
        "locations": locations,
        "possible_blockers": (["location not verified"] if location_unknown else []),
        "matched_filters": list(dict.fromkeys(matched_filters)),
        "prefilter_score": score,
    }
