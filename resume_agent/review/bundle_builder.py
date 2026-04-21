"""Compose the new multi-surface review bundle from existing workflow artifacts."""

from __future__ import annotations

import re
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


def _directive_tokens(directive) -> list[str]:
    raw_parts = [
        token.strip().lower()
        for token in re.split(r"[,;/]| and ", f"{directive.action} {directive.rationale}")
        if token.strip() and len(token.strip()) > 4
    ]
    expanded: list[str] = []
    for part in raw_parts:
        expanded.append(part)
        expanded.extend(
            word
            for word in re.split(r"[^a-z0-9+#.]+", part)
            if len(word.strip()) > 4
        )
    deduped: list[str] = []
    seen: set[str] = set()
    for token in expanded:
        if token not in seen:
            deduped.append(token)
            seen.add(token)
    return deduped[:8]


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


def _build_strategy_alignment_section(tailored_resume: str, strategy_brief) -> ReviewSection:
    if not strategy_brief:
        return ReviewSection(
            score=80,
            verdict="informational",
            summary="No strategy brief was available, so alignment could not be fully checked.",
            issues=[],
            recommendations=[],
            metrics={"directive_count": 0, "matched_directives": 0},
        )

    resume_text = (tailored_resume or "").lower()
    all_directives = list(getattr(strategy_brief, "tailoring_directives", []) or [])
    directives = [directive for directive in all_directives if getattr(directive, "enabled", True)]
    issues: list[ReviewIssue] = []
    matched = 0
    directive_trace: list[dict] = []
    disabled_directives = 0
    for directive in all_directives:
        if not getattr(directive, "enabled", True):
            disabled_directives += 1
            directive_trace.append(
                {
                    "id": getattr(directive, "id", ""),
                    "section": getattr(directive, "section", ""),
                    "action": getattr(directive, "action", ""),
                    "status": "skipped_disabled",
                    "reason": "Disabled during strategy review.",
                }
            )

    for directive in directives:
        tokens = _directive_tokens(directive)
        matched_tokens = [token for token in tokens if token in resume_text]
        if matched_tokens:
            matched += 1
            directive_trace.append(
                {
                    "id": directive.id,
                    "section": directive.section,
                    "action": directive.action,
                    "status": "applied",
                    "reason": f'Observed in draft via terms like "{matched_tokens[0]}".',
                }
            )
        else:
            issues.append(
                ReviewIssue(
                    severity=Severity.WARNING,
                    category="strategy_alignment",
                    message=f'Strategy directive may be underrepresented: "{directive.action}"',
                    suggestion="Review whether the final draft actually reflects this approved strategy move.",
                    evidence=directive.section,
                )
            )
            directive_trace.append(
                {
                    "id": directive.id,
                    "section": directive.section,
                    "action": directive.action,
                    "status": "underrepresented",
                    "reason": "Approved directive is not clearly reflected in the current draft.",
                }
            )

    total = len(directives)
    score = 100 if total == 0 else max(0, int(round((matched / max(total, 1)) * 100)))
    if score >= 80:
        verdict = "strong"
        summary = "The final draft appears aligned with the approved strategy."
    elif score >= 60:
        verdict = "moderate"
        summary = "The draft reflects some approved strategy, but not consistently."
    else:
        verdict = "weak"
        summary = "Several approved strategy moves are not clearly reflected in the draft."

    return ReviewSection(
        score=score,
        verdict=verdict,
        summary=summary,
        issues=issues,
        recommendations=_recommendations_from_issues(issues),
        metrics={
            "directive_count": total,
            "matched_directives": matched,
            "disabled_directives": disabled_directives,
            "directive_trace": directive_trace,
        },
    )


def _build_overall_top_wins(authenticity: ReviewSection, ats_parse: ReviewSection, job_match: ReviewSection, strategy_alignment: ReviewSection, editorial: ReviewSection) -> list[str]:
    wins: list[str] = []
    if authenticity.verdict == "pass":
        wins.append("Claims appear grounded in the source resume and confirmed profile.")
    if job_match.score >= 75:
        wins.append("Role alignment is strong enough to support this version.")
    if ats_parse.score >= 80:
        wins.append("ATS structure looks solid for parsing.")
    if strategy_alignment.score >= 80:
        wins.append("The draft reflects the approved strategy clearly.")
    if editorial.score >= 80:
        wins.append("The draft reads cleanly and presents experience with strong signal.")
    return wins[:3]


def _build_overall_top_risks(authenticity: ReviewSection, ats_parse: ReviewSection, job_match: ReviewSection, strategy_alignment: ReviewSection) -> list[str]:
    risks: list[str] = []
    if authenticity.issues:
        risks.append(authenticity.issues[0].message)
    if job_match.verdict == "weak" and job_match.issues:
        risks.append(job_match.issues[0].message)
    if ats_parse.verdict == "fail" and ats_parse.issues:
        risks.append(ats_parse.issues[0].message)
    if strategy_alignment.issues:
        risks.append(strategy_alignment.issues[0].message)
    return risks[:3]


def _build_readiness_checks(authenticity: ReviewSection, ats_parse: ReviewSection, strategy_alignment: ReviewSection) -> list[str]:
    checks: list[str] = []
    if authenticity.issues:
        checks.append("Re-check every risky claim or metric before sending.")
    if ats_parse.verdict == "fail":
        checks.append("Clean up ATS formatting hazards before exporting or submitting.")
    if strategy_alignment.issues:
        checks.append("Decide whether to refine the draft so the approved strategy is more visible.")
    if not checks:
        checks.append("No critical blockers remain. Final pass should just be a human sanity check.")
    return checks[:3]


def build_review_bundle(
    tailored_resume: str,
    validation: Optional[ResumeValidation] = None,
    ats_score=None,
    fit_evaluation=None,
    analyzed_jd=None,
    strategy_brief=None,
) -> ReviewBundle:
    """Build a structured bundle that separates review semantics."""
    ats_parse = review_ats_parse(tailored_resume)
    authenticity = _build_authenticity_section(validation)
    job_match = _build_job_match_section(ats_score, fit_evaluation, analyzed_jd)
    strategy_alignment = _build_strategy_alignment_section(tailored_resume, strategy_brief)
    editorial = _build_editorial_section(validation)

    overall_score = int(round((authenticity.score * 0.3) + (ats_parse.score * 0.18) + (job_match.score * 0.22) + (strategy_alignment.score * 0.12) + (editorial.score * 0.18)))
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

    top_wins = _build_overall_top_wins(authenticity, ats_parse, job_match, strategy_alignment, editorial)
    top_risks = _build_overall_top_risks(authenticity, ats_parse, job_match, strategy_alignment)
    readiness_checks = _build_readiness_checks(authenticity, ats_parse, strategy_alignment)

    return ReviewBundle(
        authenticity=authenticity,
        ats_parse=ats_parse,
        job_match=job_match,
        strategy_alignment=strategy_alignment,
        editorial=editorial,
        overall=ReviewOverall(
            score=overall_score,
            verdict=overall_verdict,
            summary=summary,
            recommendation=recommendation,
            top_wins=top_wins,
            top_risks=top_risks,
            readiness_checks=readiness_checks,
        ),
    )
