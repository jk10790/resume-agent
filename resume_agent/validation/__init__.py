"""Validating a tailored resume against its source.

Two modes, chosen by `TAILORING_VALIDATION_MODE`:

- `rules` (default) runs the deterministic checks in `rules.py`. No LLM calls.
- `full` adds an LLM review pass on top, for the cases where a judgement about
  tone or coverage is genuinely wanted.
- `off` skips validation.

The previous arrangement had only an all-or-nothing LLM path costing four calls,
plus four more if a fix triggered re-validation, so in practice it was left
disabled -- which meant no validation at all. The rules path is the one that
catches fabricated numbers and unsupported skills, and it is free, so it runs
every time.
"""

from __future__ import annotations

from typing import Any, List, Optional

from ..config import settings
from ..models.agent_models import ResumeValidation, ValidationIssue
from ..utils.logger import logger
from . import rules

__all__ = ["validate_tailored_resume", "rules"]


def validate_tailored_resume(
    *,
    original_resume: str,
    tailored_resume: str,
    jd_text: str = "",
    analyzed_jd: Optional[Any] = None,
    profile_context: Optional[Any] = None,
    llm_service: Optional[Any] = None,
    mode: Optional[str] = None,
) -> ResumeValidation:
    """Check a tailored resume and report what is wrong with it."""
    mode = (mode or settings.tailoring_validation_mode or "rules").strip().lower()
    if mode == "off":
        return ResumeValidation(quality_score=100, is_valid=True, issues=[])

    confirmed_skills = list(getattr(profile_context, "confirmed_skills", None) or [])
    verified_metrics = getattr(profile_context, "confirmed_metric_records", None)
    jd_skills = list(getattr(analyzed_jd, "required_skills", None) or []) + list(
        getattr(analyzed_jd, "preferred_skills", None) or []
    )

    issues: List[ValidationIssue] = []
    issues.extend(rules.check_structure(tailored_resume))
    issues.extend(rules.check_jd_leakage(tailored_resume, jd_text))
    issues.extend(rules.check_experience_years(original_resume, tailored_resume))

    metric_issues, provenance = rules.check_metric_provenance(
        original_resume,
        tailored_resume,
        verified_metric_records=verified_metrics,
    )
    issues.extend(metric_issues)

    # Worded scale claims are the same defect without digits, so they join the
    # flagged list the grounding pass acts on.
    scale_issues, scale_phrases = rules.check_scale_claims(original_resume, tailored_resume)
    issues.extend(scale_issues)
    if scale_phrases:
        provenance["flagged"] = sorted(set(provenance.get("flagged", []) + scale_phrases))

    issues.extend(
        rules.check_skill_authenticity(
            original_resume,
            tailored_resume,
            jd_skills=jd_skills,
            confirmed_skills=confirmed_skills,
        )
    )

    issues.extend(rules.check_voice_drift(original_resume, tailored_resume))

    if mode == "full" and llm_service is not None:
        issues.extend(_llm_review(llm_service, original_resume, tailored_resume, jd_text))

    word_count = len((tailored_resume or "").split())
    return ResumeValidation(
        quality_score=rules.quality_score(issues),
        is_valid=not rules.has_errors(issues),
        issues=issues,
        recommendations=[issue.suggestion for issue in issues if issue.suggestion],
        metric_provenance=provenance,
        length_check={
            "word_count": word_count,
            "char_count": len(tailored_resume or ""),
            "is_reasonable": settings.resume_recommended_min_words
            <= word_count
            <= settings.resume_recommended_max_words,
            "recommended_range": (
                f"{settings.resume_recommended_min_words}-{settings.resume_recommended_max_words} words"
            ),
        },
    )


def _llm_review(
    llm_service: Any, original_resume: str, tailored_resume: str, jd_text: str
) -> List[ValidationIssue]:
    """One escalation call, not four. Failures degrade to the rules result."""
    from ..models.agent_models import Severity

    try:
        data = llm_service.run_task(
            "resume.review",
            original_resume=original_resume[:4000],
            tailored_resume=tailored_resume[:6000],
            jd_excerpt=(jd_text or "")[:2500],
        )
    except Exception as e:
        logger.warning(f"LLM validation pass failed; keeping rule findings only: {e}")
        return []

    findings = data.get("issues") if isinstance(data, dict) else None
    if not isinstance(findings, list):
        return []

    issues: List[ValidationIssue] = []
    for finding in findings[:10]:
        if not isinstance(finding, dict) or not finding.get("message"):
            continue
        severity = str(finding.get("severity", "warning")).lower()
        if severity not in ("error", "warning", "info"):
            severity = "warning"
        issues.append(
            ValidationIssue(
                severity=Severity(severity),
                category=str(finding.get("category", "content")),
                message=str(finding["message"]),
                suggestion=finding.get("suggestion"),
            )
        )
    return issues
