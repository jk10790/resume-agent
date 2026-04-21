"""Helpers for job archetype detection, target-role weighting, and blocker coding."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from ..models.resume import FitEvaluation


ARCHETYPE_LABELS: Dict[str, str] = {
    "software_engineering": "Software Engineering",
    "platform_infrastructure": "Platform / Infrastructure / SRE",
    "data_ml_ai": "Data / ML / AI Engineering",
    "applied_ai_llmops": "Applied AI / LLMOps / Agentic Systems",
    "product_technical_product": "Product / Technical Product",
    "solutions_customer_engineering": "Solutions / Customer / Sales Engineering",
}

ARCHETYPE_ALIASES: Dict[str, str] = {
    "software_engineering": "software_engineering",
    "software / product engineering": "software_engineering",
    "platform_infrastructure": "platform_infrastructure",
    "platform / infrastructure / sre": "platform_infrastructure",
    "data_ml_ai": "data_ml_ai",
    "data / ml / ai engineering": "data_ml_ai",
    "applied_ai_llmops": "applied_ai_llmops",
    "applied_ai_llmops_agentic_systems": "applied_ai_llmops",
    "applied ai / llmops / agentic systems": "applied_ai_llmops",
    "product_technical_product": "product_technical_product",
    "product / technical product": "product_technical_product",
    "solutions_customer_engineering": "solutions_customer_engineering",
    "solutions / customer / sales engineering": "solutions_customer_engineering",
}

TARGET_ALIGNMENT_WEIGHTS: Dict[str, int] = {
    "primary": 1,
    "secondary": 0,
    "adjacent": -1,
    "unranked": 0,
}


def normalize_archetype_name(value: Optional[str]) -> str:
    raw = " ".join(str(value or "").strip().lower().replace("-", "_").split())
    raw = raw.replace("/", " / ")
    raw = " ".join(raw.split())
    compact = raw.replace(" ", "")
    if compact in ARCHETYPE_ALIASES:
        return ARCHETYPE_ALIASES[compact]
    return ARCHETYPE_ALIASES.get(raw, raw.replace(" ", "_") if raw else "software_engineering")


def archetype_label(value: Optional[str]) -> str:
    return ARCHETYPE_LABELS.get(normalize_archetype_name(value), "General")


def detect_job_archetype(analyzed_jd: Any) -> str:
    title_blob = " ".join(
        [
            getattr(analyzed_jd, "job_title", "") or "",
            getattr(analyzed_jd, "summary", "") or "",
            " ".join(getattr(analyzed_jd, "technologies_needed", [])[:8]),
            " ".join(getattr(analyzed_jd, "required_skills", [])[:10]),
        ]
    ).lower()
    if any(token in title_blob for token in ("llm", "agent", "prompt", "rag", "eval", "orchestration", "hitl")):
        return "applied_ai_llmops"
    if any(token in title_blob for token in ("platform", "infrastructure", "sre", "terraform", "kubernetes", "observability")):
        return "platform_infrastructure"
    if any(token in title_blob for token in ("data", "ml", "machine learning", "analytics", "airflow", "spark")):
        return "data_ml_ai"
    if any(token in title_blob for token in ("product manager", "technical product", "pm", "roadmap", "prd")):
        return "product_technical_product"
    if any(token in title_blob for token in ("solutions", "sales engineer", "customer engineer", "solutions architect", "client-facing")):
        return "solutions_customer_engineering"
    return "software_engineering"


def normalize_target_archetype_preferences(records: Iterable[Dict[str, Any]] | None) -> List[Dict[str, str]]:
    normalized: List[Dict[str, str]] = []
    seen: set[str] = set()
    for record in records or []:
        archetype = normalize_archetype_name(record.get("archetype"))
        tier = str(record.get("tier") or "").strip().lower()
        if archetype in seen or tier not in {"primary", "secondary", "adjacent"}:
            continue
        normalized.append({"archetype": archetype, "tier": tier})
        seen.add(archetype)
    return normalized


def get_target_alignment(archetype: Optional[str], preferences: Iterable[Dict[str, Any]] | None) -> str:
    normalized_archetype = normalize_archetype_name(archetype)
    for record in normalize_target_archetype_preferences(preferences):
        if record["archetype"] == normalized_archetype:
            return record["tier"]
    return "unranked"


def apply_target_alignment(
    fit_evaluation: FitEvaluation,
    *,
    archetype: str,
    preferences: Iterable[Dict[str, Any]] | None,
) -> tuple[FitEvaluation, str]:
    tier = get_target_alignment(archetype, preferences)
    delta = TARGET_ALIGNMENT_WEIGHTS.get(tier, 0)
    adjusted_score = max(1, min(10, fit_evaluation.score + delta))
    adjusted_should_apply = fit_evaluation.should_apply
    adjusted_confidence = fit_evaluation.confidence
    recommendations = list(fit_evaluation.recommendations or [])

    if tier == "primary":
        recommendations.insert(0, f"This role aligns with your primary target archetype: {archetype_label(archetype)}.")
        adjusted_confidence = min(1.0, adjusted_confidence + 0.05)
    elif tier == "secondary":
        recommendations.insert(0, f"This role aligns with a secondary target archetype: {archetype_label(archetype)}.")
    elif tier == "adjacent":
        recommendations.insert(0, f"This role sits in an adjacent target archetype: {archetype_label(archetype)}. Treat it as a stretch path.")
        adjusted_confidence = max(0.0, adjusted_confidence - 0.05)
        if adjusted_score <= 4:
            adjusted_should_apply = False
    elif preferences:
        recommendations.insert(0, f"This role is outside your saved target archetypes. Confirm it is worth pursuing before spending time on tailoring.")

    adjusted = fit_evaluation.model_copy(
        update={
            "score": adjusted_score,
            "should_apply": adjusted_should_apply,
            "confidence": adjusted_confidence,
            "recommendations": recommendations[:8],
        }
    )
    return adjusted, tier


def infer_gap_reason_code(requirement: Optional[str], mitigation: Optional[str] = None) -> Optional[str]:
    blob = " ".join(part for part in [requirement or "", mitigation or ""] if part).lower()
    if not blob:
        return None
    if any(token in blob for token in ("us only", "us-only", "canada only", "canada-only", "residency", "visa", "location", "country", "timezone")):
        return "geo_restriction"
    if any(token in blob for token in ("hybrid", "onsite", "on-site", "office", "relocate", "relocation")):
        return "onsite_requirement"
    if any(token in blob for token in ("staff", "principal", "director", "manager", "lead", "seniority", "scope")):
        return "seniority_mismatch"
    if any(token in blob for token in ("people management", "direct reports", "hiring", "managerial")):
        return "people_management_gap"
    if any(token in blob for token in ("degree", "phd", "masters", "bachelor", "education")):
        return "education_requirement"
    if any(token in blob for token in ("clearance", "citizenship", "security clearance")):
        return "clearance_requirement"
    if any(token in blob for token in ("domain", "industry", "healthcare", "fintech", "payments", "regulated")):
        return "domain_mismatch"
    tech_tokens = (
        "python", "java", "go", "rust", "typescript", "javascript", "react", "aws", "gcp",
        "azure", "docker", "kubernetes", "terraform", "spark", "airflow", "postgres", "sql",
    )
    if any(token in blob for token in tech_tokens):
        return "stack_mismatch"
    return None
