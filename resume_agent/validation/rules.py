"""Deterministic checks on a tailored resume.

Every rule here is exact: it compares the draft against the source text and the
user's confirmed facts, and it costs nothing to run. That matters because the
failures worth catching -- a number that was not in the resume, a technology the
candidate never claimed, an inflated year count -- are all questions of fact,
and asking a language model to check another language model's arithmetic is both
slower and less reliable than comparing the two strings.

The LLM checks that used to run by default (four calls, plus four more on
re-validation) are now an explicit escalation. See `validation/__init__.py`.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from ..config import settings
from ..models.agent_models import Severity, ValidationIssue
from ..utils.metrics import (
    MetricMatch,
    extract_metrics,
    extract_metrics_from_memory,
    normalize_metric_set,
)

_YEARS_OF_EXPERIENCE = re.compile(r"(\d+)\s*(?:years?|yrs?)\s+of\s+experience", re.IGNORECASE)
# Tolerates the markdown heading the tailoring prompt mandates ("# Jane Doe"),
# which the bare-name pattern missed -- so this warning fired on every draft.
_HEADER = re.compile(r"^\s*#{0,3}\s*\*{0,2}[A-Z][a-zA-Z.'-]+\s+[A-Z]")

# Words that describe what someone does rather than name a tool. A phrase built
# only from these is a capability, and no resume can "fabricate" one the way it
# can fabricate a technology.
_CAPABILITY_WORDS = frozenset("""
architecture collaboration communication experience development design systems
system management leadership practices understanding service services skills
engineering software technical cross functional team teams working ability
knowledge strong excellent proven demonstrated years senior staff principal
problem solving analysis analytical building scalable distributed modern
""".split())


def check_structure(tailored_resume: str) -> List[ValidationIssue]:
    """Shape checks: non-empty, plausible header, sane length."""
    issues: List[ValidationIssue] = []

    if not tailored_resume or len(tailored_resume.strip()) < 100:
        issues.append(
            ValidationIssue(
                severity=Severity.ERROR,
                category="format",
                message="Resume is too short or empty",
                suggestion="Ensure resume has substantial content",
            )
        )
        return issues

    if not _HEADER.search(tailored_resume[:200]):
        issues.append(
            ValidationIssue(
                severity=Severity.WARNING,
                category="format",
                message="Resume may be missing proper header (name, contact)",
                suggestion="Ensure resume starts with your name and contact information",
            )
        )

    word_count = len(tailored_resume.split())
    recommended = f"{settings.resume_recommended_min_words}-{settings.resume_recommended_max_words} words"
    if word_count < settings.resume_min_words:
        issues.append(
            ValidationIssue(
                severity=Severity.WARNING,
                category="format",
                message=f"Resume is quite short ({word_count} words)",
                suggestion=f"Consider adding more detail to experiences (recommended: {recommended})",
            )
        )
    elif word_count > settings.resume_max_words:
        issues.append(
            ValidationIssue(
                severity=Severity.WARNING,
                category="format",
                message=f"Resume is quite long ({word_count} words)",
                suggestion=f"Consider condensing to 1-2 pages worth of content (recommended: {recommended})",
            )
        )

    return issues


def check_jd_leakage(tailored_resume: str, jd_text: str) -> List[ValidationIssue]:
    """Catch job-description text copied into the resume."""
    if len(jd_text or "") <= 100:
        return []
    if jd_text[:200].lower() not in (tailored_resume or "").lower():
        return []
    return [
        ValidationIssue(
            severity=Severity.ERROR,
            category="content",
            message="Job description content detected in resume",
            suggestion="Remove any JD text that was accidentally included",
        )
    ]


def check_experience_years(original_resume: str, tailored_resume: str) -> List[ValidationIssue]:
    """Flag a years-of-experience figure that the source resume does not state."""
    if not original_resume:
        return []
    original = set(_YEARS_OF_EXPERIENCE.findall(original_resume))
    issues = []
    for years in _YEARS_OF_EXPERIENCE.findall(tailored_resume or ""):
        if years not in original:
            issues.append(
                ValidationIssue(
                    severity=Severity.ERROR,
                    category="consistency",
                    message=(
                        f"Fabricated experience: '{years} years of experience' was added "
                        "but is not in the original resume"
                    ),
                    suggestion=(
                        "Remove the added years. Use only experience levels stated in the "
                        "original resume."
                    ),
                )
            )
    return issues


def check_metric_provenance(
    original_resume: str,
    tailored_resume: str,
    *,
    user_metric_text: Optional[str] = None,
    verified_metric_records: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[List[ValidationIssue], Dict[str, Any]]:
    """Every number in the draft must trace to the resume or a confirmed metric.

    This is the check that catches an invented "reduced latency by 35%" no matter
    which prompt produced it, which is why it runs by default and for free.
    """
    from ..storage.user_memory import get_verified_metrics, load_memory

    original_metrics = extract_metrics(original_resume)

    if user_metric_text:
        user_metrics = extract_metrics(user_metric_text)
    else:
        records = verified_metric_records if verified_metric_records is not None else get_verified_metrics()
        if records:
            user_metrics = [
                MetricMatch(
                    raw=str(record.get("raw", "")),
                    normalized=str(record.get("normalized", "")),
                    line=str(record.get("line", "")),
                    category=str(record.get("category", "number")),
                )
                for record in records
            ]
        else:
            user_metrics = extract_metrics_from_memory(load_memory())

    allowed = normalize_metric_set(original_metrics + user_metrics)
    tailored_metrics = extract_metrics(tailored_resume)
    unverified = [metric for metric in tailored_metrics if metric.normalized not in allowed]

    issues = [
        ValidationIssue(
            severity=Severity.ERROR,
            category="consistency",
            message=f'Unverified metric: "{metric.raw}" is not in the original resume or user-provided metrics',
            suggestion="Remove the numeric claim or rewrite without numbers unless you can confirm it.",
        )
        for metric in unverified[:10]
    ]

    provenance = {
        "allowed": sorted({m.raw for m in allowed.values()}),
        "tailored": sorted({m.raw for m in normalize_metric_set(tailored_metrics).values()}),
        "flagged": sorted({m.raw for m in unverified}),
        "flagged_details": [
            {"raw": m.raw, "line": m.line, "category": m.category} for m in unverified[:20]
        ],
    }
    return issues, provenance


_WORDED_SCALE = re.compile(
    r"\b(?:millions?|billions?|thousands?|hundreds?|dozens?)\s+of\s+[a-z]+", re.IGNORECASE
)


def check_scale_claims(original_resume: str, tailored_resume: str) -> Tuple[List[ValidationIssue], List[str]]:
    """Flag worded scale claims the source resume does not make.

    "millions of orders" is the same fabrication as "40% faster", but it carries
    no digits, so the metric extractor never saw it and a draft containing one
    was reported clean. Returns the offending phrases alongside the issues so the
    grounding pass can strip them the same way it strips numbers.
    """
    source = (original_resume or "").lower()
    issues: List[ValidationIssue] = []
    phrases: List[str] = []

    for match in _WORDED_SCALE.finditer(tailored_resume or ""):
        phrase = match.group(0)
        if phrase.lower() in source:
            continue
        if phrase in phrases:
            continue
        phrases.append(phrase)
        issues.append(
            ValidationIssue(
                severity=Severity.ERROR,
                category="consistency",
                message=f'Unverified scale claim: "{phrase}" is not supported by the original resume',
                suggestion="Remove the scale claim or describe the work without quantifying it.",
            )
        )
    return issues, phrases


def check_skill_authenticity(
    original_resume: str,
    tailored_resume: str,
    *,
    jd_skills: Optional[List[str]] = None,
    confirmed_skills: Optional[List[str]] = None,
) -> List[ValidationIssue]:
    """Flag a JD skill that appears in the draft but in neither source nor confirmed set.

    Scoped to skills the job description asks for, because those are the ones a
    tailoring pass has a motive to add. A term the candidate genuinely uses that
    the JD never mentions is not a fabrication risk worth reporting.
    """
    haystack = f"{original_resume or ''}\n{' '.join(confirmed_skills or [])}".lower()
    draft = (tailored_resume or "").lower()

    issues = []
    for skill in jd_skills or []:
        needle = str(skill).strip().lower()
        # Very short tokens ("go", "r", "c") match inside unrelated words; the
        # false-positive rate is not worth the few real catches.
        if len(needle) < 3:
            continue
        # A capability phrase is not a technology. "Service architecture",
        # "cross-functional collaboration" and "API development" are things a job
        # description asks for in prose; flagging them as fabricated skills
        # buries the real findings in noise.
        #
        # One generic word is enough to make the phrase a capability: requiring
        # *every* word to be generic still flagged "API development", because
        # "API" is a real term. Named technologies survive because none of their
        # words are generic ("Spring Boot", "Apache Kafka", "Node.js").
        words = [word for word in re.split(r"[\s/-]+", needle) if word]
        if len(words) > 1 and any(word in _CAPABILITY_WORDS for word in words):
            continue
        if needle in draft and needle not in haystack:
            issues.append(
                ValidationIssue(
                    severity=Severity.ERROR,
                    category="consistency",
                    message=f"Unsupported skill: '{skill}' appears in the tailored resume but not in the original or your confirmed skills",
                    suggestion=f"Remove '{skill}', or add it to your confirmed skills if you do have that experience.",
                )
            )
    return issues


_FIRST_PERSON = re.compile(r"\b(?:I|I'm|I've|I'll|my|me)\b")
_BULLET = re.compile(r"^\s*[-*\u2022]\s+(.*)$", re.MULTILINE)


def _bullets(text: str) -> List[str]:
    """Accomplishment lines, however the document marks them.

    A resume exported from Google Docs carries no "-" markers, so matching only
    markdown bullets found nothing in the source and silently skipped the
    length comparison against it.
    """
    marked = [b.strip() for b in _BULLET.findall(text or "") if b.strip()]
    if marked:
        return marked
    return [
        line.strip()
        for line in (text or "").splitlines()
        if len(line.split()) > 6 and not line.lstrip().startswith(("#", "**"))
    ]


def check_voice_drift(
    original_resume: str,
    tailored_resume: str,
    *,
    length_tolerance: float = 1.25,
) -> List[ValidationIssue]:
    """Flag a draft that has wandered out of the source document's register.

    A resume is terse fragments, not prose about the candidate. A tailoring pass
    told to "sound human" will drift into narration -- first person, bullets that
    explain what they imply -- which reads as a cover letter, buries the verb and
    the technology, and lowers what a skimming reader gets per line.

    Both checks are relative to the source, so a candidate who does write in the
    first person keeps their own voice.
    """
    issues: List[ValidationIssue] = []

    source_first_person = len(_FIRST_PERSON.findall(original_resume or ""))
    draft_first_person = len(_FIRST_PERSON.findall(tailored_resume or ""))
    if source_first_person == 0 and draft_first_person > 0:
        issues.append(
            ValidationIssue(
                severity=Severity.WARNING,
                category="voice",
                message=(
                    f"Draft introduces first-person writing ({draft_first_person} instances) "
                    "where the original resume uses none"
                ),
                suggestion="Rewrite those lines as verb-first fragments, matching the original resume.",
            )
        )

    source_bullets = _bullets(original_resume)
    draft_bullets = _bullets(tailored_resume)
    if source_bullets and draft_bullets:
        source_avg = sum(len(b.split()) for b in source_bullets) / len(source_bullets)
        draft_avg = sum(len(b.split()) for b in draft_bullets) / len(draft_bullets)
        if source_avg and draft_avg > source_avg * length_tolerance:
            issues.append(
                ValidationIssue(
                    severity=Severity.WARNING,
                    category="voice",
                    message=(
                        f"Bullets grew from about {source_avg:.0f} words to {draft_avg:.0f}; "
                        "the draft is explaining rather than stating"
                    ),
                    suggestion="Trim bullets back toward the original's length; state the accomplishment and stop.",
                )
            )

    return issues


def has_errors(issues: List[ValidationIssue]) -> bool:
    return any(str(getattr(issue, "severity", "")) in ("error", "Severity.ERROR") for issue in issues)


def quality_score(issues: List[ValidationIssue]) -> int:
    """Score 0-100 from the issues found. Errors dominate; warnings nudge."""
    score = 100
    for issue in issues:
        severity = str(getattr(issue, "severity", ""))
        if severity in ("error", "Severity.ERROR"):
            score -= 15
        elif severity in ("warning", "Severity.WARNING"):
            score -= 5
    return max(0, min(100, score))
