"""Shared fit-evaluation path.

Both the Chrome-extension endpoint (`POST /api/evaluate-fit`) and Discovery
(`POST /api/discover/roles/{id}/evaluate-fit`) need the same three things: a
readable resume, a job description, and a fit score. Keeping that here stops the
two callers from drifting apart.

Errors carry a `code` so each router can map them onto its own HTTP semantics
without this module importing FastAPI.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from ..storage.google_docs import read_resume_file
from ..utils.exceptions import GoogleAPIError
from ..utils.google_ids import extract_google_doc_id
from ..utils.logger import logger


class FitEvaluationError(RuntimeError):
    """Fit evaluation could not be completed."""

    def __init__(self, message: str, code: str = "failed"):
        super().__init__(message)
        self.code = code


def normalize_resume_doc_ids(candidates: Sequence[Optional[str]]) -> List[str]:
    """De-duplicate and normalize resume doc id candidates, preserving priority order."""
    doc_ids: List[str] = []
    for raw_value in candidates:
        normalized = extract_google_doc_id(raw_value)
        if normalized and normalized not in doc_ids:
            doc_ids.append(normalized)
    return doc_ids


def load_resume_text(google_services, doc_ids: Sequence[str]) -> str:
    """Read the first readable resume from the candidate doc ids.

    Raises FitEvaluationError with codes: no_google_session, no_resume_configured,
    resume_forbidden, resume_unreadable.
    """
    if not google_services:
        raise FitEvaluationError(
            "Google sign-in required. Open the Resume Agent web app in this browser, "
            "sign in with Google, then try again (the extension uses the same session).",
            code="no_google_session",
        )
    if not doc_ids:
        raise FitEvaluationError(
            "No resume configured. Set RESUME_DOC_ID or pass resume_doc_id.",
            code="no_resume_configured",
        )

    drive_service, docs_service = google_services
    last_google_error: Optional[GoogleAPIError] = None
    for doc_id in doc_ids:
        try:
            resume_text = read_resume_file(drive_service, docs_service, doc_id)
            if resume_text:
                return resume_text
        except GoogleAPIError as e:
            last_google_error = e
            continue

    if last_google_error and (
        "not found" in str(last_google_error).lower() or "inaccessible" in str(last_google_error).lower()
    ):
        raise FitEvaluationError(
            "That resume file wasn't found or you don't have access. "
            "If you're using the extension: open the web app, sign in with Google, "
            "pick your resume from the app's Drive picker, then try Evaluate fit again from the extension.",
            code="resume_forbidden",
        )
    if last_google_error:
        raise FitEvaluationError(str(last_google_error), code="resume_unreadable")
    raise FitEvaluationError(
        "No readable resume could be loaded for fit evaluation.",
        code="resume_unreadable",
    )


def serialize_fit_evaluation(evaluation) -> Dict[str, Any]:
    """Flatten a FitEvaluation into the payload shape both fit endpoints return."""
    return {
        "score": evaluation.score,
        "should_apply": evaluation.should_apply,
        "confidence": evaluation.confidence,
        "matching_areas": getattr(evaluation, "matching_areas", []) or [],
        "missing_areas": getattr(evaluation, "missing_areas", []) or [],
        "recommendations": getattr(evaluation, "recommendations", []) or [],
    }


def evaluate_fit_for_jd(
    *,
    jd_text: str,
    resume_text: str,
    llm_service=None,
    google_services=None,
    local_user_id: Optional[int] = None,
    job_url: Optional[str] = None,
    company: str = "",
    job_title: str = "",
) -> Dict[str, Any]:
    """Run the parse + evaluate steps of the workflow and return a serialized evaluation."""
    if not (jd_text or "").strip():
        raise FitEvaluationError("No job description text to evaluate.", code="no_jd_text")
    if not (resume_text or "").strip():
        raise FitEvaluationError("No readable resume could be loaded for fit evaluation.", code="resume_unreadable")

    from .llm_service import LLMService
    from .resume_workflow import TailorResumeRequest, TailorResumeResult, WorkflowStep

    try:
        from .multi_agent_workflow import MultiAgentWorkflowService as WorkflowService
    except (ImportError, NameError):  # pragma: no cover - mirrors the api/main.py fallback
        from .resume_workflow import ResumeWorkflowService as WorkflowService

    llm_service = llm_service or LLMService()
    workflow = WorkflowService(llm_service=llm_service, google_services=google_services)

    request = TailorResumeRequest(
        company=company,
        job_title=job_title,
        jd_text=jd_text,
        job_url=job_url,
        evaluate_first=True,
        evaluate_only=True,
        local_user_id=local_user_id,
    )
    result = TailorResumeResult(
        current_step=WorkflowStep.LOADING_RESUME,
        resume_text=resume_text,
        original_resume_text=resume_text,
    )
    for step in (WorkflowStep.PARSING_RESUME, WorkflowStep.EVALUATING_FIT):
        result = workflow.execute_workflow_step(request, step, result)
        if result.error:
            raise FitEvaluationError(result.error, code="failed")

    if not result.evaluation:
        raise FitEvaluationError("Fit evaluation returned no result.", code="failed")

    logger.info(
        "Fit evaluation completed",
        score=result.evaluation.score,
        should_apply=result.evaluation.should_apply,
    )
    return serialize_fit_evaluation(result.evaluation)
