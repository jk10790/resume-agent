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

from resume_agent.services.resume_workflow import (
    ResumeWorkflowService,
    TailorResumeRequest,
    TailorResumeResult,
    WorkflowStep
)
try:
    from resume_agent.services.multi_agent_workflow import MultiAgentWorkflowService
except (ImportError, NameError) as e:
    # Fallback: use old workflow service if import fails
    import sys
    print(f"Warning: Failed to import MultiAgentWorkflowService: {e}", file=sys.stderr)
    MultiAgentWorkflowService = ResumeWorkflowService
from resume_agent.services.llm_service import LLMService
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


def serialize_evaluation(evaluation):
    if not evaluation:
        return None
    return {
        "score": evaluation.score,
        "should_apply": evaluation.should_apply,
        "matching_areas": evaluation.matching_areas,
        "missing_areas": evaluation.missing_areas,
        "recommendations": evaluation.recommendations,
        "confidence": evaluation.confidence,
        "reasoning": getattr(evaluation, "reasoning", None),
    }


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


def serialize_tailor_result(result, approval_id=None):
    return {
        "tailored_resume": result.tailored_resume or "",
        "original_resume_text": result.original_resume_text,
        "evaluation": serialize_evaluation(result.evaluation),
        "validation": serialize_validation(result.validation, result.ats_score),
        "review_bundle": serialize_review_bundle(getattr(result, "review_bundle", None)),
        "quality_report": result.quality_report,
        "quality_warning": result.quality_warning,
        "jd_requirements": result.jd_requirements,
        "ats_score": result.ats_score,
        "approval_required": result.approval_required,
        "approval_status": result.approval_status,
        "approval_stage": getattr(result, "approval_stage", None),
        "approval_id": approval_id,
        "current_tailoring_iteration": result.current_tailoring_iteration,
        "doc_url": result.doc_url or "",
        "diff_path": str(result.diff_path) if result.diff_path else None,
        "application_id": result.application_id,
        "fit_score": result.evaluation.score if result.evaluation else None,
        "should_apply": result.evaluation.should_apply if result.evaluation else None,
        "strategy_brief": serialize_strategy_brief(getattr(result, "strategy_brief", None)),
        "strategy_brief_id": getattr(result, "strategy_brief_id", None),
        "gating_decision": getattr(getattr(result, "strategy_brief", None), "gating_decision", None),
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


@app.post("/api/evaluate-fit")
async def evaluate_fit(request: EvaluateFitRequest, http_request: Request):
    """Evaluate job fit for the current page (e.g. from Chrome extension). Returns score and recommendations."""
    if not request.job_url and not request.jd_text:
        raise HTTPException(status_code=400, detail="Provide job_url or jd_text")
    try:
        llm_service = LLMService()
        google_services = get_google_services_from_request(http_request)
        if not google_services:
            raise HTTPException(
                status_code=401,
                detail=(
                    "Google sign-in required. Open the Resume Agent web app in this browser, "
                    "sign in with Google, then use the extension again (the extension uses the same session)."
                ),
            )
        drive_service, docs_service = google_services
        from resume_agent.config import settings
        from resume_agent.utils.exceptions import GoogleAPIError
        candidate_doc_ids = []
        for raw_value in (
            request.resume_doc_id,
            get_preferred_resume_doc_id(http_request),
            settings.resume_doc_id,
        ):
            normalized = extract_google_doc_id(raw_value)
            if normalized and normalized not in candidate_doc_ids:
                candidate_doc_ids.append(normalized)
        if not candidate_doc_ids:
            raise HTTPException(status_code=400, detail="No resume configured. Set RESUME_DOC_ID or pass resume_doc_id.")
        last_google_error = None
        resume_text = None
        for doc_id in candidate_doc_ids:
            try:
                resume_text = read_resume_file(drive_service, docs_service, doc_id)
                if resume_text:
                    break
            except GoogleAPIError as e:
                last_google_error = e
                continue
        if not resume_text:
            if last_google_error and ("not found" in str(last_google_error).lower() or "inaccessible" in str(last_google_error).lower()):
                raise HTTPException(
                    status_code=403,
                    detail=(
                        "That resume file wasn't found or you don't have access. "
                        "If you're using the extension: open the web app, sign in with Google, "
                        "pick your resume from the app's Drive picker, then try Evaluate fit again from the extension."
                    ),
                )
            if last_google_error:
                raise last_google_error
            raise HTTPException(status_code=400, detail="No readable resume could be loaded for fit evaluation.")
        if request.job_url:
            from resume_agent.agents.jd_extractor import extract_clean_jd
            jd_text = extract_clean_jd(request.job_url, llm_service)
        else:
            jd_text = (request.jd_text or "").strip()
        if not jd_text:
            raise HTTPException(status_code=400, detail="Could not get job description from URL or jd_text.")
        workflow = MultiAgentWorkflowService(llm_service=llm_service, google_services=google_services)
        req = TailorResumeRequest(
            company="",
            job_title="",
            jd_text=jd_text,
            job_url=request.job_url,
            evaluate_first=True,
            evaluate_only=True,
            local_user_id=(get_local_user_from_request(http_request).get("id") if get_session_data(http_request) else None),
        )
        result = TailorResumeResult(current_step=WorkflowStep.LOADING_RESUME, resume_text=resume_text, original_resume_text=resume_text)
        result = workflow.execute_workflow_step(req, WorkflowStep.PARSING_RESUME, result)
        if result.error:
            raise HTTPException(status_code=500, detail=result.error)
        result = workflow.execute_workflow_step(req, WorkflowStep.EVALUATING_FIT, result)
        if result.error:
            raise HTTPException(status_code=500, detail=result.error)
        ev = result.evaluation
        return {
            "success": True,
            "score": ev.score,
            "should_apply": ev.should_apply,
            "confidence": ev.confidence,
            "matching_areas": getattr(ev, "matching_areas", []) or [],
            "missing_areas": getattr(ev, "missing_areas", []) or [],
            "recommendations": getattr(ev, "recommendations", []) or [],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Evaluate fit failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/job-strategy/evaluate")
async def evaluate_job_strategy(request: JobStrategyEvaluateRequest, http_request: Request):
    """Create or reuse a persisted strategy brief for the authenticated user."""
    local_user = get_local_user_from_request(http_request)
    llm_service = LLMService()
    workflow_service = MultiAgentWorkflowService(
        llm_service=llm_service,
        google_services=get_google_services_from_request(http_request),
    )
    workflow_request = TailorResumeRequest(
        company=request.company or "",
        job_title=request.job_title or "",
        jd_text=request.jd_text,
        job_url=request.job_url,
        resume_doc_id=request.resume_doc_id,
        local_user_id=local_user["id"],
        evaluate_first=True,
    )
    result = TailorResumeResult(current_step=WorkflowStep.LOADING_RESUME)
    for step in [
        WorkflowStep.LOADING_RESUME,
        WorkflowStep.PARSING_RESUME,
        WorkflowStep.EVALUATING_FIT,
        WorkflowStep.BUILDING_STRATEGY,
    ]:
        result = workflow_service.execute_workflow_step(workflow_request, step, result)
        if result.error:
            raise HTTPException(status_code=500, detail=result.error)

    add_job_strategy_event_for_user(
        local_user["id"],
        strategy_brief_id=result.strategy_brief_id,
        event_type="strategy_brief_evaluated",
        payload={"gating_decision": result.strategy_brief.gating_decision},
    )
    return {
        **_serialize_strategy_detail(local_user["id"], result.strategy_brief_id),
        "evaluation": serialize_evaluation(result.evaluation),
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

    duplicated = StrategyBriefService(LLMService()).persist_brief(
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

    workflow_service = MultiAgentWorkflowService(
        llm_service=LLMService(),
        google_services=get_google_services_from_request(http_request),
    )
    workflow_request = TailorResumeRequest(
        company=strategy_brief.company,
        job_title=strategy_brief.job_title,
        jd_text=request.jd_text or strategy_brief.jd_text or "",
        job_url=strategy_brief.job_url,
        resume_doc_id=request.resume_doc_id,
        local_user_id=local_user["id"],
        tailoring_intensity=request.tailoring_intensity,
        preserve_sections=request.preserve_sections,
        protected_entry_texts=request.protected_entry_texts,
    )
    if not workflow_request.jd_text:
        raise HTTPException(status_code=400, detail="Saved strategy brief is missing canonical JD text")
    result = TailorResumeResult(
        current_step=WorkflowStep.LOADING_RESUME,
        strategy_brief=strategy_brief,
        strategy_brief_id=strategy_brief.id,
        approval_stage="final_resume",
        approval_status="approved",
    )
    for step in [
        WorkflowStep.LOADING_RESUME,
        WorkflowStep.PARSING_RESUME,
        WorkflowStep.EVALUATING_FIT,
        WorkflowStep.TAILORING_RESUME,
        WorkflowStep.VALIDATING_RESUME,
    ]:
        result.strategy_brief = strategy_brief
        result.strategy_brief_id = strategy_brief.id
        result.approval_stage = "final_resume"
        result.approval_status = "approved"
        result = workflow_service.execute_workflow_step(workflow_request, step, result)
        if result.error:
            raise HTTPException(status_code=500, detail=result.error)

    add_job_strategy_event_for_user(
        local_user["id"],
        strategy_brief_id=brief_id,
        event_type="strategy_tailored",
        payload={"quality_score": getattr(getattr(result, "validation", None), "quality_score", None)},
    )
    return {"result": serialize_tailor_result(result)}


@app.post("/api/job-strategy/{brief_id}/regenerate-section")
async def regenerate_job_strategy_section(brief_id: int, request: JobStrategyRegenerateRequest, http_request: Request):
    local_user = get_local_user_from_request(http_request)
    stored = get_job_strategy_brief_for_user(local_user["id"], brief_id)
    if not stored:
        raise HTTPException(status_code=404, detail="Strategy brief not found")
    from resume_agent.models.agent_models import JobStrategyBrief

    brief = JobStrategyBrief(**stored)
    workflow_service = MultiAgentWorkflowService(
        llm_service=LLMService(),
        google_services=get_google_services_from_request(http_request),
    )
    workflow_request = TailorResumeRequest(
        company=brief.company,
        job_title=brief.job_title,
        jd_text=request.jd_text or brief.jd_text or "",
        job_url=brief.job_url,
        resume_doc_id=request.resume_doc_id,
        local_user_id=local_user["id"],
        evaluate_first=True,
    )
    if not workflow_request.jd_text:
        raise HTTPException(status_code=400, detail="Saved strategy brief is missing canonical JD text")
    result = TailorResumeResult(current_step=WorkflowStep.LOADING_RESUME)
    for step in [WorkflowStep.LOADING_RESUME, WorkflowStep.PARSING_RESUME, WorkflowStep.EVALUATING_FIT]:
        result = workflow_service.execute_workflow_step(workflow_request, step, result)
        if result.error:
            raise HTTPException(status_code=500, detail=result.error)

    regenerated = workflow_service.strategy_brief_service.regenerate_section(
        brief=brief,
        section=request.section,
        parsed_resume=result.parsed_resume,
        analyzed_jd=result.analyzed_jd,
        fit_evaluation=result.evaluation,
        profile_context=result.profile_context,
    )
    regenerated = workflow_service.strategy_brief_service.persist_brief(local_user["id"], regenerated)
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
    workflow_service = MultiAgentWorkflowService(
        llm_service=LLMService(),
        google_services=get_google_services_from_request(http_request),
    )
    workflow_request = TailorResumeRequest(
        company=request.company or existing.company,
        job_title=request.job_title or existing.job_title,
        jd_text=request.jd_text or existing.jd_text or "",
        job_url=request.job_url or existing.job_url,
        resume_doc_id=request.resume_doc_id,
        local_user_id=local_user["id"],
        evaluate_first=True,
    )
    if not workflow_request.jd_text:
        raise HTTPException(status_code=400, detail="Strategy brief is missing canonical JD text")

    result = TailorResumeResult(current_step=WorkflowStep.LOADING_RESUME)
    for step in [WorkflowStep.LOADING_RESUME, WorkflowStep.PARSING_RESUME, WorkflowStep.EVALUATING_FIT]:
        result = workflow_service.execute_workflow_step(workflow_request, step, result)
        if result.error:
            raise HTTPException(status_code=500, detail=result.error)

    rebuilt = workflow_service.strategy_brief_service.build_brief(
        company=workflow_request.company,
        job_title=workflow_request.job_title,
        job_url=workflow_request.job_url,
        jd_text=workflow_request.jd_text,
        parsed_resume=result.parsed_resume,
        analyzed_jd=result.analyzed_jd,
        fit_evaluation=result.evaluation,
        profile_context=result.profile_context,
    )
    rebuilt.id = existing.id
    rebuilt.approval_status = "pending"
    rebuilt.created_at = existing.created_at
    rebuilt = workflow_service.strategy_brief_service.persist_brief(local_user["id"], rebuilt)
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
        
        llm_service = LLMService()
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
        
        llm_service = LLMService()
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
        llm_service = LLMService()
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

        llm_service = LLMService()
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
    async def generate_progress():
        try:
            # Initialize services
            llm_service = LLMService()
            google_services = get_google_services_from_request(http_request)
            
            # Use multi-agent workflow service
            workflow_service = MultiAgentWorkflowService(
                llm_service=llm_service,
                google_services=google_services
            )
            
            # Convert API request to workflow request (company/job_title optional; used for save folder naming)
            workflow_request = TailorResumeRequest(
                company=request.company or "",
                job_title=request.job_title or "",
                jd_text=request.jd_text,
                job_url=request.job_url,
                evaluate_first=request.evaluate_first or request.evaluate_only,
                evaluate_only=request.evaluate_only,
                track_application=(request.track_application and not request.discovered_role_id),
                tailoring_intensity=request.tailoring_intensity,
                sections_to_tailor=request.sections_to_tailor,
                refinement_feedback=request.refinement_feedback,
                target_entry_text=request.target_entry_text,
                revert_target_entry=request.revert_target_entry,
                protected_entry_texts=request.protected_entry_texts,
                preserve_sections=request.preserve_sections,
                resume_doc_id=request.resume_doc_id,
                save_folder_id=request.save_folder_id,
                local_user_id=(get_local_user_from_request(http_request).get("id") if get_session_data(http_request) else None),
                discovered_role_id=request.discovered_role_id,
            )
            
            steps = [
                (WorkflowStep.LOADING_RESUME, "📥 Loading resume from Google Docs..."),
                (WorkflowStep.PARSING_RESUME, "🔍 Parsing resume and analyzing job description..."),
                (WorkflowStep.EVALUATING_FIT, "📊 Evaluating job fit..."),
            ]

            if not request.evaluate_only:
                validation_message = (
                    "🔍 Validating resume quality..."
                    if settings.tailoring_run_validation
                    else "🔍 Checking cached resume quality..."
                )
                steps.extend([
                    (WorkflowStep.BUILDING_STRATEGY, "🧭 Building job strategy brief..."),
                    (WorkflowStep.PREVIEW, "👀 Waiting for strategy approval..."),
                    (WorkflowStep.TAILORING_RESUME, "✂️ Tailoring resume with AI..."),
                    (WorkflowStep.VALIDATING_RESUME, validation_message),
                    (WorkflowStep.PREVIEW, "👁️ Waiting for final resume approval..."),
                ])
            
            # Initialize result and persist request metadata so approve-and-save has resume_doc_id/save_folder_id
            result = TailorResumeResult(current_step=WorkflowStep.LOADING_RESUME)
            result.company = request.company or ""
            result.job_title = request.job_title or ""
            result.jd_text = request.jd_text or ""
            result.job_url = request.job_url
            result.resume_doc_id = request.resume_doc_id
            result.save_folder_id = request.save_folder_id
            total_steps = len(steps)
            approval_id = None
            
            # Execute steps with progress updates
            for i, (step, description) in enumerate(steps):
                # Send step start
                progress_data = {
                    "type": "step_start",
                    "step": step.value,
                    "message": description,
                    "progress": i / total_steps,
                    "step_number": i + 1,
                    "total_steps": total_steps
                }
                yield f"data: {json.dumps(progress_data)}\n\n"
                
                # Create progress callback for agent-level updates
                def progress_callback(message: str):
                    """Send agent-level progress updates"""
                    try:
                        progress_update = {
                            "type": "step_progress",
                            "step": step.value,
                            "message": message,
                            "progress": i / total_steps,
                            "step_number": i + 1,
                            "total_steps": total_steps
                        }
                        # Note: Can't yield from callback, so we'll log it
                        # The callback is mainly for logging and future use
                        logger.info(f"Agent progress: {message}")
                    except Exception:
                        pass  # Don't fail on callback errors
                
                # Execute step (run in thread pool to avoid blocking)
                try:
                    result = await asyncio.to_thread(
                        workflow_service.execute_workflow_step,
                        workflow_request,
                        step,
                        result,
                        progress_callback
                    )
                except Exception as e:
                    logger.error(f"Error executing step {step.value}: {e}", exc_info=True)
                    result.error = str(e)
                    result.current_step = WorkflowStep.ERROR
                
                if result.error:
                    # Send error
                    error_data = {
                        "type": "error",
                        "step": step.value,
                        "error": result.error,
                        "progress": (i + 1) / total_steps
                    }
                    yield f"data: {json.dumps(error_data)}\n\n"
                    break
                
                if result.approval_required and result.approval_status == "pending":
                    complete_data = {
                        "type": "step_complete",
                        "step": step.value,
                        "message": f"✅ {description.replace('...', ' completed')}",
                        "progress": (i + 1) / total_steps,
                        "step_number": i + 1,
                        "total_steps": total_steps
                    }
                    yield f"data: {json.dumps(complete_data)}\n\n"

                    import uuid
                    approval_id = str(uuid.uuid4())
                    approval_storage.store(approval_id, result)

                    fit_warning = None
                    if result.evaluation and (result.evaluation.score < 5 or not result.evaluation.should_apply):
                        fit_warning = {
                            "score": result.evaluation.score,
                            "should_apply": result.evaluation.should_apply,
                            "message": f"⚠️ Low fit score ({result.evaluation.score}/10). This role may not be a good match. Explicit approval is required to continue.",
                            "missing_areas": result.evaluation.missing_areas or []
                        }

                    llm_acknowledgment = None
                    if result.approval_stage == "final_resume":
                        try:
                            from resume_agent.agents.resume_tailor import get_llm_acknowledgment
                            llm_acknowledgment = get_llm_acknowledgment(llm_service, "tailored")
                        except Exception as e:
                            logger.warning(f"Failed to get LLM acknowledgment: {e}")

                    approval_data = {
                        "type": "approval_required",
                        "approval_id": approval_id,
                        "approval_stage": result.approval_stage,
                        "message": (
                            "Strategy brief ready. Review and approve before generating the resume draft."
                            if result.approval_stage == "strategy"
                            else "Resume draft ready. Review and approve before saving and tracking."
                        ),
                        "llm_acknowledgment": llm_acknowledgment,
                        "fit_warning": fit_warning,
                        "progress": (i + 1) / total_steps,
                        "step_number": i + 1,
                        "total_steps": total_steps,
                        "result": serialize_tailor_result(result, approval_id)
                    }
                    yield f"data: {json.dumps(approval_data)}\n\n"
                    return
                
                # Send step complete
                complete_data = {
                    "type": "step_complete",
                    "step": step.value,
                    "message": f"✅ {description.replace('...', ' completed')}",
                    "progress": (i + 1) / total_steps,
                    "step_number": i + 1,
                    "total_steps": total_steps
                }
                yield f"data: {json.dumps(complete_data)}\n\n"
                
                # Small delay for UI smoothness
                await asyncio.sleep(0.1)
            
            # If we got here and no approval needed, continue with saving
            if not result.approval_required and not result.error and not request.evaluate_only:
                # Continue with saving steps
                saving_steps = [
                    (WorkflowStep.SAVING_TO_GOOGLE, "💾 Saving to Google Docs..."),
                    (WorkflowStep.GENERATING_DIFF, "📝 Generating change log..."),
                ]
                
                if request.track_application:
                    saving_steps.append((WorkflowStep.TRACKING_APPLICATION, "📊 Tracking application..."))
                
                for i, (step, description) in enumerate(saving_steps):
                    progress_data = {
                        "type": "step_start",
                        "step": step.value,
                        "message": description,
                        "progress": (total_steps + i) / (total_steps + len(saving_steps)),
                        "step_number": total_steps + i + 1,
                        "total_steps": total_steps + len(saving_steps)
                    }
                    yield f"data: {json.dumps(progress_data)}\n\n"
                    
                    # Create progress callback for agent-level updates
                    def progress_callback(message: str):
                        """Send agent-level progress updates"""
                        # Log for now - could be enhanced to send SSE events
                        logger.info(f"Agent progress: {message}")
                    
                    try:
                        result = await asyncio.to_thread(
                            workflow_service.execute_workflow_step,
                            workflow_request,
                            step,
                            result,
                            progress_callback
                        )
                    except Exception as e:
                        logger.error(f"Error executing step {step.value}: {e}", exc_info=True)
                        result.error = str(e)
                        result.current_step = WorkflowStep.ERROR
                    
                    if result.error:
                        error_data = {
                            "type": "error",
                            "step": step.value,
                            "error": result.error,
                            "progress": (total_steps + i + 1) / (total_steps + len(saving_steps))
                        }
                        yield f"data: {json.dumps(error_data)}\n\n"
                        break
                    
                    complete_data = {
                        "type": "step_complete",
                        "step": step.value,
                        "message": f"✅ {description.replace('...', ' completed')}",
                        "progress": (total_steps + i + 1) / (total_steps + len(saving_steps)),
                        "step_number": total_steps + i + 1,
                        "total_steps": total_steps + len(saving_steps)
                    }
                    yield f"data: {json.dumps(complete_data)}\n\n"
                    await asyncio.sleep(0.1)
            
            # Send final result
            if not result.error:
                # Get LLM acknowledgment if not already done (for non-approval flows)
                llm_acknowledgment = None
                if not result.approval_required:
                    try:
                        from resume_agent.agents.resume_tailor import get_llm_acknowledgment
                        llm_acknowledgment = get_llm_acknowledgment(llm_service, "tailored")
                    except Exception as e:
                        logger.warning(f"Failed to get LLM acknowledgment: {e}")
                
                # Log to ensure we're sending the latest resume
                logger.info(
                    "Sending final result",
                    has_resume=bool(result.tailored_resume),
                    resume_length=len(result.tailored_resume) if result.tailored_resume else 0,
                    application_id=result.application_id
                )
                final_data = {
                    "type": "complete",
                    "llm_acknowledgment": llm_acknowledgment,
                    "result": serialize_tailor_result(result, approval_id),
                    "timestamp": asyncio.get_event_loop().time()
                }
                yield f"data: {json.dumps(final_data)}\n\n"
            else:
                error_data = {
                    "type": "error",
                    "error": result.error,
                    "progress": 1.0
                }
                yield f"data: {json.dumps(error_data)}\n\n"
                
        except Exception as e:
            logger.error(f"Error in tailor_resume_stream: {e}", exc_info=True)
            error_data = {
                "type": "error",
                "error": str(e),
                "traceback": traceback.format_exc()
            }
            yield f"data: {json.dumps(error_data)}\n\n"
    
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
    """Approve or reject the current workflow gate and continue if appropriate."""
    result = approval_storage.get(request.approval_id)
    if result is None:
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
    
    local_user_id = (get_local_user_from_request(http_request).get("id") if get_session_data(http_request) else None)

    if not request.approved:
        workflow_service = MultiAgentWorkflowService(google_services=get_google_services_from_request(http_request))
        if getattr(result, "approval_stage", None) == "strategy":
            workflow_service.mark_strategy_approval(result, approved=False, user_id=local_user_id)
        approval_storage.delete(request.approval_id)
        return {
            "success": True,
            "approved": False,
            "approval_stage": getattr(result, "approval_stage", None),
            "message": (
                "Strategy brief rejected"
                if getattr(result, "approval_stage", None) == "strategy"
                else "Resume tailoring rejected"
            ),
        }
    
    try:
        google_services = get_google_services_from_request(http_request)
        workflow_service = MultiAgentWorkflowService(google_services=google_services)

        workflow_request = TailorResumeRequest(
            company=result.company or "",
            job_title=result.job_title or "",
            jd_text=result.jd_text or "",
            job_url=result.job_url,
            track_application=True,
            resume_doc_id=result.resume_doc_id,
            save_folder_id=result.save_folder_id,
            local_user_id=local_user_id,
        )

        if getattr(result, "approval_stage", None) == "strategy":
            overridden = bool(
                getattr(result, "strategy_brief", None)
                and result.strategy_brief.gating_decision == "stop_and_ask"
            )
            workflow_service.mark_strategy_approval(
                result,
                approved=True,
                overridden=overridden,
                user_id=local_user_id,
            )
            result.approval_required = False
            result.approval_status = "approved"

            for step in [WorkflowStep.TAILORING_RESUME, WorkflowStep.VALIDATING_RESUME]:
                result = workflow_service.execute_workflow_step(workflow_request, step, result)
                if result.error:
                    break

            if result.error:
                raise HTTPException(status_code=502, detail=result.error)

            approval_storage.store(request.approval_id, result)
            return {
                "success": True,
                "approved": True,
                "approval_required": True,
                "approval_id": request.approval_id,
                "approval_stage": result.approval_stage,
                "result": serialize_tailor_result(result, request.approval_id),
            }

        result.approval_status = "approved"
        result.approval_required = False
        if local_user_id and result.strategy_brief_id:
            add_job_strategy_event_for_user(
                local_user_id,
                strategy_brief_id=result.strategy_brief_id,
                event_type="final_resume_approved",
                payload={"stage": "final_resume"},
            )

        for step in [WorkflowStep.SAVING_TO_GOOGLE, WorkflowStep.GENERATING_DIFF, WorkflowStep.TRACKING_APPLICATION]:
            result = workflow_service.execute_workflow_step(workflow_request, step, result)
            if result.error:
                break

        if result.error:
            err = result.error
            if "invalid_grant" in err.lower():
                err = (
                    "Google sign-in has expired or was revoked. "
                    "Please sign in again with Google (e.g. from the app or Drive picker), then try Approve and save again."
                )
            raise HTTPException(status_code=502, detail=err)

        approval_storage.delete(request.approval_id)
        if local_user_id and result.strategy_brief_id:
            add_job_strategy_event_for_user(
                local_user_id,
                strategy_brief_id=result.strategy_brief_id,
                event_type="final_resume_saved",
                payload={
                    "doc_url": result.doc_url,
                    "application_id": result.application_id,
                },
            )
        return {
            "success": True,
            "approved": True,
            "approval_stage": "final_resume",
            "result": {
                "doc_url": result.doc_url,
                "application_id": result.application_id,
                "strategy_brief_id": result.strategy_brief_id,
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error continuing workflow after approval: {e}", exc_info=True)
        err = str(e)
        if "invalid_grant" in err.lower():
            err = (
                "Google sign-in has expired or was revoked. "
                "Please sign in again with Google, then try Approve and save again."
            )
        raise HTTPException(status_code=500, detail=err)


@app.post("/api/update-strategy-brief")
async def update_strategy_brief(request: StrategyBriefUpdateRequest, http_request: Request):
    """Persist strategy-brief edits made during the strategy approval stage."""
    result = approval_storage.get(request.approval_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Approval request not found or expired")
    if getattr(result, "approval_stage", None) != "strategy":
        raise HTTPException(status_code=400, detail="Strategy brief can only be edited during the strategy approval stage")
    if not getattr(result, "strategy_brief", None):
        raise HTTPException(status_code=400, detail="No strategy brief is available for this approval")

    try:
        from resume_agent.models.agent_models import JobStrategyBrief
        updated_brief = JobStrategyBrief(**request.strategy_brief)
        result.strategy_brief = updated_brief
        result.strategy_brief_id = updated_brief.id
        approval_storage.store(request.approval_id, result)

        local_user_id = (get_local_user_from_request(http_request).get("id") if get_session_data(http_request) else None)
        if local_user_id and result.strategy_brief_id:
            workflow_service = MultiAgentWorkflowService(google_services=get_google_services_from_request(http_request))
            workflow_service.strategy_brief_service.persist_brief(local_user_id, updated_brief)
            add_job_strategy_event_for_user(
                local_user_id,
                strategy_brief_id=result.strategy_brief_id,
                event_type="strategy_brief_updated",
                payload={"source": "approval_stage_edit"},
            )

        return {
            "success": True,
            "approval_id": request.approval_id,
            "result": serialize_tailor_result(result, request.approval_id),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating strategy brief: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/refine-resume")
async def refine_resume(request: RefinementRequest, http_request: Request):
    """Refine a tailored resume based on feedback"""
    original_result = approval_storage.get(request.approval_id)
    if original_result is None:
        raise HTTPException(status_code=404, detail="Approval request not found or expired")
    if getattr(original_result, "approval_stage", None) != "final_resume":
        raise HTTPException(status_code=400, detail="Resume refinement is only available during final resume review")
    
    try:
        google_services = get_google_services_from_request(http_request)
        workflow_service = MultiAgentWorkflowService(google_services=google_services)
        
        # Create refinement request using stored metadata from original result
        workflow_request = TailorResumeRequest(
            company=original_result.company or "",
            job_title=original_result.job_title or "",
            jd_text=original_result.jd_text or "",
            refinement_feedback=request.feedback,
            sections_to_tailor=request.sections_to_tailor,
            target_entry_text=request.target_entry_text,
            revert_target_entry=request.revert_target_entry,
            protected_entry_texts=request.protected_entry_texts,
            preserve_sections=request.preserve_sections,
            resume_doc_id=None,  # Will use resume_text from result
            save_folder_id=None,  # Will use same folder as original
            local_user_id=(get_local_user_from_request(http_request).get("id") if get_session_data(http_request) else None),
        )
        
        # Increment iteration counter
        if original_result.current_tailoring_iteration:
            original_result.current_tailoring_iteration += 1
        else:
            original_result.current_tailoring_iteration = 2
        
        # Re-tailor with feedback
        refined_result = workflow_service.execute_workflow_step(
            workflow_request,
            WorkflowStep.TAILORING_RESUME,
            original_result
        )
        
        # Get LLM acknowledgment for refinement
        llm_acknowledgment = None
        try:
            from resume_agent.agents.resume_tailor import get_llm_acknowledgment
            llm_service = LLMService()
            llm_acknowledgment = get_llm_acknowledgment(llm_service, "refined", context=request.feedback)
        except Exception as e:
            logger.warning(f"Failed to get LLM acknowledgment for refinement: {e}")
        
        # Run validation on refined resume (optional)
        if settings.tailoring_run_validation and refined_result.tailored_resume and not refined_result.error:
            try:
                from resume_agent.agents.resume_validator import validate_resume_quality
                llm_service = LLMService()
                validation = validate_resume_quality(
                    llm_service,
                    original_result.original_resume_text or original_result.resume_text or "",
                    refined_result.tailored_resume,
                    original_result.jd_text or "",
                    user_skills=(refined_result.profile_context.confirmed_skills if getattr(refined_result, "profile_context", None) else []),
                    verified_metric_records=(refined_result.profile_context.confirmed_metric_records if getattr(refined_result, "profile_context", None) else []),
                )
                refined_result.validation = validation
                if getattr(original_result, "analyzed_jd", None) and getattr(original_result, "parsed_resume", None):
                    try:
                        ats_score_object = workflow_service.ats_scorer.score(
                            refined_result.tailored_resume,
                            original_result.analyzed_jd,
                            original_result.parsed_resume
                        )
                        refined_result.ats_score_object = ats_score_object
                        refined_result.ats_score = ats_score_object.score
                    except Exception as ats_error:
                        logger.warning(f"ATS scoring failed during refinement: {ats_error}")
                        refined_result.ats_score = original_result.ats_score
                else:
                    refined_result.ats_score = original_result.ats_score
                refined_result.review_bundle = build_review_bundle(
                    tailored_resume=refined_result.tailored_resume,
                    validation=validation,
                    ats_score=getattr(refined_result, "ats_score_object", None) or getattr(original_result, "ats_score_object", None),
                    fit_evaluation=original_result.evaluation,
                    analyzed_jd=original_result.analyzed_jd,
                    strategy_brief=original_result.strategy_brief,
                )
            except Exception as e:
                logger.warning(f"Validation failed during refinement: {e}")
                # Continue without validation
        
        # Preserve metadata in refined result
        refined_result.company = original_result.company
        refined_result.job_title = original_result.job_title
        refined_result.jd_text = original_result.jd_text
        refined_result.job_url = original_result.job_url
        refined_result.current_tailoring_iteration = original_result.current_tailoring_iteration
        # Keep original_resume_text pointing to the very first resume (for comparison)
        if not refined_result.original_resume_text:
            refined_result.original_resume_text = original_result.original_resume_text
        
        # Set approval required again so user can review the refinement
        refined_result.approval_required = True
        refined_result.approval_status = "pending"
        
        # Update stored approval
        approval_storage.store(request.approval_id, refined_result)
        
        # Return full result data for frontend (matching structure from tailor_resume_stream)
        return {
            "success": True,
            "message": "Resume refined successfully",
            "llm_acknowledgment": llm_acknowledgment,
            "approval_id": request.approval_id,
            "result": serialize_tailor_result(refined_result, request.approval_id)
        }
    except Exception as e:
        logger.error(f"Error refining resume: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/update-approval-draft")
async def update_approval_draft(request: ApprovalDraftUpdateRequest, http_request: Request):
    """Persist an edited approval draft and refresh validation/review signals."""
    result = approval_storage.get(request.approval_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Approval request not found or expired")
    if getattr(result, "approval_stage", None) != "final_resume":
        raise HTTPException(status_code=400, detail="Draft editing is only available during final resume review")

    try:
        workflow_service = MultiAgentWorkflowService(google_services=get_google_services_from_request(http_request))
        updated_text = (request.tailored_resume or "").strip()
        if not updated_text:
            raise HTTPException(status_code=400, detail="Tailored resume text cannot be empty")

        result.tailored_resume = updated_text
        result.approval_required = True
        result.approval_status = "pending"

        if settings.tailoring_run_validation and not result.error:
            try:
                from resume_agent.agents.resume_validator import validate_resume_quality
                llm_service = LLMService()
                validation = validate_resume_quality(
                    llm_service,
                    result.original_resume_text or result.resume_text or "",
                    result.tailored_resume,
                    result.jd_text or "",
                    user_skills=(result.profile_context.confirmed_skills if getattr(result, "profile_context", None) else []),
                    verified_metric_records=(result.profile_context.confirmed_metric_records if getattr(result, "profile_context", None) else []),
                )
                result.validation = validation
                if getattr(result, "analyzed_jd", None) and getattr(result, "parsed_resume", None):
                    try:
                        ats_score_object = workflow_service.ats_scorer.score(
                            result.tailored_resume,
                            result.analyzed_jd,
                            result.parsed_resume
                        )
                        result.ats_score_object = ats_score_object
                        result.ats_score = ats_score_object.score
                    except Exception as ats_error:
                        logger.warning(f"ATS scoring failed during approval draft update: {ats_error}")
                result.review_bundle = build_review_bundle(
                    tailored_resume=result.tailored_resume,
                    validation=validation,
                    ats_score=getattr(result, "ats_score_object", None),
                    fit_evaluation=result.evaluation,
                    analyzed_jd=result.analyzed_jd,
                    strategy_brief=result.strategy_brief,
                )
            except Exception as e:
                logger.warning(f"Validation failed during approval draft update: {e}")

        approval_storage.store(request.approval_id, result)
        return {
            "success": True,
            "message": "Approval draft updated",
            "approval_id": request.approval_id,
            "result": serialize_tailor_result(result, request.approval_id),
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
