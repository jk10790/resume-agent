from __future__ import annotations

import re
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path

import yaml

from ...config import settings


SUPPORTED_ATS_PROVIDERS = {"greenhouse", "ashby", "lever", "smartrecruiters", "workday"}


class SourceCatalogError(RuntimeError):
    pass


@dataclass(frozen=True)
class TitleGuardrails:
    positive: list[str]
    negative: list[str]
    seniority_boost: list[str]


@dataclass(frozen=True)
class BodyGuardrails:
    """Regexes that disqualify a posting outright based on its body text.

    `exclude` covers requirements no amount of tailoring can satisfy — an active
    security clearance, or citizenship-only eligibility.

    `discipline_exclude` covers roles in the wrong discipline. These are kept
    separate because they are matched against the opening of the body only: a
    backend posting routinely mentions "frontend" somewhere far down, while a
    frontend posting says so in its first sentences.
    """

    exclude: list[str] = field(default_factory=list)
    compiled_exclude: list[re.Pattern] = field(default_factory=list)
    discipline_exclude: list[str] = field(default_factory=list)
    compiled_discipline_exclude: list[re.Pattern] = field(default_factory=list)

    @staticmethod
    def _compile(patterns: list[str], field_name: str) -> list[re.Pattern]:
        compiled = []
        for pattern in patterns:
            try:
                compiled.append(re.compile(pattern, re.IGNORECASE))
            except re.error as exc:
                raise SourceCatalogError(f"{field_name} has an invalid regex {pattern!r}: {exc}") from exc
        return compiled

    @classmethod
    def from_patterns(
        cls,
        patterns: list[str],
        discipline_patterns: list[str] | None = None,
    ) -> "BodyGuardrails":
        discipline_patterns = discipline_patterns or []
        return cls(
            exclude=patterns,
            compiled_exclude=cls._compile(patterns, "body_guardrails.exclude"),
            discipline_exclude=discipline_patterns,
            compiled_discipline_exclude=cls._compile(discipline_patterns, "body_guardrails.discipline_exclude"),
        )


@dataclass(frozen=True)
class TrackedCompany:
    name: str
    enabled: bool
    provider: str
    careers_url: str
    api_url: str
    tags: list[str]
    sponsorship_policy: str


@dataclass(frozen=True)
class SourceCatalog:
    version: int
    title_guardrails: TitleGuardrails
    body_guardrails: BodyGuardrails
    tracked_companies: list[TrackedCompany]
    catalog_hash: str
    source_path: str

    def enabled_companies(self) -> list[TrackedCompany]:
        return [company for company in self.tracked_companies if company.enabled]

    def supported_companies(self) -> list[TrackedCompany]:
        return [company for company in self.enabled_companies() if company.provider in SUPPORTED_ATS_PROVIDERS]


def _ensure_string_list(value: object, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise SourceCatalogError(f"{field_name} must be a list of non-empty strings.")
    return [item.strip() for item in value]


def _load_company(raw: object, index: int) -> TrackedCompany:
    if not isinstance(raw, dict):
        raise SourceCatalogError(f"tracked_companies[{index}] must be an object.")
    provider = str(raw.get("provider") or "").strip().lower()
    company = TrackedCompany(
        name=str(raw.get("name") or "").strip(),
        enabled=bool(raw.get("enabled", True)),
        provider=provider,
        careers_url=str(raw.get("careers_url") or "").strip(),
        api_url=str(raw.get("api_url") or "").strip(),
        tags=_ensure_string_list(raw.get("tags"), f"tracked_companies[{index}].tags"),
        sponsorship_policy=str(raw.get("sponsorship_policy") or "unknown").strip().lower() or "unknown",
    )
    if not company.name:
        raise SourceCatalogError(f"tracked_companies[{index}].name is required.")
    if not company.careers_url:
        raise SourceCatalogError(f"tracked_companies[{index}].careers_url is required.")
    if not company.provider:
        raise SourceCatalogError(f"tracked_companies[{index}].provider is required.")
    if not company.api_url:
        raise SourceCatalogError(f"tracked_companies[{index}].api_url is required.")
    return company


def load_source_catalog(path: str | None = None) -> SourceCatalog:
    catalog_path = Path(path or settings.resolved_discover_source_config_path)
    if not catalog_path.exists():
        raise SourceCatalogError(f"Discovery source config not found: {catalog_path}")

    raw_text = catalog_path.read_text(encoding="utf-8")
    try:
        payload = yaml.safe_load(raw_text) or {}
    except Exception as exc:
        raise SourceCatalogError(f"Discovery source config is invalid YAML: {exc}") from exc

    if not isinstance(payload, dict):
        raise SourceCatalogError("Discovery source config must be a mapping.")

    version = payload.get("version")
    if not isinstance(version, int):
        raise SourceCatalogError("Discovery source config must define an integer version.")

    raw_guardrails = payload.get("title_guardrails")
    if not isinstance(raw_guardrails, dict):
        raise SourceCatalogError("title_guardrails must be an object.")
    title_guardrails = TitleGuardrails(
        positive=_ensure_string_list(raw_guardrails.get("positive"), "title_guardrails.positive"),
        negative=_ensure_string_list(raw_guardrails.get("negative"), "title_guardrails.negative"),
        seniority_boost=_ensure_string_list(raw_guardrails.get("seniority_boost"), "title_guardrails.seniority_boost"),
    )

    raw_body = payload.get("body_guardrails") or {}
    if not isinstance(raw_body, dict):
        raise SourceCatalogError("body_guardrails must be an object.")
    body_guardrails = BodyGuardrails.from_patterns(
        _ensure_string_list(raw_body.get("exclude"), "body_guardrails.exclude"),
        _ensure_string_list(raw_body.get("discipline_exclude"), "body_guardrails.discipline_exclude"),
    )

    raw_companies = payload.get("tracked_companies")
    if not isinstance(raw_companies, list):
        raise SourceCatalogError("tracked_companies must be a list.")
    companies = [_load_company(item, index) for index, item in enumerate(raw_companies)]

    catalog = SourceCatalog(
        version=version,
        title_guardrails=title_guardrails,
        body_guardrails=body_guardrails,
        tracked_companies=companies,
        catalog_hash=sha256(raw_text.encode("utf-8")).hexdigest(),
        source_path=str(catalog_path),
    )
    if not catalog.supported_companies():
        raise SourceCatalogError("Discovery source config has no enabled supported ATS companies.")
    return catalog
