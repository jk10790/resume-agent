"""
Resume Workflow Service
Encapsulates the complete resume tailoring workflow logic.
This service can be used by UI, CLI, or future API endpoints.
"""

from typing import Optional, Dict, Any, Tuple, List
from dataclasses import dataclass
from enum import Enum

# Avoid circular imports by importing inside functions
from ..services.llm_service import LLMService
from ..storage.google_docs import read_google_doc, read_resume_file, write_to_google_doc, create_google_doc_in_folder
from ..storage.google_drive import get_subfolder_id_for_job, copy_doc_to_folder
from ..storage.google_drive_utils import get_file_metadata, GOOGLE_DOC_MIME
from ..utils.diff import generate_diff_markdown
from ..tracking.application_tracker import add_or_update_application
from ..config import RESUME_DOC_ID, GOOGLE_FOLDER_ID, settings
from ..utils.logger import logger
from ..utils.exceptions import ResumeAgentError
from ..review.bundle_builder import build_review_bundle
from ..models.agent_models import UserProfileContext


class WorkflowStep(str, Enum):
    """Workflow execution steps"""
    NOT_STARTED = "not_started"
    LOADING_RESUME = "loading_resume"
    PARSING_RESUME = "parsing_resume"  # New: Dedicated parsing step
    ANALYZING_JD = "analyzing_jd"  # New: Dedicated JD analysis step
    EVALUATING_FIT = "evaluating_fit"
    BUILDING_STRATEGY = "building_strategy"
    TAILORING_RESUME = "tailoring_resume"
    VALIDATING_RESUME = "validating_resume"  # New: Quality validation
    PREVIEW = "preview"  # New: User preview/approval
    SAVING_TO_GOOGLE = "saving_to_google"
    GENERATING_DIFF = "generating_diff"
    TRACKING_APPLICATION = "tracking_application"
    COMPLETED = "completed"
    ERROR = "error"


@dataclass
class TailorResumeRequest:
    """Request for tailoring a resume"""
    company: str
    job_title: str
    jd_text: str
    job_url: Optional[str] = None
    evaluate_first: bool = True
    evaluate_only: bool = False
    track_application: bool = True
    tailoring_intensity: Optional[str] = None  # "light", "medium", "heavy" - defaults to config
    sections_to_tailor: Optional[List[str]] = None  # None = all sections, [] = specific sections
    refinement_feedback: Optional[str] = None  # Feedback for iterative refinement
    target_entry_text: Optional[str] = None  # Optional specific line/bullet to rewrite during refinement
    revert_target_entry: bool = False  # If true, restore the targeted entry toward original wording
    protected_entry_texts: Optional[List[str]] = None  # Exact current-draft entries to preserve during refinement
    preserve_sections: Optional[List[str]] = None  # Sections that must remain unchanged during refinement
    resume_doc_id: Optional[str] = None  # Optional: specific resume doc ID (defaults to RESUME_DOC_ID)
    save_folder_id: Optional[str] = None  # Optional: folder to save to (defaults to GOOGLE_FOLDER_ID)
    local_user_id: Optional[int] = None  # Optional authenticated local user id for profile-backed context
    discovered_role_id: Optional[int] = None  # Optional discovery inbox record that seeded this workflow


@dataclass
class TailorResumeResult:
    """Result of tailoring a resume"""
    tailored_resume: str = ""
    tailored_doc_id: str = ""
    doc_url: str = ""
    diff_path: Optional[str] = None
    application_id: Optional[int] = None
    evaluation: Optional[Any] = None  # FitEvaluation
    validation: Optional[Any] = None  # ResumeValidation
    review_bundle: Optional[Any] = None  # ReviewBundle
    quality_report: Optional[Dict[str, Any]] = None  # Cached resume quality report
    quality_warning: Optional[Dict[str, Any]] = None  # Quality warning for UI
    jd_requirements: Optional[Dict[str, List[str]]] = None  # Extracted JD requirements
    original_resume_text: Optional[str] = None  # Original resume for comparison
    error: Optional[str] = None
    current_step: WorkflowStep = WorkflowStep.NOT_STARTED
    resume_text: Optional[str] = None  # Store intermediate result
    # Store request metadata for approval workflow (needed for approve-and-save)
    company: Optional[str] = None
    job_title: Optional[str] = None
    jd_text: Optional[str] = None
    job_url: Optional[str] = None
    resume_doc_id: Optional[str] = None
    save_folder_id: Optional[str] = None
    current_tailoring_iteration: int = 1
    approval_required: bool = False
    approval_status: Optional[str] = None  # "pending", "approved", "rejected"
    ats_score: Optional[int] = None
    # Multi-agent workflow data (properly typed storage)
    parsed_resume: Optional[Any] = None  # ParsedResume from ResumeParserAgent
    analyzed_jd: Optional[Any] = None  # AnalyzedJD from JDAnalyzerAgent
    ats_score_object: Optional[Any] = None  # ATSScore from ATSScorerAgent
    profile_context: Optional[UserProfileContext] = None  # Persisted authenticated user context
    strategy_brief: Optional[Any] = None  # JobStrategyBrief
    strategy_brief_id: Optional[int] = None  # Persisted strategy brief id
    approval_stage: Optional[str] = None  # strategy | final_resume
    # Workflow control flags
    poor_fit_stopped: bool = False  # True if workflow stopped due to poor fit evaluation
    resume_source_cache_key: Optional[str] = None  # Stable source-version cache key for resume parsing


class ResumeWorkflowService:
    """Service for handling resume tailoring workflows"""
    
    def __init__(self, llm_service: Optional[LLMService] = None, google_services: Optional[Tuple] = None):
        """
        Initialize workflow service.
        
        Args:
            llm_service: Optional LLMService instance (creates one if not provided)
            google_services: Optional tuple of (drive_service, docs_service) (creates if not provided)
        """
        self.llm_service = llm_service or LLMService()
        self.google_services = google_services  # Session-based only; pass from get_google_services_from_request
    
    def load_resume(self, resume_doc_id: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
        """
        Load resume from Google Docs.
        
        Args:
            resume_doc_id: Optional specific doc ID to load (defaults to RESUME_DOC_ID from config)
        
        Returns:
            Tuple of (resume_text, error_message). If error, resume_text is None.
        """
        try:
            # Use provided doc_id or fall back to configured default
            doc_id = resume_doc_id or RESUME_DOC_ID
            
            if not doc_id:
                return None, "Resume document ID not provided and RESUME_DOC_ID not configured in settings"
            
            if not self.google_services:
                return None, "Google services not available. Please authenticate."
            drive_service, docs_service = self.google_services
            resume_text = read_resume_file(drive_service, docs_service, doc_id)
            
            if not resume_text:
                return None, "Resume text is empty"
            
            logger.info("Resume loaded", doc_id=doc_id, length=len(resume_text))
            return resume_text, None
        except Exception as e:
            logger.error(f"Failed to load resume: {e}", exc_info=True)
            return None, f"Failed to load resume: {e}"

    def get_resume_source_cache_key(self, resume_doc_id: Optional[str] = None) -> Optional[str]:
        """Build a stable source-version cache key when the resume comes from Drive."""
        try:
            doc_id = resume_doc_id or RESUME_DOC_ID
            if not doc_id or not self.google_services:
                return None
            drive_service, _ = self.google_services
            meta = get_file_metadata(drive_service, doc_id)
            if not meta:
                return None
            modified_time = meta.get("modifiedTime") or "unknown"
            mime_type = meta.get("mimeType") or "unknown"
            return f"drive:{doc_id}:{mime_type}:{modified_time}"
        except Exception as e:
            logger.warning(f"Failed to derive resume source cache key for {resume_doc_id}: {e}")
            return None
    
    def evaluate_fit(
        self,
        resume_text: str,
        jd_text: str
    ) -> Any:  # Returns FitEvaluation
        """
        Evaluate how well a resume fits a job description.
        
        Args:
            resume_text: Resume content
            jd_text: Job description content
        
        Returns:
            FitEvaluation object
        """
        # Import here to avoid circular import
        from ..agents.fit_evaluator import evaluate_resume_fit
        
        logger.info("Evaluating fit", resume_length=len(resume_text), jd_length=len(jd_text))
        evaluation = evaluate_resume_fit(
            model=self.llm_service,
            resume_text=resume_text,
            jd_text=jd_text
        )
        logger.info("Fit evaluation completed", score=evaluation.score, should_apply=evaluation.should_apply)
        return evaluation
    
    def execute_workflow_step(
        self,
        request: TailorResumeRequest,
        current_step: WorkflowStep,
        previous_result: Optional[TailorResumeResult] = None
    ) -> TailorResumeResult:
        """
        Execute a single step of the workflow.
        Designed for progressive UI updates.
        
        Args:
            request: TailorResumeRequest with all required information
            current_step: Current step to execute
            previous_result: Result from previous step (if any)
        
        Returns:
            TailorResumeResult with updated state
        """
        result = previous_result or TailorResumeResult(current_step=current_step)
        
        try:
            if current_step == WorkflowStep.LOADING_RESUME:
                logger.info("Workflow step: Loading resume")
                resume_text, error = self.load_resume(resume_doc_id=request.resume_doc_id)
                if error:
                    result.error = error
                    result.current_step = WorkflowStep.ERROR
                    return result
                result.resume_text = resume_text
                result.resume_source_cache_key = self.get_resume_source_cache_key(request.resume_doc_id)
                should_evaluate = request.evaluate_first or request.evaluate_only
                result.current_step = WorkflowStep.EVALUATING_FIT if should_evaluate else WorkflowStep.TAILORING_RESUME
                logger.info("Workflow step: Resume loaded", resume_length=len(resume_text))
                
            elif current_step == WorkflowStep.EVALUATING_FIT:
                logger.info("Workflow step: Evaluating fit")
                if not result.resume_text:
                    result.error = "Resume not loaded"
                    result.current_step = WorkflowStep.ERROR
                    return result
                evaluation = self.evaluate_fit(result.resume_text, request.jd_text)
                result.evaluation = evaluation
                
                # If fit is too low, set a flag but don't block - let user decide
                # But log a warning
                if evaluation.score < 5 and not evaluation.should_apply:
                    logger.warning(
                        "Low fit score detected - user may want to reconsider tailoring for this role",
                        score=evaluation.score,
                        should_apply=evaluation.should_apply
                    )
                
                if request.evaluate_only:
                    result.current_step = WorkflowStep.COMPLETED
                else:
                    result.current_step = WorkflowStep.TAILORING_RESUME
                logger.info("Workflow step: Fit evaluated", score=evaluation.score, should_apply=evaluation.should_apply)
                
            elif current_step == WorkflowStep.TAILORING_RESUME:
                # Get tailoring intensity from request or use default from config
                intensity = request.tailoring_intensity or settings.tailoring_intensity_default
                logger.info("Workflow step: Tailoring resume", intensity=intensity, sections=request.sections_to_tailor)
                if not result.resume_text:
                    result.error = "Resume not loaded"
                    result.current_step = WorkflowStep.ERROR
                    return result
                
                from ..agents.resume_tailor import tailor_resume_for_job
                from ..utils.cache_tailoring import TailoringCache
                from ..utils.agent_cache import get_agent_cache
                
                # Exact cache: same resume + same job → return cached result, no LLM call
                agent_cache = get_agent_cache()
                cached_tailored = agent_cache.get_tailored_result(
                    result.resume_text,
                    request.jd_text,
                    intensity=intensity,
                    refinement_feedback=request.refinement_feedback,
                    sections_to_tailor=request.sections_to_tailor,
                    current_draft_text=(result.tailored_resume if request.refinement_feedback else None),
                )
                if cached_tailored is not None:
                    result.tailored_resume = cached_tailored
                    if not result.original_resume_text:
                        result.original_resume_text = result.resume_text
                    result.current_step = WorkflowStep.VALIDATING_RESUME
                    logger.info("Workflow step: Resume tailored (from cache)", tailored_length=len(cached_tailored))
                    return result
                
                # Check cache for similar patterns (if we have JD requirements from previous step)
                jd_requirements = result.jd_requirements or {}
                if not jd_requirements:
                    # Extract JD requirements if not already done
                    from ..agents.resume_validator import extract_jd_requirements
                    jd_requirements = extract_jd_requirements(self.llm_service, request.jd_text)
                    result.jd_requirements = jd_requirements
                
                # Try to find similar patterns
                tailoring_cache = TailoringCache()
                similar_patterns = tailoring_cache.find_similar_patterns(
                    request.jd_text,
                    jd_requirements,
                    min_similarity=0.7
                )
                
                # Log cache hit if found (for future use - could apply cached patterns directly)
                if similar_patterns and similar_patterns[0][1] >= 0.85:
                    logger.info("Found similar tailoring pattern", 
                              pattern_id=similar_patterns[0][0].pattern_id, 
                              similarity=similar_patterns[0][1],
                              quality_score=similar_patterns[0][0].quality_score)
                
                # Handle section-by-section tailoring
                if request.sections_to_tailor is not None and len(request.sections_to_tailor) > 0:
                    from ..utils.resume_parser import parse_resume_sections, merge_resume_sections
                    
                    # Parse original resume
                    original_sections = parse_resume_sections(result.resume_text)
                    
                    # Tailor the full resume first (we'll extract sections after)
                    full_tailored = tailor_resume_for_job(
                        result.resume_text,
                        request.jd_text,
                        self.llm_service,
                        intensity=intensity,
                        refinement_feedback=request.refinement_feedback
                    )
                    
                    # Parse tailored resume
                    tailored_sections = parse_resume_sections(full_tailored)
                    
                    # Merge: use tailored for selected sections, original for others
                    tailored_resume = merge_resume_sections(
                        original_sections,
                        tailored_sections,
                        request.sections_to_tailor
                    )
                else:
                    # Tailor entire resume
                    tailored_resume = tailor_resume_for_job(
                        result.resume_text,
                        request.jd_text,
                        self.llm_service,
                        intensity=intensity,
                        refinement_feedback=request.refinement_feedback
                    )
                
                result.tailored_resume = tailored_resume
                # Only set original_resume_text if not already set (preserve first original for comparison)
                if not result.original_resume_text:
                    result.original_resume_text = result.resume_text
                # Cache so repeat tailor for same resume + same job skips LLM
                    agent_cache.set_tailored_result(
                        result.resume_text,
                        request.jd_text,
                        tailored_resume,
                        intensity=intensity,
                        refinement_feedback=request.refinement_feedback,
                        sections_to_tailor=request.sections_to_tailor,
                        current_draft_text=(result.tailored_resume if request.refinement_feedback else None),
                    )
                result.current_step = WorkflowStep.VALIDATING_RESUME
                logger.info("Workflow step: Resume tailored", tailored_length=len(tailored_resume))
                
            elif current_step == WorkflowStep.VALIDATING_RESUME:
                logger.info("Workflow step: Validating resume quality")
                if not result.tailored_resume or not result.resume_text:
                    result.error = "Resume not available for validation"
                    result.current_step = WorkflowStep.ERROR
                    return result
                
                try:
                    from ..agents.resume_validator import validate_resume_quality, extract_jd_requirements
                    
                    # Extract JD requirements first
                    jd_requirements = extract_jd_requirements(self.llm_service, request.jd_text)
                    result.jd_requirements = jd_requirements
                    
                    # Validate resume quality
                    validation = validate_resume_quality(
                        self.llm_service,
                        result.resume_text,
                        result.tailored_resume,
                        request.jd_text
                    )
                    result.validation = validation
                    result.review_bundle = build_review_bundle(
                        tailored_resume=result.tailored_resume,
                        validation=validation,
                        fit_evaluation=result.evaluation,
                    )
                    
                    # Cache the tailoring pattern if quality is good
                    if validation.quality_score >= 70:
                        try:
                            from ..utils.cache_tailoring import TailoringCache
                            from ..utils.resume_parser import parse_resume_sections
                            
                            # Extract changes by comparing sections
                            original_sections = parse_resume_sections(result.resume_text)
                            tailored_sections = parse_resume_sections(result.tailored_resume)
                            
                            tailoring_changes = {}
                            for section_name in tailored_sections:
                                if section_name in original_sections:
                                    if original_sections[section_name].content != tailored_sections[section_name].content:
                                        tailoring_changes[section_name] = tailored_sections[section_name].content[:500]  # Store first 500 chars
                            
                            if tailoring_changes:
                                # Get intensity from request or use default
                                cache_intensity = request.tailoring_intensity or settings.tailoring_intensity_default
                                tailoring_cache = TailoringCache()
                                tailoring_cache.save_pattern(
                                    jd_text=request.jd_text,
                                    jd_requirements=jd_requirements,
                                    tailoring_changes=tailoring_changes,
                                    intensity=cache_intensity,
                                    quality_score=validation.quality_score
                                )
                                logger.info("Cached tailoring pattern", quality_score=validation.quality_score)
                        except Exception as e:
                            logger.warning(f"Failed to cache tailoring pattern: {e}")
                    
                    logger.info(
                        "Workflow step: Resume validated",
                        quality_score=validation.quality_score,
                        ats_score=validation.ats_score,
                        is_valid=validation.is_valid,
                        issues_count=len(validation.issues)
                    )
                    
                    # Auto-fix ERROR-level issues by re-tailoring with validation feedback
                    from ..models.agent_models import Severity
                    error_issues = [issue for issue in validation.issues if issue.severity == Severity.ERROR]
                    if error_issues and not request.refinement_feedback:
                        logger.info(f"Found {len(error_issues)} ERROR-level issues, attempting auto-fix")
                        
                        # Create feedback from error issues
                        error_feedback = "CRITICAL ISSUES FOUND - MUST FIX:\n\n"
                        for issue in error_issues:
                            error_feedback += f"- {issue.message}\n"
                            if issue.suggestion:
                                error_feedback += f"  Fix: {issue.suggestion}\n"
                        
                        # Re-tailor with error feedback
                        try:
                            from ..agents.resume_tailor import tailor_resume_for_job
                            intensity = request.tailoring_intensity or settings.tailoring_intensity_default
                            
                            fixed_resume = tailor_resume_for_job(
                                result.resume_text,  # Use original resume as base
                                request.jd_text,
                                self.llm_service,
                                intensity=intensity,
                                refinement_feedback=error_feedback
                            )
                            
                            result.tailored_resume = fixed_resume
                            logger.info("Auto-fixed ERROR-level issues by re-tailoring")
                            
                            # Re-validate the fixed resume (quick check)
                            try:
                                fixed_validation = validate_resume_quality(
                                    self.llm_service,
                                    result.resume_text,
                                    fixed_resume,
                                    request.jd_text
                                )
                                # Update validation if fixed version is better
                                if fixed_validation.quality_score > validation.quality_score:
                                    result.validation = fixed_validation
                                    result.review_bundle = build_review_bundle(
                                        tailored_resume=fixed_resume,
                                        validation=fixed_validation,
                                        fit_evaluation=result.evaluation,
                                    )
                                    logger.info(f"Fixed resume has better quality score: {fixed_validation.quality_score} > {validation.quality_score}")
                            except Exception as e:
                                logger.warning(f"Failed to re-validate fixed resume: {e}")
                                # Keep original validation
                        except Exception as e:
                            logger.error(f"Failed to auto-fix ERROR issues: {e}", exc_info=True)
                            # Continue with original tailored resume
                    
                    # If validation fails badly, we could stop here, but for now continue
                    # The UI can show warnings and let user decide
                    result.current_step = WorkflowStep.PREVIEW
                    # Set approval required so API knows to wait for user approval
                    result.approval_required = True
                    result.approval_status = "pending"
                    
                except Exception as e:
                    logger.warning(f"Validation failed, continuing anyway: {e}")
                    # Don't fail the workflow if validation fails
                    result.current_step = WorkflowStep.PREVIEW
                    result.approval_required = True
                    result.approval_status = "pending"
                
            elif current_step == WorkflowStep.PREVIEW:
                # Preview step - just a marker, approval already set in VALIDATING_RESUME
                # This step exists so the API can show "Preparing preview..." message
                logger.info("Workflow step: Preview - waiting for user approval")
                # Ensure approval flags are set (in case this step is called directly)
                if not result.approval_required:
                    result.approval_required = True
                    result.approval_status = "pending"
                return result
                
            elif current_step == WorkflowStep.SAVING_TO_GOOGLE:
                logger.info("Workflow step: Saving to Google Docs", save_folder_id=request.save_folder_id, resume_doc_id=request.resume_doc_id)
                if not self.google_services:
                    result.error = "Google services not available"
                    result.current_step = WorkflowStep.ERROR
                    return result
                if not result.tailored_resume:
                    result.error = "Tailored resume not available"
                    result.current_step = WorkflowStep.ERROR
                    return result
                
                drive_service, docs_service = self.google_services
                
                # Use provided save_folder_id or default to GOOGLE_FOLDER_ID
                base_folder_id = request.save_folder_id or GOOGLE_FOLDER_ID
                if not base_folder_id:
                    result.error = "Save folder ID not provided and GOOGLE_FOLDER_ID not configured"
                    result.current_step = WorkflowStep.ERROR
                    return result
                source_file_id = request.resume_doc_id or RESUME_DOC_ID
                if not source_file_id:
                    result.error = "Source resume document ID not available for copying"
                    result.current_step = WorkflowStep.ERROR
                    return result
                # Fallback for optional company/job title (folder and doc naming only)
                job_display = (request.job_title or "").strip() or "Job_Application"
                company_display = (request.company or "").strip() or "Application"
                drive_service, docs_service = self.google_services
                subfolder_id = get_subfolder_id_for_job(base_folder_id, job_display, company_display, drive_service=drive_service)
                doc_name = f"{job_display}_Tailored"
                meta = get_file_metadata(drive_service, source_file_id)
                if meta and meta.get("mimeType") == GOOGLE_DOC_MIME:
                    tailored_doc_id = copy_doc_to_folder(
                        source_file_id,
                        subfolder_id,
                        doc_name,
                        drive_service=drive_service,
                    )
                    write_to_google_doc(tailored_doc_id, result.tailored_resume, docs_service=docs_service)
                else:
                    tailored_doc_id = create_google_doc_in_folder(
                        drive_service, subfolder_id, doc_name, result.tailored_resume, docs_service=docs_service
                    )
                result.tailored_doc_id = tailored_doc_id
                result.doc_url = f"https://docs.google.com/document/d/{tailored_doc_id}"
                result.current_step = WorkflowStep.GENERATING_DIFF
                logger.info("Workflow step: Saved to Google Docs", doc_id=tailored_doc_id, folder_id=subfolder_id)
                
            elif current_step == WorkflowStep.GENERATING_DIFF:
                logger.info("Workflow step: Generating diff")
                if result.resume_text and result.tailored_resume:
                    try:
                        diff_path = generate_diff_markdown(
                            result.resume_text,
                            result.tailored_resume,
                            request.job_title,
                            request.company
                        )
                        result.diff_path = str(diff_path) if diff_path else None
                    except Exception as e:
                        logger.warning(f"Failed to generate diff: {e}")
                result.current_step = WorkflowStep.TRACKING_APPLICATION if (request.track_application and not request.discovered_role_id) else WorkflowStep.COMPLETED
                logger.info("Workflow step: Diff generated")
                
            elif current_step == WorkflowStep.TRACKING_APPLICATION:
                logger.info("Workflow step: Tracking application")
                if not result.evaluation:
                    # Get evaluation if we didn't already do it
                    if result.resume_text:
                        result.evaluation = self.evaluate_fit(result.resume_text, request.jd_text)
                
                if result.evaluation:
                    job_title = (request.job_title or "").strip() or "Job Application"
                    company = (request.company or "").strip() or "Unknown"
                    application_id = add_or_update_application(
                        job_title=job_title,
                        company=company,
                        user_id=request.local_user_id,
                        job_url=request.job_url or "",
                        fit_score=result.evaluation.score,
                        resume_doc_id=result.tailored_doc_id,
                    )
                    result.application_id = application_id
                result.current_step = WorkflowStep.COMPLETED
                logger.info("Workflow step: Application tracked", application_id=result.application_id)
                
        except Exception as e:
            logger.error(f"Workflow step {current_step} failed: {e}", exc_info=True)
            result.error = f"Step {current_step.value} failed: {e}"
            result.current_step = WorkflowStep.ERROR
        
        return result
    
    def tailor_resume_workflow(self, request: TailorResumeRequest) -> TailorResumeResult:
        """
        Complete resume tailoring workflow.
        
        This method handles:
        1. Loading resume
        2. Evaluating fit (optional)
        3. Tailoring resume
        4. Saving to Google Docs
        5. Generating diff
        6. Tracking application (optional)
        
        Args:
            request: TailorResumeRequest with all required information
        
        Returns:
            TailorResumeResult with tailored resume and metadata
        """
        logger.info("Starting resume tailoring workflow", company=request.company, job_title=request.job_title)
        
        # Step 1: Load resume
        resume_text, error = self.load_resume()
        if error:
            logger.error(f"Failed to load resume: {error}")
            return TailorResumeResult(
                tailored_resume="",
                tailored_doc_id="",
                doc_url="",
                error=error
            )
        
        logger.info("Resume loaded successfully", resume_length=len(resume_text))
        
        # Step 2: Evaluate fit (optional)
        evaluation = None
        if request.evaluate_first:
            try:
                evaluation = self.evaluate_fit(resume_text, request.jd_text)
            except Exception as e:
                logger.error(f"Fit evaluation failed: {e}", exc_info=True)
                # Continue anyway if evaluation fails
        
        # Step 3: Tailor resume
        try:
            # Import here to avoid circular import
            from ..agents.resume_tailor import tailor_resume_for_job
            
            logger.info("Starting resume tailoring", resume_length=len(resume_text), jd_length=len(request.jd_text))
            tailored_resume = tailor_resume_for_job(
                resume_text,
                request.jd_text,
                self.llm_service
            )
            logger.info("Resume tailoring completed", tailored_length=len(tailored_resume))
        except Exception as e:
            logger.error(f"Resume tailoring failed: {e}", exc_info=True)
            return TailorResumeResult(
                tailored_resume="",
                tailored_doc_id="",
                doc_url="",
                error=f"Resume tailoring failed: {e}"
            )
        
        # Step 4: Save to Google Docs
        if not self.google_services:
            return TailorResumeResult(
                tailored_resume=tailored_resume,
                tailored_doc_id="",
                doc_url="",
                error="Google services not available"
            )
        
        try:
            drive_service, docs_service = self.google_services
            base_folder_id = request.save_folder_id or GOOGLE_FOLDER_ID
            job_display = (request.job_title or "").strip() or "Job_Application"
            company_display = (request.company or "").strip() or "Application"
            logger.info("Getting subfolder for job", company=company_display, job_title=job_display)
            subfolder_id = get_subfolder_id_for_job(base_folder_id, job_display, company_display, drive_service=drive_service)
            source_file_id = request.resume_doc_id or RESUME_DOC_ID
            doc_name = f"{job_display}_Tailored"
            meta = get_file_metadata(drive_service, source_file_id) if source_file_id else None
            if meta and meta.get("mimeType") == GOOGLE_DOC_MIME:
                logger.info("Copying document to folder", subfolder_id=subfolder_id)
                tailored_doc_id = copy_doc_to_folder(
                    source_file_id,
                    subfolder_id,
                    doc_name,
                    drive_service=drive_service,
                )
                logger.info("Writing tailored resume to Google Doc", doc_id=tailored_doc_id)
                write_to_google_doc(tailored_doc_id, tailored_resume, docs_service=docs_service)
            else:
                logger.info("Creating new Google Doc in folder (PDF or unknown source)", subfolder_id=subfolder_id)
                tailored_doc_id = create_google_doc_in_folder(
                    drive_service, subfolder_id, doc_name, tailored_resume, docs_service=docs_service
                )
            logger.info("Successfully saved to Google Docs", doc_id=tailored_doc_id)
            
            doc_url = f"https://docs.google.com/document/d/{tailored_doc_id}"
            
            # Step 5: Generate diff
            diff_path = None
            try:
                diff_path = generate_diff_markdown(resume_text, tailored_resume, request.job_title, request.company)
                logger.info("Diff generated", path=diff_path)
            except Exception as e:
                logger.warning(f"Failed to generate diff: {e}")
            
            # Step 6: Track application (optional); same company+role updates existing record
            application_id = None
            if request.track_application and not request.discovered_role_id:
                try:
                    # Get evaluation if we didn't already do it
                    if evaluation is None:
                        evaluation = self.evaluate_fit(resume_text, request.jd_text)
                    job_title = (request.job_title or "").strip() or "Job Application"
                    company = (request.company or "").strip() or "Unknown"
                    application_id = add_or_update_application(
                        job_title=job_title,
                        company=company,
                        user_id=request.local_user_id,
                        job_url=request.job_url or "",
                        fit_score=evaluation.score,
                        resume_doc_id=tailored_doc_id,
                    )
                    logger.info("Application tracked", application_id=application_id)
                except Exception as e:
                    logger.warning(f"Could not track application: {e}")
            
            return TailorResumeResult(
                tailored_resume=tailored_resume,
                tailored_doc_id=tailored_doc_id,
                doc_url=doc_url,
                diff_path=diff_path,
                application_id=application_id,
                evaluation=evaluation,
                current_step=WorkflowStep.COMPLETED
            )
            
        except Exception as e:
            logger.error(f"Error saving to Google Docs: {e}", exc_info=True)
            return TailorResumeResult(
                tailored_resume=tailored_resume,
                tailored_doc_id="",
                doc_url="",
                error=f"Error saving to Google Docs: {e}"
            )
