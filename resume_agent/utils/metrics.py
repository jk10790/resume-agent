"""
Metric extraction and normalization utilities.
Used to validate numeric claims and ensure provenance.
"""

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional
import re


@dataclass(frozen=True)
class MetricMatch:
    raw: str
    normalized: str
    line: str
    category: str


_PERCENT_PATTERN = re.compile(r"(?P<num>\d+(?:\.\d+)?)\s*%", re.IGNORECASE)
_PERCENT_WORD_PATTERN = re.compile(r"(?P<num>\d+(?:\.\d+)?)\s*percent\b", re.IGNORECASE)
_CURRENCY_PATTERN = re.compile(r"\$\s?(?P<num>\d+(?:,\d{3})*(?:\.\d+)?)")
_SCALE_PATTERN = re.compile(r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>k|m|b|thousand|million|billion)\b", re.IGNORECASE)
_RATIO_PATTERN = re.compile(r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>x|times)\b", re.IGNORECASE)
_TIME_PATTERN = re.compile(
    r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>ms|s|sec|secs|seconds|minutes|min|hours|days|weeks|months|years)\b",
    re.IGNORECASE,
)

_COUNT_UNIT_PATTERN = re.compile(
    r"(?P<num>\d+(?:,\d{3})*|\d+)\+?\s*(?P<unit>"
    r"user requests|requests|users|services|microservices|tests|defects|bugs|issues|releases|deployments|"
    r"pipelines|projects|tickets|endpoints|apis|api|features|repos|repositories|systems|servers|clusters|"
    r"nodes|databases|tables|rows|records|files|scripts|jobs|workflows|teams|people|engineers"
    r")\b",
    re.IGNORECASE,
)

_COMMA_NUMBER_PATTERN = re.compile(r"\b(?P<num>\d{1,3}(?:,\d{3})+)\b")


def _normalize_number(value: str) -> str:
    return value.replace(",", "").strip()


def _is_year(value: str) -> bool:
    if not value.isdigit() or len(value) != 4:
        return False
    year = int(value)
    return 1900 <= year <= 2099


def _normalize_scale(num: str, unit: str) -> str:
    value = float(_normalize_number(num))
    unit_lower = unit.lower()
    multiplier = 1
    if unit_lower in ("k", "thousand"):
        multiplier = 1_000
    elif unit_lower in ("m", "million"):
        multiplier = 1_000_000
    elif unit_lower in ("b", "billion"):
        multiplier = 1_000_000_000
    normalized = int(value * multiplier) if value.is_integer() else value * multiplier
    return f"{normalized}".rstrip("0").rstrip(".")


def _add_match(matches: List[MetricMatch], raw: str, normalized: str, line: str, category: str) -> None:
    if raw and normalized:
        matches.append(MetricMatch(raw=raw.strip(), normalized=normalized.strip(), line=line.strip(), category=category))


def extract_metrics(text: str) -> List[MetricMatch]:
    """Extract numeric claims from text with normalization."""
    matches: List[MetricMatch] = []
    if not text:
        return matches

    lines = text.splitlines()
    for line in lines:
        if not line.strip():
            continue

        for match in _PERCENT_PATTERN.finditer(line):
            num = _normalize_number(match.group("num"))
            _add_match(matches, match.group(0), f"percent:{num}", line, "percent")

        for match in _PERCENT_WORD_PATTERN.finditer(line):
            num = _normalize_number(match.group("num"))
            _add_match(matches, match.group(0), f"percent:{num}", line, "percent")

        for match in _CURRENCY_PATTERN.finditer(line):
            num = _normalize_number(match.group("num"))
            _add_match(matches, match.group(0), f"usd:{num}", line, "currency")

        for match in _SCALE_PATTERN.finditer(line):
            num = _normalize_scale(match.group("num"), match.group("unit"))
            unit = match.group("unit").lower()
            _add_match(matches, match.group(0), f"scale:{unit}:{num}", line, "scale")

        for match in _RATIO_PATTERN.finditer(line):
            num = _normalize_number(match.group("num"))
            unit = match.group("unit").lower()
            _add_match(matches, match.group(0), f"ratio:{unit}:{num}", line, "ratio")

        for match in _TIME_PATTERN.finditer(line):
            num = _normalize_number(match.group("num"))
            unit = match.group("unit").lower()
            _add_match(matches, match.group(0), f"time:{unit}:{num}", line, "time")

        for match in _COUNT_UNIT_PATTERN.finditer(line):
            num = _normalize_number(match.group("num"))
            unit = match.group("unit").lower()
            _add_match(matches, match.group(0), f"count:{unit}:{num}", line, "count")

        for match in _COMMA_NUMBER_PATTERN.finditer(line):
            num = _normalize_number(match.group("num"))
            if not _is_year(num):
                _add_match(matches, match.group(0), f"number:{num}", line, "number")

    return matches


def normalize_metric_set(metrics: Iterable[MetricMatch]) -> Dict[str, MetricMatch]:
    """Return a normalized lookup for metric matches."""
    return {metric.normalized: metric for metric in metrics}


def extract_metrics_from_user_answers(user_answers: Optional[Dict[str, Any]]) -> List[MetricMatch]:
    """Extract metrics from user-provided answers (dict values)."""
    if not user_answers:
        return []

    texts: List[str] = []
    for value in user_answers.values():
        if isinstance(value, str) and value.strip():
            texts.append(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item.strip():
                    texts.append(item)
    return extract_metrics("\n".join(texts))


def extract_metrics_from_memory(memory: Dict[str, Any]) -> List[MetricMatch]:
    """Extract metrics from stored user memory (clarifications/answers)."""
    if not memory:
        return []

    texts: List[str] = []
    user_answers = memory.get("user_answers")
    if isinstance(user_answers, dict):
        texts.extend([str(v) for v in user_answers.values() if isinstance(v, str) and str(v).strip()])

    for key in ("metrics_by_role", "metrics_details", "team_size", "project_scale", "notable_achievements"):
        value = memory.get(key)
        if isinstance(value, str) and value.strip():
            texts.append(value)

    return extract_metrics("\n".join(texts))
