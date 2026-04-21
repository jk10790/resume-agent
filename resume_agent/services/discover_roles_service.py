from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any, Dict, Iterable, Optional
from urllib.parse import urlparse
import sqlite3

import requests
from langchain_core.messages import HumanMessage, SystemMessage

from ..config import settings
from ..storage.cache_store import get_cache_store
from ..storage.user_store import get_db_connection
from ..storage.user_store import (
    delete_discovery_saved_search_for_user,
    get_discovery_saved_search_for_user,
    get_discovery_user_preferences_for_user,
    get_discovered_role_for_user,
    get_latest_discovery_suggestion_event_for_user,
    link_discovered_role_strategy_brief_for_user,
    list_discovery_saved_searches_for_user,
    list_discovered_role_feedback_for_user,
    list_discovered_roles_for_user,
    mark_discovered_role_opened_in_tailor_for_user,
    mark_discovery_saved_search_used_for_user,
    record_discovered_role_feedback_for_user,
    record_discovery_suggestion_event_for_user,
    save_or_merge_discovered_role_for_user,
    save_discovery_saved_search_for_user,
    save_discovery_user_preferences_for_user,
    update_discovered_role_inbox_state_for_user,
)
from ..utils.logger import logger
from .discovery.firecrawl_provider import FirecrawlSearchProvider
from .discovery.posting_extractor import (
    ExtractedPosting,
    extract_posting_from_html,
    normalize_url,
    relative_posted_label,
)


ROLE_FAMILY_EXPANSIONS: dict[str, list[str]] = {
    "software_engineering": [
        "software engineer",
        "software developer",
        "backend engineer",
        "backend developer",
        "application engineer",
        "product engineer",
        "full stack engineer",
    ],
    "platform_infrastructure": [
        "platform engineer",
        "infrastructure engineer",
        "site reliability engineer",
        "sre",
        "devops engineer",
        "production engineer",
    ],
    "data_ml_ai": [
        "data engineer",
        "machine learning engineer",
        "ml engineer",
        "data platform engineer",
        "ai engineer",
    ],
    "applied_ai_llmops": [
        "applied ai engineer",
        "llm engineer",
        "llmops engineer",
        "agent engineer",
        "ai systems engineer",
        "ai platform engineer",
    ],
    "product_technical_product": [
        "product manager",
        "technical product manager",
        "platform product manager",
        "ai product manager",
    ],
    "solutions_customer_engineering": [
        "solutions engineer",
        "customer engineer",
        "sales engineer",
        "forward deployed engineer",
        "implementation engineer",
    ],
}

DISCOVERY_PROMPT_VERSION = "v1"
EXTRACTOR_VERSION = "v1"
DISCOVER_STATUS_REASONS = {
    "disabled": "Discover is disabled on this instance.",
    "provider_missing": "Discover search is not configured on this instance.",
}


class DiscoverConfigError(RuntimeError):
    pass


@dataclass
class DiscoverSearchCriteria:
    search_intent: str = ""
    role_families: list[str] | None = None
    seniority: str = "any"
    remote_modes: list[str] | None = None
    include_locations: list[str] | None = None
    exclude_locations: list[str] | None = None
    must_have_keywords: list[str] | None = None
    avoid_keywords: list[str] | None = None
    page_size: int = 20
    refresh: bool = False

    def normalized(self) -> dict[str, Any]:
        def norm_text(value: str) -> str:
            return " ".join(str(value or "").strip().lower().split())

        def norm_list(values: Optional[Iterable[str]]) -> list[str]:
            return sorted({norm_text(item) for item in (values or []) if norm_text(item)})

        return {
            "search_intent": norm_text(self.search_intent),
            "role_families": [item for item in (self.role_families or []) if item],
            "seniority": norm_text(self.seniority) or "any",
            "remote_modes": norm_list(self.remote_modes),
            "include_locations": norm_list(self.include_locations),
            "exclude_locations": norm_list(self.exclude_locations),
            "must_have_keywords": norm_list(self.must_have_keywords),
            "avoid_keywords": norm_list(self.avoid_keywords),
            "page_size": max(1, min(int(self.page_size or settings.discover_max_display_results), settings.discover_max_display_results)),
            "refresh": bool(self.refresh),
        }


class DiscoverRolesService:
    def __init__(self, llm_service=None, provider=None):
        self.llm_service = llm_service
        self.cache = get_cache_store()
        self.provider = provider or self._build_provider()

    def _build_provider(self):
        if not settings.discover_enabled:
            return None
        if settings.discover_provider == "firecrawl" and settings.discover_firecrawl_api_key:
            return FirecrawlSearchProvider()
        return None

    def get_status(self) -> dict[str, Any]:
        configured = bool(self.provider)
        enabled = bool(settings.discover_enabled and configured)
        reason = None
        if not settings.discover_enabled:
            reason = DISCOVER_STATUS_REASONS["disabled"]
        elif not configured:
            reason = DISCOVER_STATUS_REASONS["provider_missing"]
        return {
            "enabled": enabled,
            "provider": getattr(self.provider, "name", settings.discover_provider or "none"),
            "configured": configured,
            "reason": reason,
        }

    def get_preferences(self, user_id: int) -> dict[str, Any]:
        return get_discovery_user_preferences_for_user(user_id)

    def save_preferences(self, user_id: int, defaults: dict[str, Any]) -> dict[str, Any]:
        normalized = DiscoverSearchCriteria(
            search_intent=defaults.get("search_intent", ""),
            role_families=defaults.get("role_families") or [],
            seniority=defaults.get("seniority", "any"),
            remote_modes=defaults.get("remote_modes") or [],
            include_locations=defaults.get("include_locations") or [],
            exclude_locations=defaults.get("exclude_locations") or [],
            must_have_keywords=defaults.get("must_have_keywords") or [],
            avoid_keywords=defaults.get("avoid_keywords") or [],
            page_size=defaults.get("page_size", settings.discover_max_display_results),
            refresh=False,
        ).normalized()
        normalized.pop("refresh", None)
        return save_discovery_user_preferences_for_user(user_id, normalized)

    def list_saved_searches(self, user_id: int) -> list[dict[str, Any]]:
        return list_discovery_saved_searches_for_user(user_id)

    def save_search(self, user_id: int, *, name: str, criteria: dict[str, Any], search_id: int | None = None, is_default: bool = False) -> dict[str, Any]:
        normalized = DiscoverSearchCriteria(
            search_intent=criteria.get("search_intent", ""),
            role_families=criteria.get("role_families") or [],
            seniority=criteria.get("seniority", "any"),
            remote_modes=criteria.get("remote_modes") or [],
            include_locations=criteria.get("include_locations") or [],
            exclude_locations=criteria.get("exclude_locations") or [],
            must_have_keywords=criteria.get("must_have_keywords") or [],
            avoid_keywords=criteria.get("avoid_keywords") or [],
            page_size=criteria.get("page_size", settings.discover_max_display_results),
            refresh=False,
        ).normalized()
        normalized.pop("refresh", None)
        return save_discovery_saved_search_for_user(
            user_id,
            name=name,
            criteria=normalized,
            search_id=search_id,
            is_default=is_default,
        )

    def get_saved_search(self, user_id: int, search_id: int) -> Optional[dict[str, Any]]:
        return get_discovery_saved_search_for_user(user_id, search_id)

    def delete_saved_search(self, user_id: int, search_id: int) -> bool:
        return delete_discovery_saved_search_for_user(user_id, search_id)

    def apply_saved_search(self, user_id: int, search_id: int) -> Optional[dict[str, Any]]:
        saved = get_discovery_saved_search_for_user(user_id, search_id)
        if not saved:
            return None
        mark_discovery_saved_search_used_for_user(user_id, search_id)
        return saved

    def ensure_available(self) -> None:
        if not settings.discover_enabled or not self.provider:
            raise DiscoverConfigError(DISCOVER_STATUS_REASONS["provider_missing"])

    def _query_cache_key(self, user_id: int, normalized: dict[str, Any]) -> str:
        payload = json.dumps(
            {
                "user_id": user_id,
                "criteria": normalized,
                "role_expansion_version": settings.discover_role_expansion_version,
                "provider": self.provider.name if self.provider else "none",
            },
            sort_keys=True,
        )
        return sha256(payload.encode("utf-8")).hexdigest()

    def _page_cache_key(self, url: str) -> str:
        return sha256(normalize_url(url).encode("utf-8")).hexdigest()

    def _structured_cache_key(self, canonical_url: str) -> str:
        return sha256(f"{canonical_url}:{EXTRACTOR_VERSION}".encode("utf-8")).hexdigest()

    def _enrichment_cache_key(self, raw_text_hash: str) -> str:
        provider_name = getattr(self.llm_service, "provider_name", None) or "default"
        model_name = getattr(self.llm_service, "model_name", None) or "default"
        return sha256(f"{raw_text_hash}:{DISCOVERY_PROMPT_VERSION}:{provider_name}:{model_name}".encode("utf-8")).hexdigest()

    def _expiry(self, hours: int) -> str:
        return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()

    def _build_query_passes(self, normalized: dict[str, Any]) -> list[tuple[str, str | None]]:
        passes: list[tuple[str, str | None]] = []
        search_intent = normalized["search_intent"]
        role_families = normalized["role_families"][: settings.discover_max_query_variants]
        must_have = normalized["must_have_keywords"]
        for family in role_families:
            variants = ROLE_FAMILY_EXPANSIONS.get(family) or []
            if variants:
                passes.append((family, variants[0]))
        if not passes and search_intent:
            passes.append((None, None))

        query_passes: list[tuple[str, str | None]] = []
        for family, variant in passes[: settings.discover_max_query_variants]:
            query = " ".join(part for part in [search_intent, variant, " ".join(must_have)] if part).strip()
            if query:
                query_passes.append((query, variant))
        if not query_passes and search_intent:
            query_passes.append((search_intent, None))
        return query_passes[: settings.discover_max_query_variants]

    def _fetch_page(self, url: str) -> Optional[str]:
        cache_key = self._page_cache_key(url)
        cached = self.cache.get("discover_page_fetch", cache_key)
        if cached:
            return cached.get("html")
        response = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 ResumeAgent/1.0"},
            timeout=20,
        )
        response.raise_for_status()
        html = response.text
        self.cache.put(
            "discover_page_fetch",
            cache_key,
            {"url": normalize_url(url), "html": html},
            expires_at=self._expiry(settings.discover_posting_recent_ttl_hours),
        )
        return html

    def _structured_from_hit(self, hit: dict[str, Any], matched_title_variant: str | None) -> Optional[dict[str, Any]]:
        normalized_url = normalize_url(hit["url"])
        cache_key = self._structured_cache_key(normalized_url)
        cached = self.cache.get("discover_structured_posting", cache_key)
        if cached:
            return cached
        html = self._fetch_page(hit["url"])
        if not html:
            return None
        extracted = extract_posting_from_html(hit["url"], html, matched_title_variant=matched_title_variant)
        if len(extracted.raw_text) < settings.discover_min_extracted_text_chars:
            return None
        if extracted.extraction_confidence < 0.35:
            return None
        payload = {
            "canonical_url": extracted.canonical_url,
            "source_urls": extracted.source_urls,
            "source_domain": extracted.source_domain,
            "company": extracted.company,
            "job_title": extracted.job_title,
            "matched_title_variant": extracted.matched_title_variant,
            "location": extracted.location,
            "remote_mode": extracted.remote_mode,
            "employment_type": extracted.employment_type,
            "apply_url": extracted.apply_url,
            "posted_at": extracted.posted_at,
            "posted_label": extracted.posted_label or relative_posted_label(extracted.posted_at) or "Date unavailable",
            "date_confidence": extracted.date_confidence,
            "archetype": extracted.archetype,
            "extraction_confidence": extracted.extraction_confidence,
            "raw_text": extracted.raw_text,
            "raw_text_hash": extracted.raw_text_hash,
            "source_quality": extracted.source_quality,
        }
        ttl_hours = settings.discover_posting_recent_ttl_hours
        if extracted.posted_at:
            try:
                posted_dt = datetime.fromisoformat(extracted.posted_at.replace("Z", "+00:00"))
                if datetime.now(timezone.utc) - posted_dt > timedelta(days=14):
                    ttl_hours = settings.discover_posting_stale_ttl_hours
            except ValueError:
                pass
        else:
            ttl_hours = settings.discover_posting_recent_ttl_hours
        self.cache.put(
            "discover_structured_posting",
            cache_key,
            payload,
            source_hash=payload.get("raw_text_hash"),
            expires_at=self._expiry(ttl_hours),
        )
        canonical_cache_key = self._structured_cache_key(payload["canonical_url"])
        if canonical_cache_key != cache_key:
            self.cache.put(
                "discover_structured_posting",
                canonical_cache_key,
                payload,
                source_hash=payload.get("raw_text_hash"),
                expires_at=self._expiry(ttl_hours),
            )
        return payload

    def _cheap_archetype(self, text: str, matched_title_variant: str | None) -> str:
        haystack = f"{matched_title_variant or ''} {text}".lower()
        for family, variants in ROLE_FAMILY_EXPANSIONS.items():
            if any(variant in haystack for variant in variants):
                return family
        return "unknown"

    def _keyword_hits(self, text: str, values: list[str]) -> list[str]:
        haystack = text.lower()
        return [value for value in values if value and value in haystack]

    def _posted_bonus(self, posted_at: str | None) -> int:
        if not posted_at:
            return -10
        try:
            dt = datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
        except ValueError:
            return -10
        age_days = (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).days
        if age_days <= 7:
            return 15
        if age_days <= 30:
            return 8
        return 0

    def _rank_role(self, role: dict[str, Any], normalized: dict[str, Any]) -> tuple[float, list[str], list[str]]:
        score = 0.0
        matched_filters: list[str] = []
        blockers: list[str] = list(role.get("possible_blockers") or [])
        archetype = role.get("archetype") or "unknown"
        if archetype in normalized["role_families"]:
            score += 60
            matched_filters.append(archetype)
        if role.get("matched_title_variant") and any(
            role["matched_title_variant"] in ROLE_FAMILY_EXPANSIONS.get(family, [])
            for family in normalized["role_families"]
        ):
            score += 20
            matched_filters.append(role["matched_title_variant"])

        requested_remote = set(normalized["remote_modes"])
        actual_remote = str(role.get("remote_mode") or "unknown")
        if requested_remote:
            if actual_remote in requested_remote:
                score += 15
                matched_filters.append(actual_remote)
            elif actual_remote != "unknown":
                score -= 40
                blockers.append(f"{actual_remote} remote mode")

        location_blob = " ".join(
            filter(None, [str(role.get("location") or ""), str(role.get("raw_text") or "")[:600]])
        ).lower()
        include_hits = self._keyword_hits(location_blob, normalized["include_locations"])
        exclude_hits = self._keyword_hits(location_blob, normalized["exclude_locations"])
        if normalized["include_locations"]:
            if include_hits:
                score += 10
                matched_filters.extend(include_hits)
        else:
            score += 10
        if exclude_hits:
            score -= 50
            blockers.extend(exclude_hits)

        raw_text = str(role.get("raw_text") or "").lower()
        must_hits = self._keyword_hits(raw_text, normalized["must_have_keywords"])
        avoid_hits = self._keyword_hits(raw_text, normalized["avoid_keywords"])
        score += min(30, len(must_hits) * 10)
        score -= min(60, len(avoid_hits) * 20)
        matched_filters.extend(must_hits)
        blockers.extend(avoid_hits)

        score += self._posted_bonus(role.get("posted_at"))
        source_quality = role.get("source_quality")
        if source_quality == "company_public":
            score += 10
        elif source_quality == "public_ats":
            score += 8
        elif source_quality == "aggregator":
            score -= 15

        confidence = float(role.get("extraction_confidence") or 0.0)
        if confidence >= 0.85:
            score += 10
        elif confidence < 0.5:
            score -= 25
        return score, list(dict.fromkeys(matched_filters)), list(dict.fromkeys(blockers))[:4]

    def _fallback_enrichment(self, role: dict[str, Any]) -> dict[str, Any]:
        text = str(role.get("raw_text") or "")
        summary_source = re.sub(r"\s+", " ", text).strip()
        short = summary_source[:240].rsplit(" ", 1)[0].strip() if summary_source else ""
        if short and not short.endswith("."):
            short += "."
        seniority = "unknown"
        lower = text.lower()
        for value in ["principal", "director", "manager", "staff", "senior", "mid", "junior"]:
            if value in lower:
                seniority = value
                break
        return {
            "id": role["canonical_url"],
            "tldr": short or f"{role.get('job_title', 'Role')} at {role.get('company', 'Unknown company')}.",
            "archetype": self._cheap_archetype(text, role.get("matched_title_variant")),
            "seniority": seniority,
            "remote_mode": role.get("remote_mode") or "unknown",
            "possible_blockers": list(role.get("possible_blockers") or [])[:4],
        }

    def _enrich_roles(self, roles: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not roles:
            return roles
        enriched: list[dict[str, Any]] = []
        pending: list[dict[str, Any]] = []
        for role in roles:
            raw_text_hash = role.get("raw_text_hash")
            if raw_text_hash:
                cached = self.cache.get("discover_llm_enrichment", self._enrichment_cache_key(raw_text_hash))
                if cached:
                    enriched.append({**role, **cached})
                    continue
            pending.append(role)

        if not pending:
            return enriched

        if not self.llm_service:
            return enriched + [{**role, **self._fallback_enrichment(role)} for role in pending]

        for index in range(0, min(len(pending), settings.discover_max_survivors), 4):
            batch = pending[index:index + 4]
            if index // 4 >= 3:
                break
            prompt = SystemMessage(
                content="""Return valid JSON only as an array with one object per posting.
Describe the role only. Do not evaluate candidate fit. Do not recommend whether to apply.
Do not invent company facts. Do not add blockers not grounded in the page text."""
            )
            items = [
                {
                    "id": role["canonical_url"],
                    "text": str(role.get("raw_text") or "")[:1800],
                    "job_title": role.get("job_title"),
                    "company": role.get("company"),
                }
                for role in batch
            ]
            try:
                response = self.llm_service.invoke_with_retry(
                    [
                        prompt,
                        HumanMessage(
                            content=(
                                "Return JSON array with keys id, tldr, archetype, seniority, remote_mode, possible_blockers.\n\n"
                                + json.dumps(items)
                            )
                        ),
                    ]
                ).strip()
                match = re.search(r"\[.*\]", response, re.DOTALL)
                parsed = json.loads(match.group(0)) if match else []
            except Exception as exc:
                logger.warning("Discovery enrichment failed; using fallback", error=str(exc))
                parsed = [self._fallback_enrichment(role) for role in batch]
            by_id = {item.get("id"): item for item in parsed if isinstance(item, dict) and item.get("id")}
            for role in batch:
                payload = by_id.get(role["canonical_url"]) or self._fallback_enrichment(role)
                raw_text_hash = role.get("raw_text_hash")
                if raw_text_hash:
                    self.cache.put(
                        "discover_llm_enrichment",
                        self._enrichment_cache_key(raw_text_hash),
                        payload,
                        source_hash=raw_text_hash,
                        prompt_version=DISCOVERY_PROMPT_VERSION,
                    )
                enriched.append({**role, **payload})
        return enriched

    def search_roles(self, user_id: int, criteria: DiscoverSearchCriteria) -> dict[str, Any]:
        self.ensure_available()
        normalized = criteria.normalized()
        if not normalized["search_intent"] and not normalized["role_families"]:
            raise ValueError("Add search intent or at least one role family.")

        search_key = self._query_cache_key(user_id, normalized)
        warnings: list[str] = []
        cached_entry = None if normalized["refresh"] else self.cache.peek("discover_query", search_key, include_expired=True)
        cached = cached_entry["payload"] if cached_entry and not cached_entry["is_expired"] else None
        stale_cached = cached_entry["payload"] if cached_entry else None
        if cached:
            roles = [
                role
                for role_id in cached.get("role_ids", [])[: normalized["page_size"]]
                if (role := get_discovered_role_for_user(user_id, int(role_id)))
            ]
            return {
                "search_key": search_key,
                "result_source": "cache",
                "warnings": cached.get("warnings", []),
                "roles": roles,
            }

        query_passes = self._build_query_passes(normalized)
        raw_hits: list[dict[str, Any]] = []
        try:
            for query, matched_title_variant in query_passes:
                hits = self.provider.search(query, settings.discover_max_results_per_variant)
                for hit in hits[: settings.discover_max_results_per_variant]:
                    raw_hits.append({**hit, "matched_title_variant": matched_title_variant})
                    if len({normalize_url(item["url"]) for item in raw_hits}) >= settings.discover_max_fetches_per_search:
                        break
                if len({normalize_url(item["url"]) for item in raw_hits}) >= settings.discover_max_fetches_per_search:
                    break
        except Exception as exc:
            if stale_cached:
                warnings.append("Showing previous results because live search failed.")
                roles = [
                    role
                    for role_id in stale_cached.get("role_ids", [])[: normalized["page_size"]]
                    if (role := get_discovered_role_for_user(user_id, int(role_id)))
                ]
                return {
                    "search_key": search_key,
                    "result_source": "stale_cache_fallback",
                    "warnings": warnings,
                    "roles": roles,
                }
            raise exc

        seen_urls: set[str] = set()
        structured: list[dict[str, Any]] = []
        for hit in raw_hits:
            normalized_url = normalize_url(hit["url"])
            if normalized_url in seen_urls:
                continue
            seen_urls.add(normalized_url)
            posting = self._structured_from_hit(hit, hit.get("matched_title_variant"))
            if not posting:
                continue
            posting["archetype"] = self._cheap_archetype(posting["raw_text"], posting.get("matched_title_variant"))
            structured.append(posting)
            if len(structured) >= settings.discover_max_fetches_per_search:
                break

        ranked = []
        for role in structured:
            score, matched_filters, blockers = self._rank_role(role, normalized)
            ranked.append(
                {
                    **role,
                    "rank_score": score,
                    "matched_filters": matched_filters,
                    "possible_blockers": blockers,
                }
            )
        ranked.sort(
            key=lambda role: (
                -float(role.get("rank_score") or 0.0),
                role.get("posted_at") is None,
                role.get("posted_at") or "",
                str(role.get("company") or "").lower(),
                str(role.get("job_title") or "").lower(),
            )
        )
        ranked = ranked[: settings.discover_max_survivors]
        enriched = self._enrich_roles(ranked)

        stored_roles = []
        for role in enriched:
            role_payload = {
                **role,
                "archetype": role.get("archetype") or self._cheap_archetype(role.get("raw_text") or "", role.get("matched_title_variant")),
                "short_tldr": str(role.get("tldr") or role.get("short_tldr") or "").strip(),
                "last_scraped_at": datetime.now(timezone.utc).isoformat(),
                "last_ranked_at": datetime.now(timezone.utc).isoformat(),
            }
            stored_roles.append(save_or_merge_discovered_role_for_user(user_id, role_payload))

        stored_roles.sort(
            key=lambda role: (
                0 if role.get("inbox_state") == "shortlisted" else 1,
                -float(role.get("rank_score") or 0.0),
                role.get("posted_at") is None,
                role.get("posted_at") or "",
                str(role.get("company") or "").lower(),
                str(role.get("job_title") or "").lower(),
            )
        )
        stored_roles = stored_roles[: normalized["page_size"]]

        self.cache.put(
            "discover_query",
            search_key,
            {"role_ids": [role["id"] for role in stored_roles], "warnings": warnings},
            expires_at=self._expiry(settings.discover_query_cache_hours),
        )
        return {
            "search_key": search_key,
            "result_source": "fresh_search",
            "warnings": warnings,
            "roles": stored_roles,
        }

    def list_roles(self, user_id: int, inbox_state: str = "active", search: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return list_discovered_roles_for_user(user_id, inbox_state=inbox_state, search=search, limit=limit)

    def get_role_detail(self, user_id: int, role_id: int) -> Optional[dict[str, Any]]:
        role = get_discovered_role_for_user(user_id, role_id)
        if not role:
            return None
        role["feedback_events"] = list_discovered_role_feedback_for_user(user_id, role_id, limit=5)
        return role

    def get_analytics(self, user_id: int) -> dict[str, Any]:
        conn = get_db_connection()
        try:
            role_counts = conn.execute(
                """
                SELECT
                    COUNT(*) AS total_roles,
                    SUM(CASE WHEN inbox_state = 'shortlisted' THEN 1 ELSE 0 END) AS shortlisted_roles,
                    SUM(CASE WHEN inbox_state = 'dismissed' THEN 1 ELSE 0 END) AS dismissed_roles,
                    SUM(CASE WHEN opened_in_tailor_at IS NOT NULL THEN 1 ELSE 0 END) AS opened_in_tailor_roles,
                    SUM(CASE WHEN opened_strategy_brief_id IS NOT NULL THEN 1 ELSE 0 END) AS strategy_linked_roles
                FROM discovered_roles
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
            feedback_rows = conn.execute(
                """
                SELECT decision, reasons_json, COUNT(*) AS count
                FROM discovered_role_feedback
                WHERE user_id = ?
                GROUP BY decision, reasons_json
                ORDER BY count DESC
                """,
                (user_id,),
            ).fetchall()
            decision_counts: dict[str, int] = {}
            reason_counts: dict[str, int] = {}
            for row in feedback_rows:
                decision_counts[row["decision"]] = decision_counts.get(row["decision"], 0) + int(row["count"] or 0)
                for reason in json.loads(row["reasons_json"] or "[]"):
                    reason_counts[reason] = reason_counts.get(reason, 0) + int(row["count"] or 0)

            feedback_total = sum(decision_counts.values())
            restored_count = decision_counts.get("restored", 0)
            dismissal_count = decision_counts.get("not_relevant", 0)
            restore_rate = round((restored_count / dismissal_count) * 100, 1) if dismissal_count else 0.0

            applications_count = conn.execute(
                """
                SELECT COUNT(*)
                FROM applications
                WHERE strategy_brief_id IN (
                    SELECT opened_strategy_brief_id
                    FROM discovered_roles
                    WHERE user_id = ? AND opened_strategy_brief_id IS NOT NULL
                )
                """,
                (user_id,),
            ).fetchone()[0]

            recent_cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
            repeated_reason_rows = conn.execute(
                """
                SELECT reasons_json, COUNT(*) AS count
                FROM discovered_role_feedback
                WHERE user_id = ? AND created_at >= ? AND decision = 'not_relevant'
                GROUP BY reasons_json
                ORDER BY count DESC
                """,
                (user_id, recent_cutoff),
            ).fetchall()
            repeated_reasons: list[dict[str, Any]] = []
            for row in repeated_reason_rows:
                reasons = json.loads(row["reasons_json"] or "[]")
                for reason in reasons:
                    repeated_reasons.append({"reason": reason, "count": int(row["count"] or 0)})
            repeated_reasons.sort(key=lambda item: (-item["count"], item["reason"]))

            return {
                "feedback_total": feedback_total,
                "decision_counts": decision_counts,
                "reason_counts": [{"reason": key, "count": value} for key, value in sorted(reason_counts.items(), key=lambda item: (-item[1], item[0]))],
                "repeated_reasons_last_90_days": repeated_reasons[:10],
                "restore_rate_percent": restore_rate,
                "funnel": {
                    "discovered_roles": int(role_counts["total_roles"] or 0),
                    "shortlisted_roles": int(role_counts["shortlisted_roles"] or 0),
                    "dismissed_roles": int(role_counts["dismissed_roles"] or 0),
                    "opened_in_tailor_roles": int(role_counts["opened_in_tailor_roles"] or 0),
                    "strategy_linked_roles": int(role_counts["strategy_linked_roles"] or 0),
                    "application_linked_roles": int(applications_count or 0),
                },
            }
        finally:
            conn.close()

    def get_suggestions(self, user_id: int) -> list[dict[str, Any]]:
        analytics = self.get_analytics(user_id)
        if analytics["feedback_total"] < 15:
            return []

        current_defaults = self.get_preferences(user_id).get("defaults", {})
        repeated = analytics.get("repeated_reasons_last_90_days", [])
        suggestions: list[dict[str, Any]] = []
        for item in repeated:
            if item["count"] < 3:
                continue
            reason = item["reason"]
            suggestion = None
            if reason == "too frontend-heavy" and "frontend" not in (current_defaults.get("avoid_keywords") or []):
                suggestion = {
                    "suggestion_key": "avoid_keyword:frontend",
                    "title": 'You often dismiss frontend-heavy roles. Add "frontend" to Avoid keywords?',
                    "kind": "avoid_keyword",
                    "payload": {"keyword": "frontend"},
                }
            elif reason == "wrong location or remote" and "remote" not in (current_defaults.get("remote_modes") or []):
                suggestion = {
                    "suggestion_key": "default_remote:remote",
                    "title": 'You often dismiss location mismatches. Set "remote" as a default remote mode?',
                    "kind": "remote_mode_default",
                    "payload": {"remote_mode": "remote"},
                }
            elif reason == "too managerial" and "manager" not in (current_defaults.get("avoid_keywords") or []):
                suggestion = {
                    "suggestion_key": "avoid_keyword:manager",
                    "title": 'You often dismiss managerial roles. Add "manager" to Avoid keywords?',
                    "kind": "avoid_keyword",
                    "payload": {"keyword": "manager"},
                }
            if not suggestion:
                continue
            latest_event = get_latest_discovery_suggestion_event_for_user(user_id, suggestion["suggestion_key"])
            if latest_event and latest_event.get("action") in {"accepted", "dismissed"}:
                continue
            suggestions.append({**suggestion, "evidence_count": item["count"]})
        return suggestions

    def act_on_suggestion(self, user_id: int, suggestion_key: str, action: str, payload: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        payload = payload or {}
        if action not in {"accepted", "dismissed"}:
            raise ValueError("Unsupported suggestion action")
        if action == "accepted":
            current = self.get_preferences(user_id).get("defaults", {})
            updated = dict(current)
            if suggestion_key.startswith("avoid_keyword:"):
                keyword = payload.get("keyword") or suggestion_key.split(":", 1)[1]
                updated["avoid_keywords"] = sorted({*(current.get("avoid_keywords") or []), keyword})
            elif suggestion_key.startswith("default_remote:"):
                remote_mode = payload.get("remote_mode") or suggestion_key.split(":", 1)[1]
                updated["remote_modes"] = sorted({*(current.get("remote_modes") or []), remote_mode})
            self.save_preferences(user_id, updated)
        event = record_discovery_suggestion_event_for_user(
            user_id,
            suggestion_key=suggestion_key,
            action=action,
            payload=payload,
        )
        return {"event": event, "preferences": self.get_preferences(user_id)}

    def shortlist_role(self, user_id: int, role_id: int, comment: str | None = None) -> dict[str, Any]:
        role = update_discovered_role_inbox_state_for_user(user_id, role_id, "shortlisted")
        record_discovered_role_feedback_for_user(user_id, role_id, "shortlisted", [], comment)
        return role

    def dismiss_role(self, user_id: int, role_id: int, reasons: list[str], comment: str | None = None) -> dict[str, Any]:
        role = update_discovered_role_inbox_state_for_user(user_id, role_id, "dismissed")
        record_discovered_role_feedback_for_user(user_id, role_id, "not_relevant", reasons, comment)
        return role

    def restore_role(self, user_id: int, role_id: int) -> dict[str, Any]:
        role = update_discovered_role_inbox_state_for_user(user_id, role_id, "discovered")
        record_discovered_role_feedback_for_user(user_id, role_id, "restored", [], None)
        return role

    def open_in_tailor(self, user_id: int, role_id: int) -> dict[str, Any]:
        role = get_discovered_role_for_user(user_id, role_id)
        if not role:
            raise KeyError("Role not found")
        if role.get("inbox_state") == "dismissed":
            raise PermissionError("Restore this role before opening it in Tailor.")
        updated = mark_discovered_role_opened_in_tailor_for_user(user_id, role_id)
        return {
            "discover_seed": {
                "discovered_role_id": updated["id"],
                "company": updated.get("company") or "",
                "job_title": updated.get("job_title") or "",
                "job_url": updated.get("apply_url") or updated.get("canonical_url"),
                "jd_text": updated.get("raw_text") or "",
                "posted_at": updated.get("posted_at"),
                "posted_label": updated.get("posted_label") or "Date unavailable",
                "source_domain": updated.get("source_domain") or urlparse(updated.get("canonical_url") or "").netloc.lower(),
                "apply_url": updated.get("apply_url"),
            }
        }

    def link_strategy_brief(self, user_id: int, role_id: int, strategy_brief_id: int) -> None:
        link_discovered_role_strategy_brief_for_user(user_id, role_id, strategy_brief_id)
