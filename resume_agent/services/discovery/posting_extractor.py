from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup


KNOWN_ATS_DOMAINS = {
    "boards.greenhouse.io",
    "jobs.ashbyhq.com",
    "lever.co",
    "jobs.lever.co",
    "workday.com",
    "myworkdayjobs.com",
    "smartrecruiters.com",
}

AGGREGATOR_DOMAINS = {
    "indeed.com",
    "linkedin.com",
    "wellfound.com",
    "ziprecruiter.com",
    "builtin.com",
    "glassdoor.com",
}


def normalize_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc.lower()
    path = parsed.path or "/"
    return urlunparse((scheme, netloc, path.rstrip("/") or "/", "", "", ""))


def extract_domain(url: str) -> str:
    return urlparse(url).netloc.lower()


def source_quality_for_domain(domain: str) -> str:
    if any(domain == known or domain.endswith(f".{known}") for known in KNOWN_ATS_DOMAINS):
        return "public_ats"
    if any(domain == known or domain.endswith(f".{known}") for known in AGGREGATOR_DOMAINS):
        return "aggregator"
    if domain.startswith("jobs.") or domain.startswith("careers."):
        return "company_public"
    return "general_web"


def relative_posted_label(posted_at: str | None) -> str | None:
    if not posted_at:
        return None
    try:
        dt = datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    age = datetime.now(timezone.utc) - dt.astimezone(timezone.utc)
    if age < timedelta(days=1):
        hours = max(1, int(age.total_seconds() // 3600))
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = max(1, age.days)
    return f"{days} day{'s' if days != 1 else ''} ago"


@dataclass
class ExtractedPosting:
    canonical_url: str
    source_urls: list[str]
    source_domain: str
    company: str
    job_title: str
    matched_title_variant: str | None
    location: str | None
    remote_mode: str
    employment_type: str
    apply_url: str | None
    posted_at: str | None
    posted_label: str | None
    date_confidence: str
    archetype: str
    extraction_confidence: float
    raw_text: str
    raw_text_hash: str | None
    source_quality: str


def _clean_text(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", text or "")).strip()


def _extract_json_ld(soup: BeautifulSoup) -> list[dict]:
    items: list[dict] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            items.append(data)
        elif isinstance(data, list):
            items.extend(item for item in data if isinstance(item, dict))
    return items


def _flatten_job_posting(node: dict) -> dict | None:
    node_type = str(node.get("@type") or "").lower()
    if node_type == "jobposting":
        return node
    graph = node.get("@graph")
    if isinstance(graph, list):
        for item in graph:
            if isinstance(item, dict) and str(item.get("@type") or "").lower() == "jobposting":
                return item
    return None


def _meta_content(soup: BeautifulSoup, *names: str) -> str | None:
    for name in names:
        tag = soup.find("meta", attrs={"name": name}) or soup.find("meta", attrs={"property": name})
        content = tag.get("content") if tag else None
        if content and str(content).strip():
            return str(content).strip()
    return None


def _parse_date(value: str | None) -> tuple[str | None, str]:
    if not value:
        return None, "unknown"
    raw = str(value).strip()
    if not raw:
        return None, "unknown"
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    for candidate in (raw, raw.split("T")[0]):
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat(), "high"
        except ValueError:
            continue
    return None, "unknown"


def _infer_remote_mode(*values: str | None) -> str:
    haystack = " ".join(str(value or "") for value in values).lower()
    if "hybrid" in haystack:
        return "hybrid"
    if "remote" in haystack or "work from home" in haystack:
        return "remote"
    if "onsite" in haystack or "on-site" in haystack or "in office" in haystack:
        return "onsite"
    return "unknown"


def _infer_employment_type(*values: str | None) -> str:
    haystack = " ".join(str(value or "") for value in values).lower()
    if "full" in haystack and "time" in haystack:
        return "full_time"
    if "part" in haystack and "time" in haystack:
        return "part_time"
    if "contract" in haystack:
        return "contract"
    if "intern" in haystack:
        return "internship"
    return "unknown"


def _visible_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "nav", "footer", "noscript", "svg"]):
        tag.decompose()
    container = soup.find("main") or soup.find(attrs={"role": "main"}) or soup.body or soup
    parts = []
    for node in container.find_all(["h1", "h2", "h3", "p", "li", "div"]):
        text = node.get_text(" ", strip=True)
        if text and len(text) > 1:
            parts.append(text)
    return _clean_text("\n".join(parts))


def extract_posting_from_html(url: str, html: str, matched_title_variant: str | None = None) -> ExtractedPosting:
    soup = BeautifulSoup(html or "", "html.parser")
    source_domain = extract_domain(url)
    source_quality = source_quality_for_domain(source_domain)
    title = ""
    company = ""
    apply_url = None
    location = None
    posted_at = None
    date_confidence = "unknown"
    remote_mode = "unknown"
    employment_type = "unknown"
    confidence = 0.2

    for node in _extract_json_ld(soup):
        posting = _flatten_job_posting(node)
        if not posting:
            continue
        title = title or str(posting.get("title") or "").strip()
        hiring_org = posting.get("hiringOrganization")
        if isinstance(hiring_org, dict):
            company = company or str(hiring_org.get("name") or "").strip()
        location_obj = posting.get("jobLocation") or posting.get("applicantLocationRequirements")
        if isinstance(location_obj, list) and location_obj:
            location_obj = location_obj[0]
        if isinstance(location_obj, dict):
            address = location_obj.get("address")
            if isinstance(address, dict):
                location = location or ", ".join(
                    part for part in [
                        address.get("addressLocality"),
                        address.get("addressRegion"),
                        address.get("addressCountry"),
                    ] if part
                )
        apply_url = apply_url or posting.get("url")
        posted_at, date_confidence = _parse_date(posting.get("datePosted"))
        remote_mode = _infer_remote_mode(posting.get("jobLocationType"), posting.get("description"), location)
        employment_type = _infer_employment_type(posting.get("employmentType"))
        confidence = 0.92
        break

    title = title or _meta_content(soup, "og:title", "twitter:title") or (soup.title.string.strip() if soup.title and soup.title.string else "")
    company = company or _meta_content(soup, "og:site_name")
    meta_location = _meta_content(soup, "jobLocation", "location")
    location = location or meta_location
    remote_mode = _infer_remote_mode(remote_mode, meta_location, _meta_content(soup, "description", "og:description"))
    employment_type = _infer_employment_type(employment_type, _meta_content(soup, "employmentType"))
    if not posted_at:
        posted_at, date_confidence = _parse_date(_meta_content(soup, "article:published_time", "date"))
        if posted_at:
            confidence = max(confidence, 0.68)

    visible_text = _visible_text(soup)
    if not company and source_domain:
        company = source_domain.split(".")[0].replace("-", " ").replace("jobs", "").title().strip() or ""
    if not title and visible_text:
        first_line = visible_text.splitlines()[0]
        title = first_line[:180].strip()
    if "job application for" in title.lower():
        title = title.replace("Job application for", "").strip()

    if not apply_url:
        for anchor in soup.find_all("a", href=True):
            label = anchor.get_text(" ", strip=True).lower()
            href = anchor["href"]
            if any(token in label for token in ("apply", "submit application", "view job")):
                apply_url = urljoin(url, href)
                confidence = max(confidence, 0.6)
                break

    apply_url = apply_url.strip() if isinstance(apply_url, str) and apply_url.strip() else None
    canonical_url = normalize_url(apply_url or url)
    posted_label = posted_at[:10] if posted_at else None
    if not posted_label:
        posted_label = relative_posted_label(posted_at)
    readable_text = _clean_text(visible_text)
    raw_text_hash = sha256(readable_text.encode("utf-8")).hexdigest() if readable_text else None

    if len(readable_text) >= 350:
        confidence = max(confidence, 0.45)
    if title:
        confidence += 0.12
    if company:
        confidence += 0.1
    if location:
        confidence += 0.06
    confidence = max(0.0, min(confidence, 0.99))

    return ExtractedPosting(
        canonical_url=canonical_url,
        source_urls=[normalize_url(url)],
        source_domain=source_domain,
        company=company or "Unknown company",
        job_title=title or "Unknown role",
        matched_title_variant=matched_title_variant,
        location=location,
        remote_mode=remote_mode,
        employment_type=employment_type,
        apply_url=apply_url,
        posted_at=posted_at,
        posted_label=posted_label or "Date unavailable",
        date_confidence=date_confidence,
        archetype="unknown",
        extraction_confidence=confidence,
        raw_text=readable_text,
        raw_text_hash=raw_text_hash,
        source_quality=source_quality,
    )
