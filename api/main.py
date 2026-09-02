"""
FastAPI Backend for Resume Agent
Provides REST API with Server-Sent Events for streaming progress updates.
"""

from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from pydantic import BaseModel
from typing import Optional, Dict, List, Any
import json
import asyncio
import traceback
import secrets

from dataclasses import replace

from resume_agent.llm.pipeline import PipelineError
from resume_agent.pipelines import (
    ApprovedDraft,
    FitRequest,
    ResumeOrchestrator,
    TailorRequest,
    serialize_evaluation,
)
from resume_agent.validation import validate_tailored_resume
from resume_agent.services.resume_source import (
    ResumeUnavailable,
    load_first_readable,
    normalize_doc_ids,
)
from resume_agent.services.llm_service import LLMService, get_llm_service
from resume_agent.storage.google_docs import read_google_doc, read_resume_file, write_to_google_doc
from resume_agent.storage.google_drive_utils import list_google_docs, list_google_folders, get_file_metadata, GOOGLE_DOC_MIME
from resume_agent.storage.user_context import reset_current_user, set_current_user
from resume_agent.storage.user_store import (
    add_job_strategy_event_for_user,
    clear_improved_resume_for_user,
    find_job_strategy_brief_for_user,
    get_improved_resume_for_user,
    get_job_strategy_brief_for_user,
    get_user_evidence_records,
    get_quality_report_for_user,
    get_user_by_id,
    get_user_skill_records,
    list_job_strategy_briefs_for_user,
    list_job_strategy_events_for_user,
    replace_user_skill_records,
    replace_user_evidence_records,
    save_improved_resume_for_user,
    save_quality_report_for_user,
    update_job_strategy_brief_status_for_user,
)
from resume_agent.review.bundle_builder import build_review_bundle
from resume_agent.utils.exceptions import GoogleAPIError
from resume_agent.utils.google_ids import extract_google_doc_id
from resume_agent.utils.logger import logger

app = FastAPI(
    title="Resume Agent API",
    version="1.0.0",
    description="AI-powered resume tailoring and job application assistant"
)

# Lightweight health check for local usage and integration tests.
@app.get("/")
async def root():
    return {"ok": True, "service": "resume-agent-api"}

# CORS middleware for React frontend (configurable)
from resume_agent.config import settings
cors_origins = [origin.strip() for origin in settings.api_cors_origins.split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Approval storage (pluggable backend)
from resume_agent.storage.approval_storage import create_approval_storage
approval_storage = create_approval_storage()

# Session management for OAuth

# Add session middleware (must be added after app creation but before routes)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret_key,
    max_age=3600 * 24 * 7,  # 7 days
    same_site="lax",
    https_only=False  # Set to True in production with HTTPS
)

# Mount modular routers (health, applications, google drive, auth + user skills)
from api.routers import health_router, applications_router, google_drive_router, auth_router, discover_router
app.include_router(health_router)
app.include_router(applications_router)
app.include_router(google_drive_router)
app.include_router(auth_router)
app.include_router(discover_router)


@app.middleware("http")
async def bind_current_user_context(request: Request, call_next):
    """Bind authenticated session user into request-local context for storage helpers."""
    session_payload = request.scope.get("session")
    session_user = None
    if isinstance(session_payload, dict):
        session_user = session_payload.get("user_data", {}).get("local_user")
    token = set_current_user(session_user)
    try:
        response = await call_next(request)
        return response
    finally:
        reset_current_user(token)

# Helper functions for session management
def get_session_data(request: Request) -> Optional[Dict[str, Any]]:
    """Get current user session data"""
    return request.session.get("user_data")

def set_session_data(request: Request, data: Dict[str, Any], merge: bool = True):
    """
    Set user session data.
    
    Args:
        request: FastAPI Request object
        data: Dictionary of data to set
        merge: If True, merge with existing session data. If False, overwrite.
    """
    if merge:
        existing = get_session_data(request) or {}
        existing.update(data)
        request.session["user_data"] = existing
    else:
        request.session["user_data"] = data

def clear_session(request: Request):
    """Clear user session"""
    request.session.clear()

# Helper function to get Google services from session or fallback
def get_google_services_from_request(request: Request):
    """
    Get Google services from session credentials (sign in with Google in the web app).
    
    If token is refreshed, updates session with new token and expiry.
    
    Returns:
        Tuple of (drive_service, docs_service) or None if not signed in
    """
    session_data = get_session_data(request)
    session_creds = session_data.get("google_credentials") if session_data else None
    
    if session_creds:
        try:
            from resume_agent.storage.google_oauth import credentials_from_dict, credentials_to_dict
            from googleapiclient.discovery import build
            
            # Store old expiry to detect refresh
            old_expiry = session_creds.get('expiry')
            old_token = session_creds.get('token')
            
            # Get credentials (this may refresh if expired)
            creds = credentials_from_dict(session_creds)
            
            # Check if token was refreshed by comparing expiry or token
            # If refreshed, update session with new credentials
            new_expiry = creds.expiry.isoformat() if creds.expiry else None
            new_token = creds.token
            
            # Token was refreshed if expiry changed or token changed (and not expired now)
            if not creds.expired and (old_expiry != new_expiry or old_token != new_token):
                # Token was refreshed, update session
                updated_creds_dict = credentials_to_dict(creds)
                session_data = get_session_data(request) or {}
                session_data['google_credentials'] = updated_creds_dict
                set_session_data(request, session_data)
                logger.debug("Updated session with refreshed OAuth token")
            
            drive_service = build('drive', 'v3', credentials=creds)
            docs_service = build('docs', 'v1', credentials=creds)
            return drive_service, docs_service
        except Exception as e:
            err_str = str(e).lower()
            if "invalid_grant" in err_str or "expired" in err_str or "revoked" in err_str:
                # Session tokens are dead; clear session so user signs in again
                request.session.clear()
                logger.warning("Cleared session after expired/revoked Google token")
                raise HTTPException(
                    status_code=401,
                    detail="Your Google session expired or was revoked. Please sign in again (Sign in with Google in the extension or web app).",
                )
            logger.warning(f"Failed to use session credentials: {e}")
        return None
    return None


def get_local_user_from_request(request: Request) -> Dict[str, Any]:
    """Return the authenticated local user from the session."""
    session_data = get_session_data(request) or {}
    local_user = session_data.get("local_user")
    if not local_user or not local_user.get("id"):
        raise HTTPException(status_code=401, detail="Please sign in with Google first.")
    return local_user


def get_preferred_resume_doc_id(request: Request) -> Optional[str]:
    """Get the authenticated user's persisted preferred resume id, if any."""
    try:
        local_user = get_local_user_from_request(request)
    except HTTPException:
        return None
    fresh_user = get_user_by_id(int(local_user["id"])) or local_user
    return extract_google_doc_id(fresh_user.get("preferred_resume_doc_id"))


class TailorResumeAPIRequest(BaseModel):
    """API request model for tailoring resume. Company and job title are optional (used for folder/doc naming when saving)."""
    company: Optional[str] = ""
    job_title: Optional[str] = ""
    jd_text: str
    job_url: Optional[str] = None
    evaluate_first: bool = True
    evaluate_only: bool = False
    track_application: bool = True
    tailoring_intensity: str = "medium"  # "light", "medium", "heavy"
    sections_to_tailor: Optional[list] = None  # List of section names
    refinement_feedback: Optional[str] = None  # Feedback for refinement
    target_entry_text: Optional[str] = None  # Specific line/bullet to refine
    revert_target_entry: bool = False
    protected_entry_texts: Optional[list[str]] = None  # Exact draft entries to preserve during refinement
    preserve_sections: Optional[list[str]] = None  # Sections to preserve exactly
    resume_doc_id: Optional[str] = None  # Optional: specific resume doc ID (defaults to configured)
    save_folder_id: Optional[str] = None  # Optional: folder to save to (defaults to configured)
    discovered_role_id: Optional[int] = None


class EvaluateFitRequest(BaseModel):
    """Request for fit evaluation only (e.g. from Chrome extension)"""
    job_url: Optional[str] = None
    jd_text: Optional[str] = None
    resume_doc_id: Optional[str] = None


class ExtractJDRequest(BaseModel):
    """API request model for extracting job description"""
    job_url: str


class ApprovalRequest(BaseModel):
    """Request to approve or reject a tailored resume"""
    approval_id: str
    approved: bool
    feedback: Optional[str] = None  # Optional feedback if rejected


class RefinementRequest(BaseModel):
    """Request to refine a tailored resume"""
    approval_id: str
    feedback: Optional[str] = None  # What to improve
    sections_to_tailor: Optional[list[str]] = None  # Restrict refinement to specific sections
    target_entry_text: Optional[str] = None  # Specific line/bullet to refine
    revert_target_entry: bool = False
    protected_entry_texts: Optional[list[str]] = None  # Exact current-draft lines to preserve
    preserve_sections: Optional[list[str]] = None  # Sections to preserve exactly


class ApprovalDraftUpdateRequest(BaseModel):
    """Request to replace the current approval draft text after hunk-level edits."""
    approval_id: str
    tailored_resume: str


class StrategyBriefUpdateRequest(BaseModel):
    """Request to update the staged strategy brief before approval."""
    approval_id: str
    strategy_brief: Dict[str, Any]


class JobStrategyEvaluateRequest(BaseModel):
    company: Optional[str] = ""
    job_title: Optional[str] = ""
    jd_text: str
    job_url: Optional[str] = None
    resume_doc_id: Optional[str] = None


class JobStrategyTailorRequest(BaseModel):
    jd_text: Optional[str] = None
    resume_doc_id: Optional[str] = None
    tailoring_intensity: str = "medium"
    preserve_sections: Optional[list[str]] = None
    protected_entry_texts: Optional[list[str]] = None


class JobStrategyRegenerateRequest(BaseModel):
    jd_text: Optional[str] = None
    section: str
    resume_doc_id: Optional[str] = None


class JobStrategyRebaselineRequest(BaseModel):
    company: Optional[str] = None
    job_title: Optional[str] = None
    job_url: Optional[str] = None
    jd_text: Optional[str] = None
    resume_doc_id: Optional[str] = None


class StrategyDecisionRequest(BaseModel):
    reason: Optional[str] = None


class UserEvidenceRequest(BaseModel):
    evidence: List[Dict[str, Any]]


class QualityAnalysisRequest(BaseModel):
    """Request to analyze resume quality"""
    resume_doc_id: Optional[str] = None  # Google Doc ID, or use default
    resume_text: Optional[str] = None  # Or provide text directly
    improve: bool = False  # If True, also improve the resume
    user_answers: Optional[dict] = None  # Answers to clarifying questions
    issue_resolutions: Optional[dict] = None  # Per-issue approve/skip/custom instructions


def serialize_validation(validation, top_level_ats_score=None):
    if not validation:
        return None
    return {
        "quality_score": validation.quality_score,
        "is_valid": validation.is_valid,
        "ats_score": validation.ats_score,
        "job_match_score": top_level_ats_score,
        "issues": [
            {
                "severity": issue.severity,
                "category": issue.category,
                "message": issue.message,
                "suggestion": issue.suggestion,
            }
            for issue in validation.issues
        ],
        "jd_coverage": validation.jd_coverage,
        "recommendations": validation.recommendations,
        "metric_provenance": validation.metric_provenance,
    }


def serialize_review_bundle(review_bundle):
    if not review_bundle:
        return None

    def serialize_section(section):
        return {
            "score": section.score,
            "verdict": section.verdict,
            "summary": section.summary,
            "issues": [
                {
                    "severity": issue.severity,
                    "category": issue.category,
                    "message": issue.message,
                    "suggestion": issue.suggestion,
                    "evidence": issue.evidence,
                }
                for issue in section.issues
            ],
            "recommendations": section.recommendations,
            "metrics": section.metrics,
        }

    return {
        "authenticity": serialize_section(review_bundle.authenticity),
        "ats_parse": serialize_section(review_bundle.ats_parse),
        "job_match": serialize_section(review_bundle.job_match),
        "strategy_alignment": serialize_section(review_bundle.strategy_alignment),
        "editorial": serialize_section(review_bundle.editorial),
        "overall": {
            "score": review_bundle.overall.score,
            "verdict": review_bundle.overall.verdict,
            "summary": review_bundle.overall.summary,
            "recommendation": review_bundle.overall.recommendation,
            "top_wins": review_bundle.overall.top_wins,
            "top_risks": review_bundle.overall.top_risks,
            "readiness_checks": review_bundle.overall.readiness_checks,
        },
    }


def serialize_strategy_brief(brief):
    if not brief:
        return None
    evidence_items = list(brief.requirement_evidence or [])
    gap_items = list(brief.gap_assessments or [])
    evidence_sections = sorted({item.source_section for item in evidence_items if item.source_section})
    blocker_reason_codes = sorted({item.reason_code for item in gap_items if getattr(item, "reason_code", None)})
    return {
        "id": brief.id,
        "company": brief.company,
        "job_title": brief.job_title,
        "job_url": brief.job_url,
        "jd_text": brief.jd_text,
        "archetype": brief.archetype,
        "target_alignment": getattr(brief, "target_alignment", "unranked"),
        "role_summary": brief.role_summary,
        "fit_score": brief.fit_score,
        "should_apply": brief.should_apply,
        "confidence": brief.confidence,
        "gating_decision": brief.gating_decision,
        "requirement_evidence": [
            {
                "requirement": item.requirement,
                "status": item.status,
                "evidence": item.evidence,
                "source_section": item.source_section,
            }
            for item in brief.requirement_evidence
        ],
        "gap_assessments": [
            {
                "requirement": item.requirement,
                "severity": item.severity,
                "mitigation": item.mitigation,
                "reason_code": getattr(item, "reason_code", None),
            }
            for item in brief.gap_assessments
        ],
        "positioning_strategy": brief.positioning_strategy,
        "tailoring_directives": [
            {
                "id": item.id,
                "section": item.section,
                "action": item.action,
                "rationale": item.rationale,
                "enabled": item.enabled,
            }
            for item in brief.tailoring_directives
        ],
        "interview_seeds": brief.interview_seeds,
        "risk_notes": brief.risk_notes,
        "provenance": {
            "matched_requirement_count": sum(1 for item in evidence_items if item.status == "matched"),
            "adjacent_requirement_count": sum(1 for item in evidence_items if item.status == "adjacent"),
            "gap_requirement_count": sum(1 for item in evidence_items if item.status == "gap"),
            "evidence_sections": evidence_sections,
            "blocker_reason_codes": blocker_reason_codes,
            "sample_evidence": [
                {
                    "requirement": item.requirement,
                    "status": item.status,
                    "evidence": item.evidence,
                    "source_section": item.source_section,
                }
                for item in evidence_items[:3]
            ],
        },
        "approval_status": brief.approval_status,
        "created_at": brief.created_at,
        "updated_at": brief.updated_at,
    }


def serialize_tailor_outcome(outcome, approval_id=None):
    """Serialise a TailorOutcome for the API.

    `usage` and `steps` are new: what the run cost, and where it went. They were
    collected before but never left the process.
    """
    evaluation = outcome.evaluation
    return {
        "tailored_resume": outcome.tailored_resume or "",
        "original_resume_text": outcome.original_resume_text,
        "evaluation": serialize_evaluation(evaluation),
        "validation": serialize_validation(outcome.validation, outcome.ats_score),
        "review_bundle": serialize_review_bundle(outcome.review_bundle),
        "ats_score": outcome.ats_score,
        "approval_id": approval_id,
        # A draft returned with an approval id is waiting on the user. The
        # strategy stage is reached through the /api/job-strategy endpoints,
        # never from this stream.
        "approval_required": approval_id is not None,
        "approval_status": "pending" if approval_id else None,
        "approval_stage": "final_resume" if approval_id else None,
        "doc_url": outcome.doc_url or "",
        "diff_path": str(outcome.diff_path) if outcome.diff_path else None,
        "application_id": outcome.application_id,
        "fit_score": evaluation.score if evaluation else None,
        "should_apply": evaluation.should_apply if evaluation else None,
        "strategy_brief": serialize_strategy_brief(outcome.strategy_brief),
        "strategy_brief_id": outcome.strategy_brief_id,
        "gating_decision": getattr(outcome.strategy_brief, "gating_decision", None),
        "usage": outcome.usage,
        "steps": outcome.steps,
    }


def _serialize_strategy_detail(user_id: int, brief_id: int) -> Dict[str, Any]:
    brief = get_job_strategy_brief_for_user(user_id, brief_id)
    if not brief:
        raise HTTPException(status_code=404, detail="Strategy brief not found")
    from resume_agent.models.agent_models import JobStrategyBrief
    return {
        "strategy_brief": serialize_strategy_brief(JobStrategyBrief(**brief)),
        "events": list_job_strategy_events_for_user(user_id, brief_id),
    }


def _merge_usage(*usages: dict) -> dict:
    """Sum the usage dicts from several pipeline runs into one figure."""
    total: dict = {}
    for usage in usages:
        for key, value in (usage or {}).items():
            if key == "by_model":
                models = dict(total.get("by_model") or {})
                for model, count in (value or {}).items():
                    models[model] = models.get(model, 0) + count
                total["by_model"] = models
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                total[key] = (total.get(key) or 0) + value
    return total


def _evaluate_or_400(orchestrator, fit_request):
    """Run a fit evaluation, mapping pipeline failures onto HTTP status codes."""
    try:
        return orchestrator.evaluate_fit(fit_request)
    except ResumeUnavailable as e:
        raise HTTPException(status_code=FIT_ERROR_STATUS.get(e.code, 500), detail=str(e))
    except PipelineError as e:
        raise HTTPException(status_code=FIT_ERROR_STATUS.get(e.code, 500), detail=str(e))


FIT_ERROR_STATUS = {
    "no_google_session": 401,
    "no_resume_configured": 400,
    "resume_forbidden": 403,
    "no_jd_text": 400,
    "resume_unreadable": 500,
    "failed": 500,
}


@app.post("/api/evaluate-fit")
async def evaluate_fit(request: EvaluateFitRequest, http_request: Request):
    """Evaluate job fit for the current page (e.g. from Chrome extension). Returns score and recommendations."""
    if not request.job_url and not request.jd_text:
        raise HTTPException(status_code=400, detail="Provide job_url or jd_text")
    try:
        llm_service = get_llm_service()
        google_services = get_google_services_from_request(http_request)
        from resume_agent.config import settings

        doc_ids = normalize_doc_ids([
            request.resume_doc_id,
            get_preferred_resume_doc_id(http_request),
            settings.resume_doc_id,
        ])
        resume_text = load_first_readable(google_services, doc_ids)

        # Prefer text the caller already has: re-extracting from the URL costs a
        # fetch that job boards frequently block, and it can only lose detail
        # against a description the caller scraped or stored itself.
        jd_text = (request.jd_text or "").strip()
        if not jd_text and request.job_url:
            from resume_agent.agents.jd_extractor import extract_clean_jd
            jd_text = extract_clean_jd(request.job_url, llm_service)
        if not jd_text:
            raise HTTPException(status_code=400, detail="Could not get job description from URL or jd_text.")

        outcome = ResumeOrchestrator(
            llm_service=llm_service, google_services=google_services
        ).evaluate_fit(
            FitRequest(
                jd_text=jd_text,
                job_url=request.job_url,
                resume_text=resume_text,
                local_user_id=(
                    get_local_user_from_request(http_request).get("id")
                    if get_session_data(http_request)
                    else None
                ),
            )
        )
        return {
            "success": True,
            **serialize_evaluation(outcome.evaluation),
            "usage": outcome.usage,
        }
    except HTTPException:
        raise
    except ResumeUnavailable as e:
        raise HTTPException(status_code=FIT_ERROR_STATUS.get(e.code, 500), detail=str(e))
    except PipelineError as e:
        raise HTTPException(status_code=FIT_ERROR_STATUS.get(e.code, 500), detail=str(e))
    except Exception as e:
        logger.error(f"Evaluate fit failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/job-strategy/evaluate")
async def evaluate_job_strategy(request: JobStrategyEvaluateRequest, http_request: Request):
    """Create or reuse a persisted strategy brief for the authenticated user."""
    local_user = get_local_user_from_request(http_request)
    orchestrator = ResumeOrchestrator(
        llm_service=get_llm_service(),
        google_services=get_google_services_from_request(http_request),
    )
    tailor_request = TailorRequest(
        company=request.company or "",
        job_title=request.job_title or "",
        jd_text=request.jd_text,
        job_url=request.job_url,
        resume_doc_id=request.resume_doc_id,
        local_user_id=local_user["id"],
    )

    try:
        fit = orchestrator.evaluate_fit(tailor_request)
        # Reuses the evaluation just produced instead of re-parsing and
        # re-judging to build the brief.
        outcome = orchestrator.build_strategy(tailor_request, prior=fit)
    except ResumeUnavailable as e:
        raise HTTPException(status_code=FIT_ERROR_STATUS.get(e.code, 500), detail=str(e))
    except PipelineError as e:
        raise HTTPException(status_code=FIT_ERROR_STATUS.get(e.code, 500), detail=str(e))

    add_job_strategy_event_for_user(
        local_user["id"],
        strategy_brief_id=outcome.strategy_brief_id,
        event_type="strategy_brief_evaluated",
        payload={"gating_decision": outcome.strategy_brief.gating_decision},
    )
    return {
        **_serialize_strategy_detail(local_user["id"], outcome.strategy_brief_id),
        "evaluation": serialize_evaluation(fit.evaluation),
        "usage": _merge_usage(fit.usage, outcome.usage),
    }


@app.get("/api/job-strategy")
async def list_job_strategies(http_request: Request, limit: int = 50):
    local_user = get_local_user_from_request(http_request)
    from resume_agent.models.agent_models import JobStrategyBrief

    briefs = [
        serialize_strategy_brief(JobStrategyBrief(**brief))
        for brief in list_job_strategy_briefs_for_user(local_user["id"], limit=limit)
    ]
    return {"strategy_briefs": briefs}


@app.get("/api/job-strategy/{brief_id}")
async def get_job_strategy(brief_id: int, http_request: Request):
    local_user = get_local_user_from_request(http_request)
    return _serialize_strategy_detail(local_user["id"], brief_id)


@app.post("/api/job-strategy/{brief_id}/approve")
async def approve_job_strategy(brief_id: int, request: StrategyDecisionRequest, http_request: Request):
    local_user = get_local_user_from_request(http_request)
    updated = update_job_strategy_brief_status_for_user(local_user["id"], brief_id, "approved")
    if not updated:
        raise HTTPException(status_code=404, detail="Strategy brief not found")
    add_job_strategy_event_for_user(
        local_user["id"],
        strategy_brief_id=brief_id,
        event_type="strategy_approved",
        payload={"reason": request.reason},
    )
    return _serialize_strategy_detail(local_user["id"], brief_id)


@app.post("/api/job-strategy/{brief_id}/override")
async def override_job_strategy(brief_id: int, request: StrategyDecisionRequest, http_request: Request):
    local_user = get_local_user_from_request(http_request)
    updated = update_job_strategy_brief_status_for_user(local_user["id"], brief_id, "override_approved")
    if not updated:
        raise HTTPException(status_code=404, detail="Strategy brief not found")
    add_job_strategy_event_for_user(
        local_user["id"],
        strategy_brief_id=brief_id,
        event_type="strategy_override_approved",
        payload={"reason": request.reason},
    )
    return _serialize_strategy_detail(local_user["id"], brief_id)


@app.post("/api/job-strategy/{brief_id}/duplicate")
async def duplicate_job_strategy(brief_id: int, http_request: Request):
    local_user = get_local_user_from_request(http_request)
    stored = get_job_strategy_brief_for_user(local_user["id"], brief_id)
    if not stored:
        raise HTTPException(status_code=404, detail="Strategy brief not found")
    cloned = dict(stored)
    cloned.pop("id", None)
    cloned["approval_status"] = "pending"
    cloned["created_at"] = None
    cloned["updated_at"] = None
    cloned["role_summary"] = cloned.get("role_summary") or "Duplicated strategy brief pending review."
    from resume_agent.models.agent_models import JobStrategyBrief

    duplicated = ResumeOrchestrator(
        google_services=get_google_services_from_request(http_request),
    ).services.agent("strategy_brief").persist_brief(
        local_user["id"],
        JobStrategyBrief(**cloned),
    )
    add_job_strategy_event_for_user(
        local_user["id"],
        strategy_brief_id=duplicated.id,
        event_type="strategy_duplicated",
        payload={"source_brief_id": brief_id},
    )
    return _serialize_strategy_detail(local_user["id"], duplicated.id)


@app.post("/api/job-strategy/{brief_id}/tailor")
async def tailor_from_job_strategy(brief_id: int, request: JobStrategyTailorRequest, http_request: Request):
    local_user = get_local_user_from_request(http_request)
    stored = get_job_strategy_brief_for_user(local_user["id"], brief_id)
    if not stored:
        raise HTTPException(status_code=404, detail="Strategy brief not found")
    from resume_agent.models.agent_models import JobStrategyBrief

    strategy_brief = JobStrategyBrief(**stored)
    if strategy_brief.approval_status not in {"approved", "override_approved"}:
        raise HTTPException(status_code=400, detail="Strategy brief must be approved before tailoring")

    tailor_request = TailorRequest(
        company=strategy_brief.company,
        job_title=strategy_brief.job_title,
        jd_text=request.jd_text or strategy_brief.jd_text or "",
        job_url=strategy_brief.job_url,
        resume_doc_id=request.resume_doc_id,
        local_user_id=local_user["id"],
        intensity=request.tailoring_intensity,
        preserve_sections=request.preserve_sections,
        protected_entry_texts=request.protected_entry_texts,
        strategy_brief_id=strategy_brief.id,
        strategy_approved=True,
    )
    if not tailor_request.jd_text:
        raise HTTPException(status_code=400, detail="Saved strategy brief is missing canonical JD text")

    try:
        outcome = ResumeOrchestrator(
            llm_service=get_llm_service(),
            google_services=get_google_services_from_request(http_request),
        ).tailor(tailor_request, strategy_brief=strategy_brief)
    except ResumeUnavailable as e:
        raise HTTPException(status_code=FIT_ERROR_STATUS.get(e.code, 500), detail=str(e))
    except PipelineError as e:
        raise HTTPException(status_code=FIT_ERROR_STATUS.get(e.code, 500), detail=str(e))

    add_job_strategy_event_for_user(
        local_user["id"],
        strategy_brief_id=brief_id,
        event_type="strategy_tailored",
        payload={"quality_score": getattr(outcome.validation, "quality_score", None)},
    )
    return {"result": serialize_tailor_outcome(outcome)}


@app.post("/api/job-strategy/{brief_id}/regenerate-section")
async def regenerate_job_strategy_section(brief_id: int, request: JobStrategyRegenerateRequest, http_request: Request):
    local_user = get_local_user_from_request(http_request)
    stored = get_job_strategy_brief_for_user(local_user["id"], brief_id)
    if not stored:
        raise HTTPException(status_code=404, detail="Strategy brief not found")
    from resume_agent.models.agent_models import JobStrategyBrief

    brief = JobStrategyBrief(**stored)
    jd_text = request.jd_text or brief.jd_text or ""
    if not jd_text:
        raise HTTPException(status_code=400, detail="Saved strategy brief is missing canonical JD text")

    orchestrator = ResumeOrchestrator(
        llm_service=get_llm_service(),
        google_services=get_google_services_from_request(http_request),
    )
    fit = _evaluate_or_400(orchestrator, FitRequest(
        company=brief.company,
        job_title=brief.job_title,
        jd_text=jd_text,
        job_url=brief.job_url,
        resume_doc_id=request.resume_doc_id,
        local_user_id=local_user["id"],
    ))

    strategy_service = orchestrator.services.agent("strategy_brief")
    regenerated = strategy_service.regenerate_section(
        brief=brief,
        section=request.section,
        parsed_resume=fit.parsed_resume,
        analyzed_jd=fit.analyzed_jd,
        fit_evaluation=fit.evaluation,
        profile_context=fit.profile_context,
    )
    regenerated = strategy_service.persist_brief(local_user["id"], regenerated)
    add_job_strategy_event_for_user(
        local_user["id"],
        strategy_brief_id=brief_id,
        event_type="strategy_section_regenerated",
        payload={"section": request.section},
    )
    return _serialize_strategy_detail(local_user["id"], regenerated.id)


@app.post("/api/job-strategy/{brief_id}/rebaseline")
async def rebaseline_job_strategy(brief_id: int, request: JobStrategyRebaselineRequest, http_request: Request):
    local_user = get_local_user_from_request(http_request)
    stored = get_job_strategy_brief_for_user(local_user["id"], brief_id)
    if not stored:
        raise HTTPException(status_code=404, detail="Strategy brief not found")
    from resume_agent.models.agent_models import JobStrategyBrief

    existing = JobStrategyBrief(**stored)
    fit_request = FitRequest(
        company=request.company or existing.company,
        job_title=request.job_title or existing.job_title,
        jd_text=request.jd_text or existing.jd_text or "",
        job_url=request.job_url or existing.job_url,
        resume_doc_id=request.resume_doc_id,
        local_user_id=local_user["id"],
    )
    if not fit_request.jd_text:
        raise HTTPException(status_code=400, detail="Strategy brief is missing canonical JD text")

    orchestrator = ResumeOrchestrator(
        llm_service=get_llm_service(),
        google_services=get_google_services_from_request(http_request),
    )
    fit = _evaluate_or_400(orchestrator, fit_request)

    rebuilt = orchestrator.services.agent("strategy_brief").build_brief(
        company=fit_request.company,
        job_title=fit_request.job_title,
        job_url=fit_request.job_url,
        jd_text=fit_request.jd_text,
        parsed_resume=fit.parsed_resume,
        analyzed_jd=fit.analyzed_jd,
        fit_evaluation=fit.evaluation,
        profile_context=fit.profile_context,
    )
    rebuilt.id = existing.id
    rebuilt.approval_status = "pending"
    rebuilt.created_at = existing.created_at
    rebuilt = orchestrator.services.agent("strategy_brief").persist_brief(local_user["id"], rebuilt)
    add_job_strategy_event_for_user(
        local_user["id"],
        strategy_brief_id=brief_id,
        event_type="strategy_rebaselined",
        payload={
            "company": rebuilt.company,
            "job_title": rebuilt.job_title,
            "jd_length": len(rebuilt.jd_text or ""),
        },
    )
    return _serialize_strategy_detail(local_user["id"], rebuilt.id)


@app.post("/api/extract-jd")
async def extract_jd(request: ExtractJDRequest):
    """Extract job description from URL"""
    try:
        from resume_agent.agents.jd_extractor import extract_clean_jd
        
        llm_service = get_llm_service()
        jd_text = extract_clean_jd(request.job_url, llm_service)
        
        return {
            "success": True,
            "jd_text": jd_text
        }
    except Exception as e:
        logger.error(f"Error extracting JD: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/analyze-resume-quality")
async def analyze_resume_quality(request: QualityAnalysisRequest, http_request: Request):
    """Analyze resume quality and optionally improve it"""
    try:
        from resume_agent.agents.resume_quality_agent import ResumeQualityAgent
        
        llm_service = get_llm_service()
        quality_agent = ResumeQualityAgent(llm_service)
        
        # Get resume text
        resume_text = request.resume_text
        
        if not resume_text and request.resume_doc_id:
            google_services = get_google_services_from_request(http_request)
            if google_services:
                drive_service, docs_service = google_services
                resume_text = read_resume_file(drive_service, docs_service, extract_google_doc_id(request.resume_doc_id))
        
        if not resume_text:
            raise HTTPException(status_code=400, detail="No resume text provided")
        
        # Analyze quality
        quality_report = quality_agent.analyze_quality(resume_text)
        
        # Store user answers for later realism/metric validation
        if request.user_answers:
            from resume_agent.storage.user_memory import save_user_metric_answers
            save_user_metric_answers(request.user_answers)

        # Convert to dict for JSON response
        result = {
            "overall_score": quality_report.overall_score,
            "ats_score": quality_report.ats_score,
            "metrics_count": quality_report.metrics_count,
            "subscores": quality_report.subscores,
            "top_driver": quality_report.top_driver,
            "best_next_fix": quality_report.best_next_fix,
            "issues": [
                    {
                        "id": issue.id,
                        "category": issue.category.value,
                        "severity": issue.severity.value,
                        "section": issue.section,
                        "issue": issue.issue,
                        "suggestion": issue.suggestion,
                        "example": issue.example,
                        "research_note": getattr(issue, 'research_note', None),
                        "target_text": getattr(issue, 'target_text', None),
                        "target_entry_id": getattr(issue, 'target_entry_id', None),
                        "requires_user_input": getattr(issue, "requires_user_input", False),
                        "blocked_reason": getattr(issue, "blocked_reason", None),
                        "advisory_only": getattr(issue, "advisory_only", False),
                        "score_component": getattr(issue, "score_component", None),
                        "impact_level": getattr(issue, "impact_level", None),
                        "proposed_fix": getattr(issue, 'proposed_fix', None),
                    }
                for issue in quality_report.issues
            ],
            "strengths": quality_report.strengths,
            "improvement_priority": quality_report.improvement_priority,
            "estimated_impact": quality_report.estimated_impact,
            # Include clarifying questions for user to answer before improving
            "questions": [
                {
                    "id": q.id,
                    "question": q.question,
                    "context": q.context,
                    "options": q.options,
                    "required": q.required
                }
                for q in quality_report.questions
            ] if quality_report.questions else []
        }

        # Cache the quality report for future tailoring decisions
        cache_doc_id = request.resume_doc_id or "latest"
        report_payload = {
            "overall_score": quality_report.overall_score,
            "ats_score": quality_report.ats_score,
            "metrics_count": quality_report.metrics_count,
            "improvement_priority": quality_report.improvement_priority,
            "estimated_impact": quality_report.estimated_impact
        }
        try:
            local_user = get_local_user_from_request(http_request)
            save_quality_report_for_user(int(local_user["id"]), cache_doc_id, report_payload)
        except HTTPException:
            from resume_agent.storage.user_memory import save_quality_report
            save_quality_report(doc_id=cache_doc_id, report=report_payload)
        
        # Optionally improve (using user answers if provided), with auto-retry if score didn't improve
        if request.improve:
            min_improvement = 3  # Retry if score didn't go up by at least this much
            improved = quality_agent.improve_resume(
                resume_text,
                quality_report,
                user_answers=request.user_answers,
                issue_resolutions=request.issue_resolutions or {},
            )
            if improved.after_score <= improved.before_score + min_improvement:
                logger.info(
                    "Improvement negligible or none, retrying once",
                    before=improved.before_score,
                    after=improved.after_score
                )
                retry_result = quality_agent.improve_resume(
                    resume_text,
                    quality_report,
                    user_answers=request.user_answers,
                    issue_resolutions=request.issue_resolutions or {},
                )
                if retry_result.after_score > improved.after_score:
                    improved = retry_result
                    result["retried"] = True
            if improved.after_report:
                result["overall_score"] = improved.after_report.overall_score
                result["ats_score"] = improved.after_report.ats_score
                result["metrics_count"] = improved.after_report.metrics_count
                result["subscores"] = improved.after_report.subscores
                result["top_driver"] = improved.after_report.top_driver
                result["best_next_fix"] = improved.after_report.best_next_fix
                result["improvement_priority"] = improved.after_report.improvement_priority
                result["estimated_impact"] = improved.after_report.estimated_impact
                result["issues"] = [
                    {
                        "id": issue.id,
                        "category": issue.category.value,
                        "severity": issue.severity.value,
                        "section": issue.section,
                        "issue": issue.issue,
                        "suggestion": issue.suggestion,
                        "example": issue.example,
                        "research_note": issue.research_note,
                        "target_text": issue.target_text,
                        "target_entry_id": getattr(issue, "target_entry_id", None),
                        "requires_user_input": getattr(issue, "requires_user_input", False),
                        "blocked_reason": getattr(issue, "blocked_reason", None),
                        "advisory_only": getattr(issue, "advisory_only", False),
                        "score_component": getattr(issue, "score_component", None),
                        "impact_level": getattr(issue, "impact_level", None),
                        "proposed_fix": issue.proposed_fix,
                    }
                    for issue in improved.after_report.issues
                ]
                result["strengths"] = improved.after_report.strengths
                result["questions"] = [
                    {
                        "id": q.id,
                        "question": q.question,
                        "context": q.context,
                        "options": q.options,
                        "required": q.required,
                    }
                    for q in improved.after_report.questions
                ]
            result["improved_resume"] = improved.improved_text
            result["changes_made"] = improved.changes_made
            result["before_score"] = improved.before_score
            result["after_score"] = improved.after_score
            result["metrics_added"] = improved.metrics_added
            result["improvement_accepted"] = improved.accepted
            result["quality_decreased"] = improved.score_regressed
            result["quality_debug"] = improved.diagnostics
            try:
                local_user = get_local_user_from_request(http_request)
                cache_result = save_improved_resume_for_user(
                    int(local_user["id"]),
                    improved.improved_text,
                    original_doc_id=request.resume_doc_id,
                    score=improved.after_score,
                    metadata={"changes_made": improved.changes_made}
                )
            except HTTPException:
                from resume_agent.storage.user_memory import save_improved_resume
                cache_result = save_improved_resume(
                    resume_text=improved.improved_text,
                    original_doc_id=request.resume_doc_id,
                    score=improved.after_score,
                    metadata={"changes_made": improved.changes_made}
                )
            result["cached_version"] = cache_result.get("version", 1)

        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error analyzing resume quality: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


class SaveImprovedResumeRequest(BaseModel):
    """Request to save improved resume to Google Drive"""
    resume_text: str
    folder_id: str
    filename: Optional[str] = None


@app.get("/api/cached-improved-resume")
async def get_cached_improved_resume(http_request: Request, doc_id: Optional[str] = None):
    """Get cached improved resume"""
    try:
        try:
            local_user = get_local_user_from_request(http_request)
            cached = get_improved_resume_for_user(int(local_user["id"]), doc_id)
        except HTTPException:
            from resume_agent.storage.user_memory import get_improved_resume
            cached = get_improved_resume(doc_id)
        
        if not cached:
            return {"found": False, "resume": None}
        
        return {
            "found": True,
            "resume": {
                "text": cached.get("text"),
                "score": cached.get("score"),
                "original_doc_id": cached.get("original_doc_id"),
                "metadata": cached.get("metadata"),
                "updated_at": cached.get("updated_at"),
                "version": cached.get("version")
            }
        }
    except Exception as e:
        logger.error(f"Error getting cached resume: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/cached-improved-resume")
async def clear_cached_improved_resume(http_request: Request, doc_id: Optional[str] = None):
    """Clear cached improved resume(s)"""
    try:
        try:
            local_user = get_local_user_from_request(http_request)
            clear_improved_resume_for_user(int(local_user["id"]), doc_id)
        except HTTPException:
            from resume_agent.storage.user_memory import clear_improved_resume
            clear_improved_resume(doc_id)
        return {"success": True}
    except Exception as e:
        logger.error(f"Error clearing cached resume: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


def convert_markdown_to_docs_requests(text: str):
    """
    Convert markdown text to Google Docs API requests.
    Returns (plain_text, formatting_requests) where formatting_requests
    apply bold to **text** sections.
    """
    import re
    
    # First, convert dashes at start of lines to bullet points
    text = re.sub(r'^- ', '• ', text, flags=re.MULTILINE)
    text = re.sub(r'^\s+- ', '  • ', text, flags=re.MULTILINE)  # Indented bullets
    
    # Find all **bold** sections and their positions
    bold_pattern = r'\*\*(.+?)\*\*'
    
    # Build plain text (without markdown markers) and track bold ranges
    plain_text = ""
    bold_ranges = []
    last_end = 0
    
    for match in re.finditer(bold_pattern, text):
        # Add text before this match
        plain_text += text[last_end:match.start()]
        
        # Track where bold text starts (in plain text, +1 for Google Docs index)
        bold_start = len(plain_text) + 1
        
        # Add the bold text (without **)
        bold_content = match.group(1)
        plain_text += bold_content
        
        # Track where bold text ends
        bold_end = len(plain_text) + 1
        
        bold_ranges.append((bold_start, bold_end))
        last_end = match.end()
    
    # Add remaining text
    plain_text += text[last_end:]
    
    # Create formatting requests for bold ranges
    formatting_requests = []
    for start, end in bold_ranges:
        formatting_requests.append({
            'updateTextStyle': {
                'range': {
                    'startIndex': start,
                    'endIndex': end
                },
                'textStyle': {
                    'bold': True
                },
                'fields': 'bold'
            }
        })
    
    return plain_text, formatting_requests


@app.post("/api/save-improved-resume")
async def save_improved_resume(request: SaveImprovedResumeRequest, http_request: Request):
    """Save improved resume to Google Drive as a new document with proper formatting"""
    try:
        google_services = get_google_services_from_request(http_request)
        if not google_services:
            raise HTTPException(status_code=401, detail="Not authenticated with Google")
        
        drive_service, docs_service = google_services
        
        # Generate filename if not provided
        from datetime import datetime
        filename = request.filename or f"Improved_Resume_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        # Create a new Google Doc
        doc_metadata = {
            'name': filename,
            'mimeType': 'application/vnd.google-apps.document',
        }
        
        # Add parent folder (skip if 'root' to save to Drive root)
        if request.folder_id and request.folder_id != 'root':
            doc_metadata['parents'] = [request.folder_id]
        
        doc = drive_service.files().create(body=doc_metadata).execute()
        doc_id = doc.get('id')
        
        # Convert markdown to plain text + formatting requests
        plain_text, formatting_requests = convert_markdown_to_docs_requests(request.resume_text)
        
        # First, insert the plain text
        insert_requests = [{
            'insertText': {
                'location': {'index': 1},
                'text': plain_text
            }
        }]
        
        docs_service.documents().batchUpdate(
            documentId=doc_id,
            body={'requests': insert_requests}
        ).execute()
        
        # Then apply formatting (bold) if any
        if formatting_requests:
            docs_service.documents().batchUpdate(
                documentId=doc_id,
                body={'requests': formatting_requests}
            ).execute()
        
        logger.info(f"Saved improved resume to Google Drive: {filename}")
        
        return {
            "success": True,
            "doc_id": doc_id,
            "filename": filename,
            "doc_url": f"https://docs.google.com/document/d/{doc_id}/edit"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error saving improved resume: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


class UpdateResumeDocRequest(BaseModel):
    """Request to write improved content into the selected Google Doc (in-place)"""
    doc_id: str
    resume_text: str


@app.post("/api/update-resume-doc")
async def update_resume_doc(request: UpdateResumeDocRequest, http_request: Request):
    """Update the selected Google Doc in place with improved resume content. Only works for Google Docs (not PDFs)."""
    try:
        google_services = get_google_services_from_request(http_request)
        if not google_services:
            raise HTTPException(status_code=401, detail="Not authenticated with Google")
        drive_service, docs_service = google_services
        doc_id = extract_google_doc_id(request.doc_id)
        meta = get_file_metadata(drive_service, doc_id)
        if not meta:
            raise HTTPException(status_code=404, detail="Document not found")
        if meta.get("mimeType") != GOOGLE_DOC_MIME:
            raise HTTPException(
                status_code=400,
                detail="Only Google Docs can be updated in place. PDFs: use Save to Google Drive to create a new Doc."
            )
        write_to_google_doc(doc_id, request.resume_text)
        logger.info("Updated resume doc in place", doc_id=doc_id)
        return {
            "success": True,
            "doc_id": doc_id,
            "doc_url": f"https://docs.google.com/document/d/{doc_id}/edit"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating resume doc: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/resume/{doc_id}")
async def get_resume_content(doc_id: str, request: Request):
    """
    Fetch the content of a resume file (Google Doc or PDF).
    """
    try:
        google_services = get_google_services_from_request(request)
        if not google_services:
            raise HTTPException(status_code=401, detail="Google services not available. Please authenticate with Google.")
        drive_service, docs_service = google_services
        doc_id = extract_google_doc_id(doc_id)
        meta = get_file_metadata(drive_service, doc_id)
        if not meta:
            raise HTTPException(status_code=404, detail="Resume file not found or inaccessible.")
        resume_content = read_resume_file(drive_service, docs_service, doc_id, meta.get("mimeType"))
        return {
            "success": True,
            "resume_content": resume_content,
            "resume_text": resume_content,
        }
    except GoogleAPIError as e:
        logger.error(f"Google API error fetching resume {doc_id}: {e}", exc_info=True)
        raise HTTPException(status_code=e.status_code or 500, detail=e.message)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching resume {doc_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/resume/extract-skills")
async def extract_skills_from_resume(request: Request):
    """Extract skills from a resume document"""
    try:
        body = await request.json()
        resume_text = body.get('resume_text', '').strip()
        doc_id = extract_google_doc_id(body.get('doc_id'))  # Optional: if provided, fetch from Google Docs
        
        if not resume_text and not doc_id:
            raise HTTPException(status_code=400, detail="Either resume_text or doc_id is required")
        
        # If doc_id provided, fetch resume content (Google Doc or PDF)
        if doc_id and not resume_text:
            google_services = get_google_services_from_request(request)
            if not google_services:
                raise HTTPException(status_code=401, detail="Google services not available. Please authenticate with Google.")
            drive_service, docs_service = google_services
            resume_text = read_resume_file(drive_service, docs_service, doc_id)
        
        if not resume_text:
            raise HTTPException(status_code=400, detail="Resume text is empty")
        
        # Extract skills using LLM
        llm_service = get_llm_service()
        from resume_agent.agents.skill_extractor import extract_skills_from_resume, extract_experience_info
        
        skills_result = extract_skills_from_resume(llm_service, resume_text)
        experience_result = extract_experience_info(llm_service, resume_text)
        
        # Store extracted skills in user memory (but don't confirm yet - user needs to review)
        from resume_agent.storage.user_memory import load_memory, save_memory
        memory = load_memory()
        memory["extracted_skills"] = skills_result["all_skills"]
        memory["extracted_experience"] = experience_result
        save_memory(memory)
        
        return {
            "success": True,
            "skills": skills_result,
            "experience": experience_result,
            "message": "Skills extracted successfully. Please review and confirm."
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error extracting skills: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/user/profile/bootstrap")
async def bootstrap_user_profile(request: Request):
    """Parse the selected resume and create detected/suggested skill scaffolding for onboarding."""
    try:
        body = await request.json()
        resume_text = body.get("resume_text", "").strip()
        doc_id = extract_google_doc_id(body.get("doc_id"))

        if not resume_text and not doc_id:
            raise HTTPException(status_code=400, detail="Either resume_text or doc_id is required")

        local_user = get_local_user_from_request(request)

        if doc_id and not resume_text:
            google_services = get_google_services_from_request(request)
            if not google_services:
                raise HTTPException(status_code=401, detail="Google services not available. Please authenticate with Google.")
            drive_service, docs_service = google_services
            resume_text = read_resume_file(drive_service, docs_service, doc_id)

        if not resume_text:
            raise HTTPException(status_code=400, detail="Resume text is empty")

        llm_service = get_llm_service()
        from resume_agent.agents.skill_extractor import extract_experience_info, extract_skills_from_resume
        from resume_agent.agents.skill_recommender import build_skill_records, recommend_profile_skills

        skills_result = extract_skills_from_resume(llm_service, resume_text)
        experience_result = extract_experience_info(llm_service, resume_text)

        detected_records = build_skill_records(
            skills_result.get("categorized", {}),
            skills_result.get("all_skills", []),
            confidence=0.9,
        )
        suggested_records = recommend_profile_skills(
            detected_skills=skills_result.get("all_skills", []),
            confirmed_skills=[],
            job_titles=experience_result.get("job_titles", []),
            total_years=experience_result.get("total_years"),
        )

        replace_user_skill_records(
            local_user["id"],
            detected_records,
            state="detected",
            source="resume_parse",
        )
        replace_user_skill_records(
            local_user["id"],
            suggested_records,
            state="suggested",
            source="role_inference",
        )

        return {
            "success": True,
            "detected_skills": get_user_skill_records(local_user["id"], state="detected"),
            "suggested_skills": get_user_skill_records(local_user["id"], state="suggested"),
            "experience": experience_result,
            "categorized": skills_result.get("categorized", {}),
            "message": "Profile bootstrap complete. Review detected and suggested skills.",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error bootstrapping user profile: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/tailor-resume")
async def tailor_resume_stream(request: TailorResumeAPIRequest, http_request: Request):
    """
    Tailor resume with streaming progress updates via Server-Sent Events.
    
    Returns a stream of events:
    - step_start: Step is starting
    - step_progress: Step is in progress
    - step_complete: Step completed
    - complete: All steps done, final result
    - error: Error occurred
    """
    # Human-readable labels for the pipeline's own step names. The pipeline is
    # the source of truth for what runs; this only decides how it reads.
    STEP_LABELS = {
        "load_profile": "Loading your profile...",
        "load_resume": "Loading resume...",
        "understand_inputs": "Parsing resume and analyzing job description...",
        "evaluate_fit": "Evaluating job fit...",
        "build_strategy": "Building job strategy brief...",
        "tailor_resume": "Tailoring resume...",
        "humanize": "Refining tone...",
        "score_ats": "Calculating ATS score...",
        "validate": "Checking the draft against your resume...",
        "save_to_google": "Saving to Google Docs...",
        "generate_diff": "Generating change log...",
        "track_application": "Tracking application...",
    }

    async def generate_progress():
        loop = asyncio.get_running_loop()
        events: asyncio.Queue = asyncio.Queue()

        def emit(payload: dict) -> None:
            """Called from the pipeline thread; hands the event to the loop."""
            loop.call_soon_threadsafe(events.put_nowait, payload)

        # The pipelines are the source of truth for how many steps there are, so
        # the progress bar cannot drift from what actually runs.
        from resume_agent.pipelines.definitions import build_fit_pipeline, build_tailor_pipeline

        planned = (
            [step.name for step in build_fit_pipeline().steps]
            if request.evaluate_only
            else [step.name for step in build_tailor_pipeline().steps]
        )
        total_steps = len(planned)
        seen: list = []

        def progress(step_name: str) -> None:
            if step_name not in seen:
                seen.append(step_name)
            index = seen.index(step_name)
            emit({
                "type": "step_start",
                "step": step_name,
                "message": STEP_LABELS.get(step_name, step_name),
                "step_number": index + 1,
                "total_steps": total_steps,
                "progress": index / total_steps,
            })

        def run_pipeline():
            """The whole run, off the event loop."""
            orchestrator = ResumeOrchestrator(
                llm_service=get_llm_service(),
                google_services=get_google_services_from_request(http_request),
            )
            local_user_id = (
                get_local_user_from_request(http_request).get("id")
                if get_session_data(http_request)
                else None
            )
            common = dict(
                company=request.company or "",
                job_title=request.job_title or "",
                jd_text=request.jd_text,
                job_url=request.job_url,
                resume_doc_id=request.resume_doc_id,
                local_user_id=local_user_id,
                discovered_role_id=request.discovered_role_id,
            )

            fit = orchestrator.evaluate_fit(FitRequest(**common), progress=progress)
            emit({"type": "evaluation", "evaluation": serialize_evaluation(fit.evaluation)})

            if request.evaluate_only:
                return orchestrator, None, fit, None

            tailor_request = TailorRequest(
                **common,
                intensity=request.tailoring_intensity,
                sections_to_tailor=request.sections_to_tailor,
                preserve_sections=request.preserve_sections,
                protected_entry_texts=request.protected_entry_texts,
                target_entry_text=request.target_entry_text,
                refinement_feedback=request.refinement_feedback,
                revert_target_entry=request.revert_target_entry,
                save_folder_id=request.save_folder_id,
                track_application=(request.track_application and not request.discovered_role_id),
            )
            outcome = orchestrator.tailor(tailor_request, prior=fit, progress=progress)
            return orchestrator, tailor_request, fit, outcome

        task = asyncio.create_task(asyncio.to_thread(run_pipeline))

        try:
            # Stream progress until the run finishes, then drain what is left.
            while not task.done() or not events.empty():
                try:
                    payload = await asyncio.wait_for(events.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    continue
                yield f"data: {json.dumps(payload)}\n\n"

            orchestrator, tailor_request, fit, outcome = await task
        except ResumeUnavailable as e:
            yield f"data: {json.dumps({'type': 'error', 'code': e.code, 'error': str(e)})}\n\n"
            return
        except PipelineError as e:
            yield f"data: {json.dumps({'type': 'error', 'step': e.step, 'code': e.code, 'error': str(e)})}\n\n"
            return
        except Exception as e:
            logger.error(f"Error in tailor_resume_stream: {e}", exc_info=True)
            yield f"data: {json.dumps({'type': 'error', 'error': str(e), 'traceback': traceback.format_exc()})}\n\n"
            return

        if outcome is None:
            payload = {
                "type": "complete",
                "progress": 1.0,
                "result": {
                    "evaluation": serialize_evaluation(fit.evaluation),
                    "fit_score": fit.evaluation.score,
                    "should_apply": fit.evaluation.should_apply,
                    "usage": fit.usage,
                    "steps": fit.steps,
                },
            }
            yield f"data: {json.dumps(payload)}\n\n"
            return

        # The draft is ready; the user approves it before anything is saved.
        import uuid

        approval_id = str(uuid.uuid4())
        approval_storage.store(approval_id, ApprovedDraft(
            request=tailor_request,
            outcome=outcome,
            fit=fit,
        ))

        fit_warning = None
        evaluation = outcome.evaluation or fit.evaluation
        if evaluation and (evaluation.score < 5 or not evaluation.should_apply):
            fit_warning = {
                "score": evaluation.score,
                "should_apply": evaluation.should_apply,
                "message": (
                    f"Low fit score ({evaluation.score}/10). This role may not be a good match. "
                    "Explicit approval is required to continue."
                ),
                "missing_areas": evaluation.missing_areas or [],
            }

        payload = {
            "type": "approval_required",
            "approval_id": approval_id,
            "approval_stage": "final_resume",
            "message": "Resume draft ready. Review and approve before saving and tracking.",
            "fit_warning": fit_warning,
            "step_number": total_steps,
            "total_steps": total_steps,
            "progress": 1.0,
            "result": serialize_tailor_outcome(outcome, approval_id),
        }
        yield f"data: {json.dumps(payload)}\n\n"

    return StreamingResponse(
        generate_progress(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@app.post("/api/approve-resume")
async def approve_resume(request: ApprovalRequest, http_request: Request):
    """Approve or reject a tailored draft. Approving saves, diffs and tracks it."""
    draft = approval_storage.get(request.approval_id)
    if draft is None:
        logger.warning(
            "Approval not found for approve-resume (may be expired or server was restarted)",
            approval_id_prefix=request.approval_id[:8] if request.approval_id else None,
        )
        raise HTTPException(
            status_code=404,
            detail=(
                "Approval request not found or expired. "
                "If the server was restarted, run Tailor again and approve within the same session."
            ),
        )

    local_user_id = (
        get_local_user_from_request(http_request).get("id") if get_session_data(http_request) else None
    )

    if not request.approved:
        approval_storage.delete(request.approval_id)
        return {
            "success": True,
            "approved": False,
            "approval_stage": "final_resume",
            "message": "Resume tailoring rejected",
        }

    outcome = draft.outcome
    if local_user_id and outcome.strategy_brief_id:
        add_job_strategy_event_for_user(
            local_user_id,
            strategy_brief_id=outcome.strategy_brief_id,
            event_type="final_resume_approved",
            payload={"stage": "final_resume"},
        )

    try:
        published = ResumeOrchestrator(
            google_services=get_google_services_from_request(http_request),
        ).publish(
            draft.request,
            outcome.tailored_resume,
            evaluation=draft.evaluation,
            analyzed_jd=getattr(draft.fit, "analyzed_jd", None),
            strategy_brief_id=outcome.strategy_brief_id,
            resume_text=outcome.original_resume_text,
        )
    except PipelineError as e:
        raise HTTPException(status_code=502, detail=_friendly_google_error(str(e)))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error publishing approved draft: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=_friendly_google_error(str(e)))

    approval_storage.delete(request.approval_id)
    if local_user_id and outcome.strategy_brief_id:
        add_job_strategy_event_for_user(
            local_user_id,
            strategy_brief_id=outcome.strategy_brief_id,
            event_type="final_resume_saved",
            payload={
                "doc_url": published.doc_url,
                "application_id": published.application_id,
            },
        )
    return {
        "success": True,
        "approved": True,
        "approval_stage": "final_resume",
        "result": {
            "doc_url": published.doc_url,
            "diff_path": published.diff_path,
            "application_id": published.application_id,
            "strategy_brief_id": outcome.strategy_brief_id,
        },
    }


def _friendly_google_error(message: str) -> str:
    """Turn an expired-credential error into something the user can act on."""
    if "invalid_grant" in message.lower():
        return (
            "Google sign-in has expired or was revoked. Please sign in again with Google "
            "(e.g. from the app or Drive picker), then try Approve and save again."
        )
    return message


@app.put("/api/job-strategy/{brief_id}")
async def edit_job_strategy(brief_id: int, request: StrategyBriefUpdateRequest, http_request: Request):
    """Persist edits to a saved strategy brief.

    Replaces the old approval-scoped edit: a brief is a durable, addressable
    object, so editing it belongs on the brief and not on a transient approval id.
    """
    local_user = get_local_user_from_request(http_request)
    if not get_job_strategy_brief_for_user(local_user["id"], brief_id):
        raise HTTPException(status_code=404, detail="Strategy brief not found")

    from resume_agent.models.agent_models import JobStrategyBrief

    try:
        updated = JobStrategyBrief(**request.strategy_brief)
        updated.id = brief_id
        ResumeOrchestrator(
            google_services=get_google_services_from_request(http_request),
        ).services.agent("strategy_brief").persist_brief(local_user["id"], updated)
        add_job_strategy_event_for_user(
            local_user["id"],
            strategy_brief_id=brief_id,
            event_type="strategy_brief_updated",
            payload={"source": "brief_edit"},
        )
        return _serialize_strategy_detail(local_user["id"], brief_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating strategy brief: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/refine-resume")
async def refine_resume(request: RefinementRequest, http_request: Request):
    """Re-tailor the pending draft with the user's feedback."""
    draft = approval_storage.get(request.approval_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Approval request not found or expired")

    try:
        orchestrator = ResumeOrchestrator(
            google_services=get_google_services_from_request(http_request),
        )
        refine_request = replace(
            draft.request,
            refinement_feedback=request.feedback,
            sections_to_tailor=request.sections_to_tailor,
            target_entry_text=request.target_entry_text,
            revert_target_entry=request.revert_target_entry,
            protected_entry_texts=request.protected_entry_texts,
            preserve_sections=request.preserve_sections,
        )
        # The current draft is the editing baseline; `prior` carries the parse and
        # judgement already made, so refining costs one tailoring call, not five.
        outcome = orchestrator.tailor(
            refine_request,
            prior=draft.fit,
            strategy_brief=draft.outcome.strategy_brief,
            current_draft_text=draft.outcome.tailored_resume,
        )

        approval_storage.store(
            request.approval_id,
            ApprovedDraft(request=refine_request, outcome=outcome, fit=draft.fit),
        )
        return {
            "success": True,
            "message": "Resume refined successfully",
            "approval_id": request.approval_id,
            "result": serialize_tailor_outcome(outcome, request.approval_id),
        }
    except PipelineError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error refining resume: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/update-approval-draft")
async def update_approval_draft(request: ApprovalDraftUpdateRequest, http_request: Request):
    """Persist a hand-edited draft and re-run validation over it."""
    draft = approval_storage.get(request.approval_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Approval request not found or expired")

    updated_text = (request.tailored_resume or "").strip()
    if not updated_text:
        raise HTTPException(status_code=400, detail="Tailored resume text cannot be empty")

    try:
        outcome = replace(draft.outcome, tailored_resume=updated_text)
        # Rules-based, so re-validating a hand edit is free and always runs.
        outcome.validation = validate_tailored_resume(
            original_resume=outcome.original_resume_text or "",
            tailored_resume=updated_text,
            jd_text=draft.request.jd_text or "",
            analyzed_jd=getattr(draft.fit, "analyzed_jd", None),
            profile_context=getattr(draft.fit, "profile_context", None),
        )
        approval_storage.store(
            request.approval_id,
            ApprovedDraft(request=draft.request, outcome=outcome, fit=draft.fit),
        )
        return {
            "success": True,
            "message": "Approval draft updated",
            "approval_id": request.approval_id,
            "result": serialize_tailor_outcome(outcome, request.approval_id),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating approval draft: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Prompt Learning & Feedback Endpoints
# ============================================================================

class FeedbackRequest(BaseModel):
    """Request to submit feedback for prompt learning"""
    feedback_text: str
    feedback_type: str  # 'formatting', 'content', 'style', 'structure', etc.
    context: Optional[Dict[str, Any]] = None  # Resume content, job description, etc.
    suggested_improvement: Optional[str] = None


class LearningApprovalRequest(BaseModel):
    """Request to approve feedback for learning"""
    feedback_id: str
    approve: bool


class PromptUpdateRequest(BaseModel):
    """Request to update prompt based on approved feedback"""
    feedback_ids: List[str]
    prompt_section: str = "system"  # 'system' or 'human'


@app.post("/api/feedback")
async def submit_feedback(request: FeedbackRequest):
    """Submit feedback about resume tailoring output"""
    try:
        # Validate input
        if not request.feedback_text or not request.feedback_text.strip():
            raise HTTPException(status_code=400, detail="feedback_text cannot be empty")
        
        if len(request.feedback_text) > 10000:
            raise HTTPException(status_code=400, detail="feedback_text too long (max 10KB)")
        
        # Validate feedback_type
        valid_types = ['formatting', 'content', 'style', 'structure', 'other']
        if request.feedback_type not in valid_types:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid feedback_type. Must be one of: {', '.join(valid_types)}"
            )
        
        # Sanitize context (limit size)
        context = request.context or {}
        if isinstance(context, dict):
            # Limit context size
            import json
            context_json = json.dumps(context)
            if len(context_json.encode('utf-8')) > 10 * 1024:  # 10KB limit
                context = {"note": "Context too large, storing only metadata"}
        
        from resume_agent.prompts.feedback_learner import FeedbackLearner
        
        feedback_learner = FeedbackLearner()
        feedback_id = feedback_learner.add_feedback(
            feedback_text=request.feedback_text.strip(),
            feedback_type=request.feedback_type,
            context=context,
            suggested_improvement=request.suggested_improvement.strip() if request.suggested_improvement else None
        )
        
        return {
            "success": True,
            "feedback_id": feedback_id,
            "message": "Feedback submitted successfully"
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error submitting feedback: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/feedback/approve")
async def approve_feedback_for_learning(request: LearningApprovalRequest):
    """Approve or reject feedback for prompt learning"""
    try:
        from resume_agent.prompts.feedback_learner import FeedbackLearner
        
        feedback_learner = FeedbackLearner()
        
        if request.approve:
            success = feedback_learner.approve_feedback_for_learning(request.feedback_id)
            if success:
                return {
                    "success": True,
                    "message": "Feedback approved for learning"
                }
            else:
                raise HTTPException(status_code=404, detail="Feedback not found")
        else:
            return {
                "success": True,
                "message": "Feedback not approved for learning"
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error approving feedback: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/feedback/opportunities")
async def get_learning_opportunities():
    """Get feedback entries that could be incorporated into prompts"""
    try:
        from resume_agent.prompts.feedback_learner import FeedbackLearner
        
        feedback_learner = FeedbackLearner()
        opportunities = feedback_learner.get_pending_learning_opportunities()
        
        return {
            "success": True,
            "opportunities": opportunities
        }
    except Exception as e:
        logger.error(f"Error getting learning opportunities: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/feedback/suggestions")
async def get_prompt_suggestions():
    """Get AI-suggested prompt improvements based on feedback patterns"""
    try:
        from resume_agent.prompts.prompt_updater import PromptUpdater
        
        updater = PromptUpdater()
        suggestions = updater.suggest_prompt_improvements()
        
        return {
            "success": True,
            "suggestions": suggestions
        }
    except Exception as e:
        logger.error(f"Error getting prompt suggestions: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/prompts/update")
async def update_prompt_from_feedback(request: PromptUpdateRequest):
    """Update prompt template based on approved feedback (requires confirmation)"""
    try:
        from resume_agent.prompts.prompt_updater import PromptUpdater
        
        updater = PromptUpdater()
        new_version = updater.update_prompt_from_feedback(
            feedback_ids=request.feedback_ids,
            prompt_section=request.prompt_section,
            ask_confirmation=False  # Confirmation handled by frontend
        )
        
        if new_version:
            return {
                "success": True,
                "message": "Prompt updated successfully",
                "new_version": new_version
            }
        else:
            raise HTTPException(status_code=400, detail="Failed to update prompt")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating prompt: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# Google OAuth, user skills, logout, and auth status are in api.routers.auth
