"""
Multi-Agent Workflow Service
Orchestrates specialized agents in a sequential workflow.
Each agent has a single, focused responsibility.
"""

from typing import Optional, Dict, Any
from dataclasses import dataclass
from enum import Enum

from .llm_service import LLMService
from ..storage.google_docs import get_services, read_google_doc, write_to_google_doc
from ..storage.google_drive import get_subfolder_id_for_job, copy_doc_to_folder
from ..utils.diff import generate_diff_markdown
from ..tracking.application_tracker import add_application
from ..config import RESUME_DOC_ID, GOOGLE_FOLDER_ID, settings
from ..utils.logger import logger
from ..utils.exceptions import ResumeAgentError

# Import all agents
from ..agents.resume_parser_agent import ResumeParserAgent
from ..agents.jd_analyzer_agent import JDAnalyzerAgent
from ..agents.fit_evaluator_agent import FitEvaluatorAgent
from ..agents.ats_scorer_agent import ATSScorerAgent
from ..agents.resume_tailor_agent import ResumeTailorAgent
from ..agents.review_agent import ReviewAgent
from ..agents.resume_fixer_agent import ResumeFixerAgent
from ..agents.resume_humanizer_agent import ResumeHumanizerAgent
from ..agents.resume_quality_agent import ResumeQualityAgent

# Import models
from ..models.agent_models import ParsedResume, AnalyzedJD

# Import existing models
from .resume_workflow import TailorResumeRequest, TailorResumeResult, WorkflowStep


class MultiAgentWorkflowService:
    """
    Workflow service that orchestrates specialized agents.
    Each agent handles one specific task with strict instructions.
    """
    
    def __init__(self, llm_service: Optional[LLMService] = None, google_services: Optional[tuple] = None):
        """
        Initialize multi-agent workflow service.
        
        Args:
            llm_service: Optional LLMService instance (creates one if not provided)
            google_services: Optional tuple of (drive_service, docs_service)
        """
        self.llm_service = llm_service or LLMService()
        self.google_services = google_services
        
        if self.google_services is None:
            try:
                self.google_services = get_services()
            except Exception as e:
                logger.warning(f"Google services not available: {e}")
                self.google_services = None
        
        # Initialize all agents (7 specialized agents)
        self.resume_parser = ResumeParserAgent(self.llm_service)      # Agent 1: Parse resume
        self.jd_analyzer = JDAnalyzerAgent(self.llm_service)          # Agent 2: Analyze JD
        self.fit_evaluator = FitEvaluatorAgent(self.llm_service)      # Agent 3: Evaluate fit
        self.ats_scorer = ATSScorerAgent(self.llm_service)            # Agent 4: Score ATS
        self.resume_tailor = ResumeTailorAgent(self.llm_service)      # Agent 5: Tailor resume
        self.review_agent = ReviewAgent(self.llm_service)             # Agent 6: Review/validate
        self.resume_fixer = ResumeFixerAgent(self.llm_service)        # Agent 7: Fix errors
        self.resume_humanizer = ResumeHumanizerAgent(self.llm_service)  # Agent 8: Humanize output
    
    def execute_workflow_step(
        self,
        request: TailorResumeRequest,
        current_step: WorkflowStep,
        previous_result: Optional[TailorResumeResult] = None,
        progress_callback: Optional[callable] = None
    ) -> TailorResumeResult:
        """
        Execute a single step of the multi-agent workflow.
        
        Args:
            request: TailorResumeRequest with all required information
            current_step: Current step to execute
            previous_result: Result from previous step (if any)
            progress_callback: Optional callback function(message: str) for progress updates
            
        Returns:
            TailorResumeResult with updated state
        """
        result = previous_result or TailorResumeResult(current_step=current_step)
        
        try:
            if current_step == WorkflowStep.LOADING_RESUME:
                logger.info("Multi-Agent Workflow: Loading resume")
                resume_text, error = self._load_resume(request.resume_doc_id)
                if error:
                    result.error = error
                    result.current_step = WorkflowStep.ERROR
                    return result
                result.resume_text = resume_text
                result.original_resume_text = resume_text
                # Load cached resume quality report (or run if missing)
                try:
                    from ..storage.user_memory import get_quality_report, save_quality_report
                    doc_id = request.resume_doc_id or RESUME_DOC_ID or "latest"
                    cached = get_quality_report(doc_id)
                    quality_payload = None
                    if cached and cached.get("report"):
                        quality_payload = {
                            **cached.get("report", {}),
                            "updated_at": cached.get("updated_at"),
                            "source": "cache"
                        }
                    elif settings.quality_auto_run_if_missing:
                        quality_agent = ResumeQualityAgent(self.llm_service)
                        report = quality_agent.analyze_quality(resume_text)
                        saved = save_quality_report(
                            doc_id=doc_id,
                            report={
                                "overall_score": report.overall_score,
                                "ats_score": report.ats_score,
                                "metrics_count": report.metrics_count,
                                "improvement_priority": report.improvement_priority,
                                "estimated_impact": report.estimated_impact
                            }
                        )
                        quality_payload = {
                            **saved.get("report", {}),
                            "updated_at": saved.get("updated_at"),
                            "source": "fresh"
                        }
                    result.quality_report = quality_payload
                    if quality_payload and quality_payload.get("overall_score") is not None:
                        if quality_payload["overall_score"] < settings.quality_low_score_threshold:
                            result.quality_warning = {
                                "score": quality_payload["overall_score"],
                                "threshold": settings.quality_low_score_threshold,
                                "message": "Resume quality score is low. Run Quality Check to improve before tailoring."
                            }
                    elif not quality_payload:
                        result.quality_warning = {
                            "missing": True,
                            "message": "No cached quality score found. Run Quality Check for a detailed report."
                        }
                except Exception as e:
                    logger.warning(f"Quality cache check failed: {e}")
                # Next step: parse resume and analyze JD (can be parallel)
                result.current_step = WorkflowStep.PARSING_RESUME if request.evaluate_first else WorkflowStep.TAILORING_RESUME
                logger.info("Multi-Agent Workflow: Resume loaded", resume_length=len(resume_text))
            
            elif current_step == WorkflowStep.PARSING_RESUME:
                logger.info("Multi-Agent Workflow: Parsing resume and analyzing JD")
                if not result.resume_text:
                    result.error = "Resume not loaded"
                    result.current_step = WorkflowStep.ERROR
                    return result
                
                # Parse Resume and Analyze JD in PARALLEL (they're independent!)
                from concurrent.futures import ThreadPoolExecutor
                
                logger.info("Agent 1 & 2: Parsing resume and analyzing JD in parallel")
                with ThreadPoolExecutor(max_workers=2) as executor:
                    parse_future = executor.submit(self.resume_parser.parse, result.resume_text)
                    analyze_future = executor.submit(
                        self.jd_analyzer.analyze,
                        request.jd_text,
                        request.job_title,
                        request.company
                    )
                    
                    # Wait for both to complete
                    try:
                        parsed_resume = parse_future.result()
                        analyzed_jd = analyze_future.result()
                    except Exception as e:
                        logger.error(f"Parallel parsing failed: {e}", exc_info=True)
                        result.error = f"Failed to parse resume or analyze JD: {e}"
                        result.current_step = WorkflowStep.ERROR
                        return result
                
                # Store parsed data
                result.parsed_resume = parsed_resume
                result.analyzed_jd = analyzed_jd
                
                # Next step: evaluate fit
                result.current_step = WorkflowStep.EVALUATING_FIT
                logger.info("Multi-Agent Workflow: Resume parsed and JD analyzed", 
                          skills_count=len(parsed_resume.all_skills),
                          required_skills_count=len(analyzed_jd.required_skills))
            
            elif current_step == WorkflowStep.EVALUATING_FIT:
                logger.info("Multi-Agent Workflow: Starting fit evaluation")
                if not result.resume_text:
                    result.error = "Resume not loaded"
                    result.current_step = WorkflowStep.ERROR
                    return result
                
                # Get parsed data (should already be available from PARSING_RESUME step)
                parsed_resume = result.parsed_resume
                analyzed_jd = result.analyzed_jd
                
                # If not available, parse now in parallel (fallback)
                if not parsed_resume or not analyzed_jd:
                    from concurrent.futures import ThreadPoolExecutor
                    
                    if progress_callback:
                        progress_callback("Agent 1: Parsing resume...")
                        progress_callback("Agent 2: Analyzing job description...")
                    
                    logger.info("Agents 1 & 2: Parsing resume and analyzing JD in parallel")
                    with ThreadPoolExecutor(max_workers=2) as executor:
                        if not parsed_resume:
                            parse_future = executor.submit(self.resume_parser.parse, result.resume_text)
                        if not analyzed_jd:
                            analyze_future = executor.submit(
                                self.jd_analyzer.analyze,
                                request.jd_text,
                                request.job_title,
                                request.company
                            )
                        
                        # Wait for results
                        if not parsed_resume:
                            try:
                                parsed_resume = parse_future.result()
                                result.parsed_resume = parsed_resume
                            except Exception as e:
                                logger.error(f"Resume parser failed: {e}", exc_info=True)
                                result.error = f"Failed to parse resume: {e}"
                                result.current_step = WorkflowStep.ERROR
                                return result
                        
                        if not analyzed_jd:
                            try:
                                analyzed_jd = analyze_future.result()
                                result.analyzed_jd = analyzed_jd
                            except Exception as e:
                                logger.error(f"JD analyzer failed: {e}", exc_info=True)
                                result.error = f"Failed to analyze job description: {e}"
                                result.current_step = WorkflowStep.ERROR
                                return result
                    
                    if progress_callback:
                        progress_callback("✅ Resume parsed and JD analyzed")
                
                # Agent 3: Evaluate Fit (single responsibility)
                if progress_callback:
                    progress_callback("Agent 3: Evaluating job fit...")
                logger.info("Agent 3: Fit Evaluator - Evaluating fit")
                try:
                    fit_evaluation = self.fit_evaluator.evaluate_fit(parsed_resume, analyzed_jd)
                    result.evaluation = fit_evaluation
                    if progress_callback:
                        progress_callback(f"✅ Fit evaluation complete (Score: {fit_evaluation.score}/10)")
                except Exception as e:
                    logger.error(f"Fit evaluator failed: {e}", exc_info=True)
                    # Continue with default evaluation
                    from ..models.resume import FitEvaluation
                    fit_evaluation = FitEvaluation(
                        score=5,
                        should_apply=False,
                        confidence=0.5,
                        matching_areas=[],
                        missing_areas=["Evaluation failed"],
                        recommendations=["Manual review recommended"]
                    )
                    result.evaluation = fit_evaluation
                
                # Check fit evaluation result - only proceed if fit is adequate
                logger.info(
                    "Multi-Agent Workflow: Fit evaluation complete",
                    score=fit_evaluation.score,
                    should_apply=fit_evaluation.should_apply
                )
                
                # If fit is poor, stop and return results to user
                if not fit_evaluation.should_apply and fit_evaluation.score < 5:
                    logger.warning(
                        "Multi-Agent Workflow: Poor fit detected, stopping workflow",
                        score=fit_evaluation.score,
                        should_apply=fit_evaluation.should_apply
                    )
                    result.current_step = WorkflowStep.COMPLETED
                    result.poor_fit_stopped = True  # Flag to indicate workflow stopped due to poor fit
                    return result
                
                # Transition to tailoring only if fit is adequate
                result.current_step = WorkflowStep.TAILORING_RESUME
            
            elif current_step == WorkflowStep.TAILORING_RESUME:
                logger.info("Multi-Agent Workflow: Starting tailoring")
                if not result.resume_text:
                    result.error = "Resume not loaded"
                    result.current_step = WorkflowStep.ERROR
                    return result
                
                # Get parsed data from previous step
                parsed_resume = result.parsed_resume
                analyzed_jd = result.analyzed_jd
                fit_evaluation = result.evaluation
                
                # If not from previous step, parse now (with error handling)
                if not parsed_resume:
                    logger.info("Agent 1: Resume Parser - Parsing resume (late)")
                    try:
                        parsed_resume = self.resume_parser.parse(result.resume_text)
                        result.parsed_resume = parsed_resume
                    except Exception as e:
                        logger.error(f"Resume parser failed: {e}", exc_info=True)
                        result.error = f"Failed to parse resume: {e}"
                        result.current_step = WorkflowStep.ERROR
                        return result
                
                if not analyzed_jd:
                    logger.info("Agent 2: JD Analyzer - Analyzing JD (late)")
                    try:
                        analyzed_jd = self.jd_analyzer.analyze(
                            request.jd_text,
                            job_title=request.job_title,
                            company=request.company
                        )
                        result.analyzed_jd = analyzed_jd
                    except Exception as e:
                        logger.error(f"JD analyzer failed: {e}", exc_info=True)
                        result.error = f"Failed to analyze job description: {e}"
                        result.current_step = WorkflowStep.ERROR
                        return result
                
                if not fit_evaluation:
                    logger.info("Agent 3: Fit Evaluator - Evaluating fit (late)")
                    try:
                        fit_evaluation = self.fit_evaluator.evaluate_fit(parsed_resume, analyzed_jd)
                        result.evaluation = fit_evaluation
                    except Exception as e:
                        logger.error(f"Fit evaluator failed: {e}", exc_info=True)
                        # Continue with default evaluation
                        from ..models.resume import FitEvaluation
                        fit_evaluation = FitEvaluation(
                            score=5,
                            should_apply=False,
                            confidence=0.5,
                            matching_areas=[],
                            missing_areas=["Evaluation failed"],
                            recommendations=["Manual review recommended"]
                        )
                        result.evaluation = fit_evaluation
                
                # Step 4: Tailor Resume (ATS scoring happens after tailoring)
                if progress_callback:
                    progress_callback("Agent 5: Tailoring resume with AI...")
                logger.info("Agent 5: Resume Tailor - Tailoring resume")
                intensity = request.tailoring_intensity or settings.tailoring_intensity_default
                
                # Check cache for similar patterns
                try:
                    from ..utils.cache_tailoring import TailoringCache
                    from ..agents.resume_validator import extract_jd_requirements
                    
                    jd_requirements = extract_jd_requirements(self.llm_service, request.jd_text)
                    tailoring_cache = TailoringCache()
                    similar_patterns = tailoring_cache.find_similar_patterns(
                        request.jd_text,
                        jd_requirements,
                        min_similarity=0.7
                    )
                    
                    if similar_patterns and similar_patterns[0][1] >= 0.85:
                        logger.info("Found similar tailoring pattern", 
                                  pattern_id=similar_patterns[0][0].pattern_id, 
                                  similarity=similar_patterns[0][1])
                except Exception as e:
                    logger.warning(f"Cache check failed: {e}")
                
                tailored_resume = self.resume_tailor.tailor(
                    result.resume_text,
                    parsed_resume,
                    analyzed_jd,
                    fit_evaluation,
                    None,  # ATS score calculated after tailoring
                    intensity=intensity,
                    refinement_feedback=request.refinement_feedback
                )
                # Optional humanization pass to reduce AI-sounding phrasing
                if settings.humanizer_enabled:
                    try:
                        if progress_callback:
                            progress_callback("Agent 8: Humanizing resume tone...")
                        logger.info("Agent 8: Humanizer - Refining naturalness")
                        tailored_resume = self.resume_humanizer.humanize(
                            result.resume_text,
                            tailored_resume
                        )
                    except Exception as e:
                        logger.warning(f"Humanizer failed: {e}")

                result.tailored_resume = tailored_resume
                if progress_callback:
                    progress_callback("✅ Resume tailored successfully")
                
                # Step 5: ATS Scoring (on tailored resume - more accurate)
                ats_score = None
                if fit_evaluation.should_apply or fit_evaluation.score >= 6:
                    if progress_callback:
                        progress_callback("Agent 4: Calculating ATS score...")
                    logger.info("Agent 4: ATS Scorer - Calculating ATS score on tailored resume")
                    try:
                        ats_score = self.ats_scorer.score(
                            tailored_resume,  # Use tailored resume for accurate scoring
                            analyzed_jd,
                            parsed_resume
                        )
                        result.ats_score = ats_score.score
                        result.ats_score_object = ats_score
                        if progress_callback:
                            progress_callback(f"✅ ATS score: {ats_score.score}/100")
                    except Exception as e:
                        logger.warning(f"ATS scoring failed: {e}", exc_info=True)
                        # Continue without ATS score
                else:
                    logger.info("Skipping ATS scoring - fit score too low", score=fit_evaluation.score)
                
                # Step 6: Review and Validate (optional - only run if enabled)
                if settings.tailoring_run_validation:
                    if progress_callback:
                        progress_callback("Agent 6: Reviewing and validating resume...")
                    logger.info("Agent 6: Review Agent - Reviewing tailored resume")
                    try:
                        review_result = self.review_agent.review(
                            result.resume_text,
                            tailored_resume,
                            parsed_resume,
                            analyzed_jd,
                            fit_evaluation,
                            ats_score
                        )
                    except Exception as e:
                        logger.error(f"Review agent failed: {e}", exc_info=True)
                        # Create minimal validation result to continue
                        from ..models.agent_models import ResumeValidation, ValidationIssue, Severity, ReviewResult
                        review_result = ReviewResult(
                            reviewed_resume=tailored_resume,
                            validation=ResumeValidation(
                                quality_score=50,
                                is_valid=False,
                                issues=[ValidationIssue(
                                    severity=Severity.WARNING,
                                    category="review",
                                    message=f"Review agent failed: {e}",
                                    suggestion="Manual review recommended"
                                )],
                                jd_coverage={},
                                keyword_density=0.0,
                                length_check={},
                                recommendations=["Review failed - manual check recommended"],
                                metric_provenance={},
                            ),
                            changes_made=[],
                            final_quality_score=50.0
                        )

                    # Update result with reviewed resume
                    result.tailored_resume = review_result.reviewed_resume
                    result.validation = review_result.validation
                    result.ats_score = review_result.validation.ats_score if review_result.validation else result.ats_score
                else:
                    result.validation = None
                
                result.current_step = WorkflowStep.VALIDATING_RESUME
                logger.info(
                    "Multi-Agent Workflow: Tailoring complete",
                    quality_score=result.validation.quality_score if result.validation else None,
                    changes_made=len(review_result.changes_made) if 'review_result' in locals() else 0
                )
            
            elif current_step == WorkflowStep.VALIDATING_RESUME:
                # Validation already done in review agent - check for ERRORs and auto-fix
                from ..agents.resume_validator import has_critical_errors, validate_resume_quality
                from ..storage.user_memory import get_skills
                
                user_skills = get_skills()
                
                # Check if there are ERROR-level issues that need fixing
                if result.validation and has_critical_errors(result.validation.issues):
                    if progress_callback:
                        progress_callback("Agent 7: Fixing validation errors...")
                    
                    error_count = sum(1 for i in result.validation.issues 
                                     if i.severity == "error" or str(i.severity) == "Severity.ERROR")
                    logger.info(
                        "Agent 7: Resume Fixer - Fixing errors",
                        error_count=error_count
                    )
                    
                    # Use the dedicated Resume Fixer Agent
                    fix_result = self.resume_fixer.fix_errors(
                        tailored_resume=result.tailored_resume,
                        original_resume=result.original_resume_text or result.resume_text or "",
                        errors=result.validation.issues,
                        user_skills=user_skills
                    )
                    
                    if fix_result.changes_made:
                        result.tailored_resume = fix_result.fixed_resume
                        
                        if progress_callback:
                            progress_callback(f"✅ Fixed {fix_result.errors_fixed} errors")
                        
                        # Re-validate after fix
                        if progress_callback:
                            progress_callback("🔍 Re-validating after fixes...")
                        
                        logger.info("Re-validating resume after Agent 7 fixes")
                        try:
                            new_validation = validate_resume_quality(
                                self.llm_service,
                                fix_result.fixed_resume,
                                request.jd_text or "",
                                result.original_resume_text or result.resume_text or ""
                            )
                            result.validation = new_validation
                            
                            # Check if there are still ERRORs
                            remaining_errors = sum(1 for i in new_validation.issues 
                                                  if i.severity == "error" or str(i.severity) == "Severity.ERROR")
                            if remaining_errors > 0:
                                logger.warning(f"Still {remaining_errors} ERRORs after fix")
                            else:
                                logger.info("All ERRORs fixed successfully")
                                if progress_callback:
                                    progress_callback("✅ All errors fixed!")
                        except Exception as e:
                            logger.error(f"Re-validation failed: {e}", exc_info=True)
                
                result.current_step = WorkflowStep.PREVIEW
                result.approval_required = True
                result.approval_status = "pending"
                logger.info("Multi-Agent Workflow: Validation complete")
            
            elif current_step == WorkflowStep.PREVIEW:
                # Preview step - waiting for approval
                logger.info("Multi-Agent Workflow: Preview - waiting for approval")
                if not result.approval_required:
                    result.approval_required = True
                    result.approval_status = "pending"
                return result
            
            elif current_step == WorkflowStep.SAVING_TO_GOOGLE:
                logger.info("Multi-Agent Workflow: Saving to Google Docs")
                if not self.google_services:
                    result.error = "Google services not available"
                    result.current_step = WorkflowStep.ERROR
                    return result
                if not result.tailored_resume:
                    result.error = "Tailored resume not available"
                    result.current_step = WorkflowStep.ERROR
                    return result
                
                drive_service, docs_service = self.google_services
                base_folder_id = request.save_folder_id or GOOGLE_FOLDER_ID
                if not base_folder_id:
                    result.error = "Save folder ID not provided"
                    result.current_step = WorkflowStep.ERROR
                    return result
                
                subfolder_id = get_subfolder_id_for_job(base_folder_id, request.job_title, request.company)
                source_doc_id = request.resume_doc_id or RESUME_DOC_ID
                if not source_doc_id:
                    result.error = "Source resume document ID not available"
                    result.current_step = WorkflowStep.ERROR
                    return result
                
                tailored_doc_id = copy_doc_to_folder(
                    source_doc_id,
                    subfolder_id,
                    f"{request.job_title}_Tailored"
                )
                write_to_google_doc(tailored_doc_id, result.tailored_resume)
                result.tailored_doc_id = tailored_doc_id
                result.doc_url = f"https://docs.google.com/document/d/{tailored_doc_id}"
                result.current_step = WorkflowStep.GENERATING_DIFF
                logger.info("Multi-Agent Workflow: Saved to Google Docs", doc_id=tailored_doc_id)
            
            elif current_step == WorkflowStep.GENERATING_DIFF:
                logger.info("Multi-Agent Workflow: Generating diff")
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
                result.current_step = WorkflowStep.TRACKING_APPLICATION if request.track_application else WorkflowStep.COMPLETED
                logger.info("Multi-Agent Workflow: Diff generated")
            
            elif current_step == WorkflowStep.TRACKING_APPLICATION:
                logger.info("Multi-Agent Workflow: Tracking application")
                if result.evaluation:
                    application_id = add_application(
                        job_title=request.job_title,
                        company=request.company,
                        job_url=request.job_url or "",
                        fit_score=result.evaluation.score,
                        resume_doc_id=result.tailored_doc_id
                    )
                    result.application_id = application_id
                result.current_step = WorkflowStep.COMPLETED
                logger.info("Multi-Agent Workflow: Application tracked", application_id=result.application_id)
        
        except Exception as e:
            logger.error(f"Multi-Agent Workflow step {current_step} failed: {e}", exc_info=True)
            result.error = f"Step {current_step.value} failed: {e}"
            result.current_step = WorkflowStep.ERROR
        
        return result
    
    def _load_resume(self, resume_doc_id: Optional[str] = None) -> tuple[Optional[str], Optional[str]]:
        """Load resume from Google Docs"""
        try:
            doc_id = resume_doc_id or RESUME_DOC_ID
            if not doc_id:
                return None, "Resume document ID not provided"
            if not self.google_services:
                return None, "Google services not available"
            
            _, docs_service = self.google_services
            resume_text = read_google_doc(docs_service, doc_id)
            if not resume_text:
                return None, "Resume text is empty"
            
            logger.info("Resume loaded", doc_id=doc_id, length=len(resume_text))
            return resume_text, None
        except Exception as e:
            logger.error(f"Failed to load resume: {e}", exc_info=True)
            return None, f"Failed to load resume: {e}"
