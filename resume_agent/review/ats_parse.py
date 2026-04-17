"""Deterministic ATS parse review for structure and parsing hazards."""

from __future__ import annotations

import re
from typing import List

from ..models.agent_models import ReviewIssue, ReviewSection, Severity

STANDARD_SECTIONS = ("experience", "education", "skills", "summary", "projects", "certifications")
DATE_PATTERNS = (
    r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+\d{4}\b",
    r"\b\d{1,2}/\d{4}\b",
    r"\b\d{4}\s*[-–]\s*(?:present|\d{4})\b",
)


def _issue(severity: Severity, category: str, message: str, suggestion: str | None = None, evidence: str | None = None) -> ReviewIssue:
    return ReviewIssue(
        severity=severity,
        category=category,
        message=message,
        suggestion=suggestion,
        evidence=evidence,
    )


def review_ats_parse(resume_text: str) -> ReviewSection:
    """Score ATS parseability using deterministic structure checks only."""
    issues: List[ReviewIssue] = []
    score = 100
    lines = [line.rstrip() for line in resume_text.splitlines()]
    non_empty_lines = [line for line in lines if line.strip()]
    lower_text = resume_text.lower()

    found_sections = [section for section in STANDARD_SECTIONS if re.search(rf"\b{re.escape(section)}\b", lower_text)]
    if len(found_sections) < 2:
        score -= 15
        issues.append(_issue(
            Severity.ERROR,
            "structure",
            "Resume is missing standard section headings.",
            "Use recognizable headings like Experience, Education, and Skills.",
        ))
    elif len(found_sections) < 4:
        score -= 6
        issues.append(_issue(
            Severity.WARNING,
            "structure",
            "Resume has only limited standard section coverage.",
            "Add clear section headings so ATS parsers can map content reliably.",
        ))

    header_window = "\n".join(lines[:5])
    has_email = bool(re.search(r"[\w.\-+]+@[\w.\-]+\.\w+", resume_text))
    has_phone = bool(re.search(r"(?:\+?\d[\d\s().-]{8,}\d)", resume_text))
    if not header_window.strip():
        score -= 10
        issues.append(_issue(Severity.ERROR, "header", "Resume header is missing or empty.", "Add your name and contact details at the top."))
    if not has_email or not has_phone:
        score -= 8
        issues.append(_issue(
            Severity.WARNING,
            "contact",
            "Contact details look incomplete for ATS parsing.",
            "Include both an email address and a phone number in the header.",
        ))

    if re.search(r"[│┆┊┃┏┓┗┛╭╮╯╰■◆●☑✓★]", resume_text):
        score -= 10
        issues.append(_issue(
            Severity.WARNING,
            "parse_hazard",
            "Decorative symbols or box-drawing characters may confuse ATS parsers.",
            "Prefer plain text bullets and simple punctuation.",
        ))

    if re.search(r"\|.+\|", resume_text) or re.search(r"<table|<tr|<td", resume_text, re.IGNORECASE):
        score -= 15
        issues.append(_issue(
            Severity.ERROR,
            "parse_hazard",
            "Table-like formatting detected.",
            "Replace tables or grid layouts with plain text sections and bullet lists.",
        ))

    bullet_lines = [line for line in non_empty_lines if re.match(r"^\s*[-*•]", line)]
    if bullet_lines and len(bullet_lines) < max(3, len(non_empty_lines) // 12):
        score -= 4
        issues.append(_issue(
            Severity.INFO,
            "readability",
            "Resume has limited bullet usage relative to its length.",
            "Use concise bullet points for experience entries.",
        ))

    bullet_styles = set()
    for line in bullet_lines[:40]:
        match = re.match(r"^\s*([-*•])", line)
        if match:
            bullet_styles.add(match.group(1))
    if len(bullet_styles) > 1:
        score -= 5
        issues.append(_issue(
            Severity.WARNING,
            "consistency",
            "Multiple bullet styles detected.",
            "Use one bullet style consistently across the resume.",
        ))

    date_style_hits = 0
    style_count = 0
    for pattern in DATE_PATTERNS:
        hits = re.findall(pattern, lower_text, re.IGNORECASE)
        if hits:
            style_count += 1
            date_style_hits += len(hits)
    if style_count > 2:
        score -= 8
        issues.append(_issue(
            Severity.WARNING,
            "consistency",
            "Multiple date formats detected across the resume.",
            "Standardize dates to one format, such as 'Jan 2024 - Present'.",
        ))

    word_count = len(re.findall(r"\b\w+\b", resume_text))
    if word_count < 250:
        score -= 10
        issues.append(_issue(
            Severity.WARNING,
            "length",
            "Resume may be too short for consistent ATS and recruiter evaluation.",
            "Add relevant accomplishments and supporting detail if experience is being undersold.",
        ))
    elif word_count > 1100:
        score -= 8
        issues.append(_issue(
            Severity.WARNING,
            "length",
            "Resume is long enough to create parsing and readability drag.",
            "Trim older or lower-value detail and keep the strongest evidence.",
        ))

    blank_line_ratio = (len(lines) - len(non_empty_lines)) / max(len(lines), 1)
    if blank_line_ratio > 0.45:
        score -= 5
        issues.append(_issue(
            Severity.INFO,
            "spacing",
            "Resume uses a large amount of whitespace.",
            "Reduce excessive blank lines to keep section flow compact.",
        ))

    score = max(0, min(100, score))
    if score >= 85:
        verdict = "pass"
        summary = "Structure looks ATS-safe with only minor formatting risk."
    elif score >= 70:
        verdict = "warn"
        summary = "Resume is generally parseable but has formatting risks worth fixing."
    else:
        verdict = "fail"
        summary = "ATS parsing risk is high enough that the format should be fixed before applying."

    recommendations = []
    for issue in issues:
        if issue.suggestion and issue.suggestion not in recommendations:
            recommendations.append(issue.suggestion)

    return ReviewSection(
        score=score,
        verdict=verdict,
        summary=summary,
        issues=issues,
        recommendations=recommendations,
        metrics={
            "word_count": word_count,
            "standard_sections_found": found_sections,
            "bullet_styles": sorted(bullet_styles),
            "date_formats_detected": style_count,
            "has_email": has_email,
            "has_phone": has_phone,
            "line_count": len(lines),
        },
    )
