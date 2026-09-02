"""Turning pipeline results into plain dicts.

One home, because a fit evaluation is rendered by the web API, the Chrome
extension endpoint and Discovery, and those had drifted into three shapes with
different key sets.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def serialize_evaluation(evaluation: Any) -> Optional[Dict[str, Any]]:
    """A FitEvaluation as the API returns it."""
    if not evaluation:
        return None
    return {
        "score": evaluation.score,
        "should_apply": evaluation.should_apply,
        "confidence": evaluation.confidence,
        "matching_areas": getattr(evaluation, "matching_areas", []) or [],
        "missing_areas": getattr(evaluation, "missing_areas", []) or [],
        "recommendations": getattr(evaluation, "recommendations", []) or [],
        "reasoning": getattr(evaluation, "reasoning", None),
    }
