"""Compose the new multi-surface review bundle from existing workflow artifacts."""

from __future__ import annotations

from typing import Iterable, Optional

from ..models.agent_models import (
    ReviewBundle,
    ReviewIssue,
    ReviewOverall,
    ReviewSection,
    ResumeValidation,
    Severity,
)
from ..review.ats_parse import review_ats_parse


def _to_review_issue(issue, evidence: str | None = None) -> ReviewIssue:
    severity = issue.severity
    if isinstance(severity, str):
        severity = Severity(severity)
    return ReviewIssue(
        severity=severity,
        category=issue.category,
        message=issue.message,
        suggestion=issue.suggestion,
        evidence=evidence,
    )


def _issues_by_predicate(validation: Optional[ResumeValidation], predicate) -> list[ReviewIssue]:
    if not validation:
        return []
    return [_to_review_issue(issue) for issue in validation.issues if predicate(issue)]


def _recommendations_from_issues(issues: Iterable[ReviewIssue]) -> list[str]:
    recommendations: list[str] = []
    for issue in issues:
        if issue.suggestion and issue.suggestion not in recommendations:
            recommendations.append(issue.suggestion)
    return recommendations


def _build_authenticity_section(validation: Optional[ResumeValidation]) -> ReviewSection:
    authenticity_issues = _issues_by_predicate(
        validation,
        lambda issue: issue.category in {"consistency", "review"} or "fabricated" in issue.message.lower() or "unverified metric" in issue.message.lower(),
    )
    metric_provenance = validation.metric_provenance if validation else {}
    flagged_metrics = (metric_provenance or {}).get("flagged_details", []) or []
    for metric in flagged_metrics:
        authenticity_issues.append(
            ReviewIssue(
                severity=Severity.ERROR,
                category="metric_provenance",
                message=f'Unverified metric: "{metric.get("raw", "")}"',
                suggestion="Remove or verify this metric before using it in the tailored resume.",
                evidence=metric.get("line"),
            )
        )

    error_count = sum(1 for issue in authenticity_issues if issue.severity == Severity.ERROR)
    warning_count = sum(1 for issue in authenticity_issues if issue.severity == Severity.WARNING)
    score = max(0, 100 - (error_count * 22) - (warning_count * 10))
    if error_count:
        verdict = "fail"
        summary = "Potential fabrication or unsupported claims were detected."
    elif warning_count:
        verdict = "warn"
        summary = "Resume is mostly grounded, but some claims need confirmation."
    else:
        verdict = "pass"
        summary = "Claims appear consistent with the source resume and confirmed profile."

    return ReviewSection(
        score=score,
        verdict=verdict,
        summary=summary,
        issues=authenticity_issues,
        recommendations=_recommendations_from_issues(authenticity_issues),
        metrics={
            "issue_count": len(authenticity_issues),
            "flagged_metrics": len(flagged_metrics),
        },
    )


def _build_job_match_section(ats_score, fit_evaluation, analyzed_jd) -> ReviewSection:
    score = ats_score.score if ats_score else max(0, min(100, int((fit_evaluation.score if fit_evaluation else 0) * 10)))
    issues: list[ReviewIssue] = []

    missing_keywords = list(ats_score.missing_keywords) if ats_score else []
    for keyword in missing_keywords[:12]:
        issues.append(
            ReviewIssue(
                severity=Severity.WARNING,
                category="missing_keyword",
                message=f"Missing JD keyword: {keyword}",
                suggestion="Add this only if you have real experience to support it.",
            )
        )

    if fit_evaluation:
        for area in fit_evaluation.missing_areas[:8]:
            issues.append(
                ReviewIssue(
                    severity=Severity.WARNING,
                    category="fit_gap",
                    message=area,
                    suggestion="Address this gap with stronger evidence where truthful, or treat the role as a weaker fit.",
                )
            )

    if score >= 75:
        verdict = "strong"
        summary = "Resume is well aligned to this job description."
    elif score >= 55:
        verdict = "moderate"
        summary = "Resume has partial alignment but still has coverage gaps."
    else:
        verdict = "weak"
        summary = "Resume is not yet well aligned to this job description."

    recommendations = []
    if ats_score:
        recommendations.extend(ats_score.recommendations[:5])
    if fit_evaluation:
        for recommendation in fit_evaluation.recommendations:
            if recommendation not in recommendations:
                recommendations.append(recommendation)

    required_skills = analyzed_jd.required_skills if analyzed_jd else []
    preferred_skills = analyzed_jd.preferred_skills if analyzed_jd else []

    return ReviewSection(
        score=score,
        verdict=verdict,
        summary=summary,
        issues=issues,
        recommendations=recommendations,
        metrics={
            "keyword_density": getattr(ats_score, "keyword_density", 0.0),
            "matched_keywords": sorted(list(getattr(ats_score, "keyword_matches", {}).keys())),
            "missing_keywords": missing_keywords,
            "matching_areas": fit_evaluation.matching_areas if fit_evaluation else [],
            "missing_areas": fit_evaluation.missing_areas if fit_evaluation else [],
            "required_skills": required_skills,
            "preferred_skills": preferred_skills,
        },
    )


def _build_editorial_section(validation: Optional[ResumeValidation]) -> ReviewSection:
    editorial_issues = _issues_by_predicate(
        validation,
        lambda issue: issue.category not in {"consistency", "review"} and "fabricated" not in issue.message.lower() and "unverified metric" not in issue.message.lower(),
    )
    base_score = validation.quality_score if validation else 70
    penalty = sum(12 if issue.severity == Severity.ERROR else 5 for issue in editorial_issues)
    score = max(0, min(100, base_score - penalty))
    if score >= 80:
        verdict = "strong"
        summary = "Resume reads clearly and presents experience with solid signal."
    elif score >= 65:
        verdict = "moderate"
        summary = "Resume is readable but can be tightened for stronger recruiter impact."
    else:
        verdict = "weak"
        summary = "Resume needs content and clarity improvements before it is presentation-ready."

    recommendations = list(validation.recommendations) if validation else []
    for recommendation in _recommendations_from_issues(editorial_issues):
        if recommendation not in recommendations:
            recommendations.append(recommendation)

    return ReviewSection(
        score=score,
        verdict=verdict,
        summary=summary,
        issues=editorial_issues,
        recommendations=recommendations,
        metrics={
            "issue_count": len(editorial_issues),
        },
    )


def build_review_bundle(
    tailored_resume: str,
    validation: Optional[ResumeValidation] = None,
    ats_score=None,
    fit_evaluation=None,
    analyzed_jd=None,
) -> ReviewBundle:
    """Build a structured bundle that separates review semantics."""
    ats_parse = review_ats_parse(tailored_resume)
    authenticity = _build_authenticity_section(validation)
    job_match = _build_job_match_section(ats_score, fit_evaluation, analyzed_jd)
    editorial = _build_editorial_section(validation)

    overall_score = int(round((authenticity.score * 0.35) + (ats_parse.score * 0.2) + (job_match.score * 0.25) + (editorial.score * 0.2)))
    if authenticity.verdict == "fail":
        overall_verdict = "needs_edits"
        recommendation = "Needs edits before submitting"
        summary = "Fix authenticity issues before trusting any other score."
    elif job_match.verdict == "weak":
        overall_verdict = "borderline"
        recommendation = "Needs edits before submitting"
        summary = "The resume is structurally usable, but alignment to this role is still weak."
    elif ats_parse.verdict == "fail":
        overall_verdict = "needs_edits"
        recommendation = "Needs edits before submitting"
        summary = "The content may be viable, but ATS formatting risk is still too high."
    elif overall_score >= 80:
        overall_verdict = "ready"
        recommendation = "Safe to submit"
        summary = "This version is broadly ready, with no critical blockers across the review surfaces."
    else:
        overall_verdict = "needs_edits"
        recommendation = "Needs edits before submitting"
        summary = "The resume is close, but there are still meaningful gaps to address."

    return ReviewBundle(
        authenticity=authenticity,
        ats_parse=ats_parse,
        job_match=job_match,
        editorial=editorial,
        overall=ReviewOverall(
            score=overall_score,
            verdict=overall_verdict,
            summary=summary,
            recommendation=recommendation,
        ),
    )
