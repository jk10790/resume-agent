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
from resume_agent.storage.google_docs import get_services, read_google_doc
from resume_agent.storage.google_drive_utils import list_google_docs, list_google_folders
from resume_agent.utils.exceptions import GoogleAPIError
from resume_agent.utils.logger import logger

app = FastAPI(
    title="Resume Agent API",
    version="1.0.0",
    description="AI-powered resume tailoring and job application assistant"
)

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
from api.routers import health_router, applications_router, google_drive_router, auth_router
app.include_router(health_router)
app.include_router(applications_router)
app.include_router(google_drive_router)
app.include_router(auth_router)

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
    Get Google services using session credentials if available, otherwise fallback to file-based.
    
    If token is refreshed, updates session with new token and expiry.
    
    Returns:
        Tuple of (drive_service, docs_service) or None if unavailable
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
            logger.warning(f"Failed to use session credentials: {e}, falling back to file-based")
    
    # Fallback to file-based auth
    try:
        return get_services()
    except Exception as e:
        logger.warning(f"Google services not available: {e}")
        return None


class TailorResumeAPIRequest(BaseModel):
    """API request model for tailoring resume"""
    company: str
    job_title: str
    jd_text: str
    job_url: Optional[str] = None
    evaluate_first: bool = True
    track_application: bool = True
    tailoring_intensity: str = "medium"  # "light", "medium", "heavy"
    sections_to_tailor: Optional[list] = None  # List of section names
    refinement_feedback: Optional[str] = None  # Feedback for refinement
    resume_doc_id: Optional[str] = None  # Optional: specific resume doc ID (defaults to configured)
    save_folder_id: Optional[str] = None  # Optional: folder to save to (defaults to configured)


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
    feedback: str  # What to improve


class QualityAnalysisRequest(BaseModel):
    """Request to analyze resume quality"""
    resume_doc_id: Optional[str] = None  # Google Doc ID, or use default
    resume_text: Optional[str] = None  # Or provide text directly
    improve: bool = False  # If True, also improve the resume
    user_answers: Optional[dict] = None  # Answers to clarifying questions


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
            # Load from Google Docs
            google_services = get_google_services_from_request(http_request)
            if google_services:
                _, docs_service = google_services
                from resume_agent.storage.google_docs import read_google_doc
                resume_text = read_google_doc(docs_service, request.resume_doc_id)
        
        if not resume_text:
            raise HTTPException(status_code=400, detail="No resume text provided")
        
        # Analyze quality
        quality_report = quality_agent.analyze_quality(resume_text)
        
        # Store user answers for later realism/metric validation
        if request.user_answers:
            from resume_agent.storage.user_memory import load_memory, save_memory
            memory = load_memory()
            memory["user_answers"] = request.user_answers
            for key in ("metrics_by_role", "metrics_details", "team_size", "project_scale", "notable_achievements"):
                value = request.user_answers.get(key)
                if isinstance(value, str) and value.strip():
                    memory[key] = value.strip()
            save_memory(memory)

        # Convert to dict for JSON response
        result = {
            "overall_score": quality_report.overall_score,
            "ats_score": quality_report.ats_score,
            "metrics_count": quality_report.metrics_count,
            "issues": [
                {
                    "category": issue.category.value,
                    "severity": issue.severity.value,
                    "section": issue.section,
                    "issue": issue.issue,
                    "suggestion": issue.suggestion,
                    "example": issue.example,
                    "research_note": getattr(issue, 'research_note', None)
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
        from resume_agent.storage.user_memory import save_quality_report
        save_quality_report(
            doc_id=request.resume_doc_id or "latest",
            report={
                "overall_score": quality_report.overall_score,
                "ats_score": quality_report.ats_score,
                "metrics_count": quality_report.metrics_count,
                "improvement_priority": quality_report.improvement_priority,
                "estimated_impact": quality_report.estimated_impact
            }
        )
        
        # Optionally improve (using user answers if provided)
        if request.improve:
            improved = quality_agent.improve_resume(
                resume_text, 
                quality_report,
                user_answers=request.user_answers
            )
            result["improved_resume"] = improved.improved_text
            result["changes_made"] = improved.changes_made
            result["before_score"] = improved.before_score
            result["after_score"] = improved.after_score
            result["metrics_added"] = improved.metrics_added
            
            # Auto-cache the improved resume (no separate API call needed!)
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
async def get_cached_improved_resume(doc_id: Optional[str] = None):
    """Get cached improved resume"""
    try:
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
async def clear_cached_improved_resume(doc_id: Optional[str] = None):
    """Clear cached improved resume(s)"""
    try:
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


@app.get("/api/resume/{doc_id}")
async def get_resume_content(doc_id: str, request: Request):
    """
    Fetch the content of a specific Google Doc resume.
    """
    try:
        google_services = get_google_services_from_request(request)
        if not google_services:
            raise HTTPException(status_code=401, detail="Google services not available. Please authenticate with Google.")
        
        _, docs_service = google_services
        resume_content = read_google_doc(docs_service, doc_id)
        
        return {
            "success": True,
            "resume_content": resume_content,
            "resume_text": resume_content  # Alias for frontend compatibility
        }
    except GoogleAPIError as e:
        logger.error(f"Google API error fetching resume {doc_id}: {e}", exc_info=True)
        raise HTTPException(status_code=e.status_code or 500, detail=e.message)
    except Exception as e:
        logger.error(f"Error fetching resume {doc_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/resume/extract-skills")
async def extract_skills_from_resume(request: Request):
    """Extract skills from a resume document"""
    try:
        body = await request.json()
        resume_text = body.get('resume_text', '').strip()
        doc_id = body.get('doc_id')  # Optional: if provided, fetch from Google Docs
        
        if not resume_text and not doc_id:
            raise HTTPException(status_code=400, detail="Either resume_text or doc_id is required")
        
        # If doc_id provided, fetch resume content
        if doc_id and not resume_text:
            google_services = get_google_services_from_request(request)
            if not google_services:
                raise HTTPException(status_code=401, detail="Google services not available. Please authenticate with Google.")
            
            _, docs_service = google_services
            resume_text = read_google_doc(docs_service, doc_id)
        
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
            
            # Convert API request to workflow request
            workflow_request = TailorResumeRequest(
                company=request.company,
                job_title=request.job_title,
                jd_text=request.jd_text,
                job_url=request.job_url,
                evaluate_first=request.evaluate_first,
                track_application=request.track_application,
                tailoring_intensity=request.tailoring_intensity,
                sections_to_tailor=request.sections_to_tailor,
                refinement_feedback=request.refinement_feedback,
                resume_doc_id=request.resume_doc_id,
                save_folder_id=request.save_folder_id
            )
            
            # Define workflow steps (include fit evaluation if requested)
            steps = [
                (WorkflowStep.LOADING_RESUME, "📥 Loading resume from Google Docs..."),
            ]
            
            # Add parsing and fit evaluation steps if requested
            if request.evaluate_first:
                steps.extend([
                    (WorkflowStep.PARSING_RESUME, "🔍 Parsing resume and analyzing job description..."),
                    (WorkflowStep.EVALUATING_FIT, "📊 Evaluating job fit..."),
                ])
            
            validation_message = (
                "🔍 Validating resume quality..."
                if settings.tailoring_run_validation
                else "🔍 Checking cached resume quality..."
            )
            steps.extend([
                (WorkflowStep.TAILORING_RESUME, "✂️ Tailoring resume with AI..."),
                (WorkflowStep.VALIDATING_RESUME, validation_message),
                (WorkflowStep.PREVIEW, "👁️ Preparing preview..."),
            ])
            
            # Initialize result
            result = TailorResumeResult(current_step=WorkflowStep.LOADING_RESUME)
            total_steps = len(steps)
            approval_id = None
            
            # Execute steps with progress updates
            for i, (step, description) in enumerate(steps):
                # Skip PREVIEW step if approval already required (set by VALIDATING_RESUME)
                if step == WorkflowStep.PREVIEW and result.approval_required and result.approval_status == "pending":
                    # Just send step start and complete for UI, then send approval event
                    progress_data = {
                        "type": "step_start",
                        "step": step.value,
                        "message": description,
                        "progress": i / total_steps,
                        "step_number": i + 1,
                        "total_steps": total_steps
                    }
                    yield f"data: {json.dumps(progress_data)}\n\n"
                    
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
                    
                    # Generate approval ID and send approval event
                    import uuid
                    approval_id = str(uuid.uuid4())
                    approval_storage.store(approval_id, result)
                    
                    # Get LLM acknowledgment for tailoring completion
                    llm_acknowledgment = None
                    try:
                        from resume_agent.agents.resume_tailor import get_llm_acknowledgment
                        llm_acknowledgment = get_llm_acknowledgment(llm_service, "tailored")
                    except Exception as e:
                        logger.warning(f"Failed to get LLM acknowledgment: {e}")
                    
                    # Check fit evaluation and warn if low
                    fit_warning = None
                    if result.evaluation:
                        if result.evaluation.score < 5 or not result.evaluation.should_apply:
                            fit_warning = {
                                "score": result.evaluation.score,
                                "should_apply": result.evaluation.should_apply,
                                "message": f"⚠️ Low fit score ({result.evaluation.score}/10). This role may not be a good match. Review the evaluation before proceeding.",
                                "missing_areas": result.evaluation.missing_areas or []
                            }
                    
                    approval_data = {
                        "type": "approval_required",
                        "approval_id": approval_id,
                        "message": "Resume tailored. Please review and approve to continue.",
                        "llm_acknowledgment": llm_acknowledgment,
                        "fit_warning": fit_warning,
                        "progress": 1.0,  # Show 100% when approval is required (all tailoring steps done)
                        "step_number": total_steps,
                        "total_steps": total_steps,
                        "result": {
                            "tailored_resume": result.tailored_resume,
                            "original_resume_text": result.original_resume_text,
                            "evaluation": {
                                "score": result.evaluation.score if result.evaluation else None,
                                "should_apply": result.evaluation.should_apply if result.evaluation else None,
                                "matching_areas": result.evaluation.matching_areas if result.evaluation else [],
                                "missing_areas": result.evaluation.missing_areas if result.evaluation else [],
                                "recommendations": result.evaluation.recommendations if result.evaluation else [],
                                "confidence": result.evaluation.confidence if result.evaluation else None,
                                "reasoning": result.evaluation.reasoning if result.evaluation else None
                            } if result.evaluation else None,
                            "validation": {
                                "quality_score": result.validation.quality_score if result.validation else None,
                                "ats_score": result.ats_score,
                                "issues": [{"category": i.category, "severity": i.severity, "message": i.message, "suggestion": i.suggestion} for i in (result.validation.issues if result.validation else [])],
                                "jd_coverage": result.validation.jd_coverage if result.validation else None,
                                "recommendations": result.validation.recommendations if result.validation else [],
                                "metric_provenance": result.validation.metric_provenance if result.validation else None
                            } if result.validation else None,
                            "quality_report": result.quality_report,
                            "quality_warning": result.quality_warning,
                            "jd_requirements": result.jd_requirements,
                            "current_tailoring_iteration": result.current_tailoring_iteration
                        }
                    }
                    yield f"data: {json.dumps(approval_data)}\n\n"
                    return  # Stop here, wait for approval
                
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
                
                # Check if workflow was stopped due to poor fit
                if getattr(result, 'poor_fit_stopped', False):
                    # Send poor fit notification with evaluation details
                    poor_fit_data = {
                        "type": "poor_fit_stopped",
                        "step": step.value,
                        "message": "⚠️ Workflow stopped: Poor job fit detected",
                        "progress": (i + 1) / total_steps,
                        "evaluation": {
                            "score": result.evaluation.score if result.evaluation else None,
                            "should_apply": result.evaluation.should_apply if result.evaluation else False,
                            "matching_areas": result.evaluation.matching_areas if result.evaluation else [],
                            "missing_areas": result.evaluation.missing_areas if result.evaluation else [],
                            "recommendations": result.evaluation.recommendations if result.evaluation else [],
                            "confidence": result.evaluation.confidence if result.evaluation else None,
                            "reasoning": result.evaluation.reasoning if result.evaluation else None
                        } if result.evaluation else None,
                        "parsed_resume": {
                            "all_skills": result.parsed_resume.all_skills if result.parsed_resume else [],
                            "total_years_experience": result.parsed_resume.total_years_experience if result.parsed_resume else None
                        } if result.parsed_resume else None,
                        "analyzed_jd": {
                            "required_skills": result.analyzed_jd.required_skills if result.analyzed_jd else [],
                            "preferred_skills": result.analyzed_jd.preferred_skills if result.analyzed_jd else [],
                            "required_experience_years": result.analyzed_jd.required_experience_years if result.analyzed_jd else None
                        } if result.analyzed_jd else None
                    }
                    yield f"data: {json.dumps(poor_fit_data)}\n\n"
                    return  # Stop workflow
                
                # Check if approval is required (after VALIDATING_RESUME or PREVIEW)
                # Also check if we're at PREVIEW step and have a tailored resume (approval should be required)
                if (result.approval_required and result.approval_status == "pending") or \
                   (step == WorkflowStep.PREVIEW and result.tailored_resume and result.validation):
                    # Ensure approval flags are set
                    if not result.approval_required:
                        result.approval_required = True
                        result.approval_status = "pending"
                    
                    # Generate approval ID
                    import uuid
                    approval_id = str(uuid.uuid4())
                    approval_storage.store(approval_id, result)
                    
                    # Send step complete for the current step first
                    complete_data = {
                        "type": "step_complete",
                        "step": step.value,
                        "message": f"✅ {description.replace('...', ' completed')}",
                        "progress": (i + 1) / total_steps,
                        "step_number": i + 1,
                        "total_steps": total_steps
                    }
                    yield f"data: {json.dumps(complete_data)}\n\n"
                    
                    # Send approval required event
                    approval_data = {
                        "type": "approval_required",
                        "approval_id": approval_id,
                        "message": "Resume tailored. Please review and approve to continue.",
                        "progress": 1.0,  # Show 100% when approval is required (all tailoring steps done)
                        "step_number": total_steps,
                        "total_steps": total_steps,
                        "result": {
                            "tailored_resume": result.tailored_resume,
                            "original_resume_text": result.original_resume_text,
                            "evaluation": {
                                "score": result.evaluation.score if result.evaluation else None,
                                "should_apply": result.evaluation.should_apply if result.evaluation else None,
                                "matching_areas": result.evaluation.matching_areas if result.evaluation else [],
                                "missing_areas": result.evaluation.missing_areas if result.evaluation else [],
                                "recommendations": result.evaluation.recommendations if result.evaluation else [],
                                "confidence": result.evaluation.confidence if result.evaluation else None,
                                "reasoning": result.evaluation.reasoning if result.evaluation else None
                            } if result.evaluation else None,
                            "validation": {
                                "quality_score": result.validation.quality_score if result.validation else None,
                                "ats_score": result.ats_score,
                                "issues": [{"category": i.category, "severity": i.severity, "message": i.message, "suggestion": i.suggestion} for i in (result.validation.issues if result.validation else [])],
                                "jd_coverage": result.validation.jd_coverage if result.validation else None,
                                "recommendations": result.validation.recommendations if result.validation else [],
                                "metric_provenance": result.validation.metric_provenance if result.validation else None
                            } if result.validation else None,
                            "quality_report": result.quality_report,
                            "quality_warning": result.quality_warning,
                            "jd_requirements": result.jd_requirements,
                            "current_tailoring_iteration": result.current_tailoring_iteration
                        }
                    }
                    yield f"data: {json.dumps(approval_data)}\n\n"
                    return  # Stop here, wait for approval
                
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
            if not result.approval_required and not result.error:
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
                    "result": {
                        "tailored_resume": result.tailored_resume or "",
                        "doc_url": result.doc_url or "",
                        "diff_path": str(result.diff_path) if result.diff_path else None,
                        "application_id": result.application_id,
                        "fit_score": result.evaluation.score if result.evaluation else None,
                        "should_apply": result.evaluation.should_apply if result.evaluation else None,
                        "validation": {
                            "quality_score": result.validation.quality_score if result.validation else None,
                            "is_valid": result.validation.is_valid if result.validation else None,
                            "ats_score": result.validation.ats_score if result.validation else None,
                            "issues": [
                                {
                                    "severity": issue.severity,
                                    "category": issue.category,
                                    "message": issue.message,
                                    "suggestion": issue.suggestion
                                }
                                for issue in (result.validation.issues if result.validation else [])
                            ],
                            "jd_coverage": result.validation.jd_coverage if result.validation else {},
                            "recommendations": result.validation.recommendations if result.validation else [],
                            "metric_provenance": result.validation.metric_provenance if result.validation else None
                        } if result.validation else None,
                        "quality_report": result.quality_report,
                        "quality_warning": result.quality_warning,
                        "jd_requirements": result.jd_requirements,
                        "ats_score": result.ats_score,
                        "original_resume_text": result.original_resume_text,
                        "approval_required": result.approval_required,
                        "approval_status": result.approval_status,
                        "approval_id": approval_id
                    },
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
    """Approve or reject a tailored resume and continue workflow"""
    result = approval_storage.get(request.approval_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Approval request not found or expired")
    
    if not request.approved:
        # Rejected - remove from storage and return
        approval_storage.delete(request.approval_id)
        return {
            "success": True,
            "approved": False,
            "message": "Resume tailoring rejected"
        }
    
    # Approved - continue workflow
    try:
        google_services = get_google_services_from_request(http_request)
        workflow_service = MultiAgentWorkflowService(google_services=google_services)
        
        # Create workflow request from stored result (should have company/job_title stored)
        workflow_request = TailorResumeRequest(
            company=result.company or "",  # Should be stored in result
            job_title=result.job_title or "",  # Should be stored in result
            jd_text=result.jd_text or "",  # Should be stored in result
            job_url=result.job_url,
            track_application=True
        )
        
        # Continue with saving steps
        result.approval_status = "approved"
        result.approval_required = False
        
        # Execute saving steps
        for step in [WorkflowStep.SAVING_TO_GOOGLE, WorkflowStep.GENERATING_DIFF, WorkflowStep.TRACKING_APPLICATION]:
            result = workflow_service.execute_workflow_step(workflow_request, step, result)
            if result.error:
                break
        
        # Remove from storage
        approval_storage.delete(request.approval_id)
        
        return {
            "success": True,
            "approved": True,
            "result": {
                "doc_url": result.doc_url,
                "application_id": result.application_id
            }
        }
    except Exception as e:
        logger.error(f"Error continuing workflow after approval: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/refine-resume")
async def refine_resume(request: RefinementRequest, http_request: Request):
    """Refine a tailored resume based on feedback"""
    original_result = approval_storage.get(request.approval_id)
    if original_result is None:
        raise HTTPException(status_code=404, detail="Approval request not found or expired")
    
    try:
        google_services = get_google_services_from_request(http_request)
        workflow_service = MultiAgentWorkflowService(google_services=google_services)
        
        # Create refinement request using stored metadata from original result
        workflow_request = TailorResumeRequest(
            company=original_result.company or "",
            job_title=original_result.job_title or "",
            jd_text=original_result.jd_text or "",
            refinement_feedback=request.feedback,
            resume_doc_id=None,  # Will use resume_text from result
            save_folder_id=None  # Will use same folder as original
        )
        
        # For refinement, use the CURRENT tailored resume as the base (not original)
        # This allows iterative refinement
        if original_result.tailored_resume:
            # Use tailored resume as the new base for refinement
            original_result.resume_text = original_result.tailored_resume
        
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
                    original_result.jd_text or ""
                )
                refined_result.validation = validation
                refined_result.ats_score = validation.ats_score
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
            "result": {
                "tailored_resume": refined_result.tailored_resume or "",
                "original_resume_text": refined_result.original_resume_text,
                "validation": {
                    "quality_score": refined_result.validation.quality_score if refined_result.validation else None,
                    "is_valid": refined_result.validation.is_valid if refined_result.validation else None,
                    "ats_score": refined_result.validation.ats_score if refined_result.validation else None,
                    "issues": [
                        {
                            "severity": issue.severity,
                            "category": issue.category,
                            "message": issue.message,
                            "suggestion": issue.suggestion
                        }
                        for issue in (refined_result.validation.issues if refined_result.validation else [])
                    ],
                    "jd_coverage": refined_result.validation.jd_coverage if refined_result.validation else {},
                    "recommendations": refined_result.validation.recommendations if refined_result.validation else [],
                    "metric_provenance": refined_result.validation.metric_provenance if refined_result.validation else None
                } if refined_result.validation else None,
                "quality_report": refined_result.quality_report,
                "quality_warning": refined_result.quality_warning,
                "jd_requirements": refined_result.jd_requirements,
                "ats_score": refined_result.ats_score,
                "approval_required": refined_result.approval_required,
                "approval_status": refined_result.approval_status,
                "approval_id": request.approval_id,
                "current_tailoring_iteration": refined_result.current_tailoring_iteration
            }
        }
    except Exception as e:
        logger.error(f"Error refining resume: {e}", exc_info=True)
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
