"""
Multi-Agent Workflow Service
Orchestrates specialized agents in a sequential workflow.
Each agent has a single, focused responsibility.
"""

from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from enum import Enum

from .llm_service import LLMService
from ..storage.google_docs import read_google_doc, read_resume_file, write_to_google_doc, create_google_doc_in_folder
from ..storage.google_drive import get_subfolder_id_for_job, copy_doc_to_folder
from ..storage.google_drive_utils import get_file_metadata, GOOGLE_DOC_MIME
from ..utils.diff import generate_diff_markdown
from ..tracking.application_tracker import add_or_update_application
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
from ..review.bundle_builder import build_review_bundle

# Import models
from ..models.agent_models import ParsedResume, AnalyzedJD, UserProfileContext
from ..models.resume import FitEvaluation
from .profile_context_service import ProfileContextService
from .strategy_brief_service import StrategyBriefService
from .archetype_strategy import apply_target_alignment, detect_job_archetype

# Import existing models
from .resume_workflow import TailorResumeRequest, TailorResumeResult, WorkflowStep
from ..storage.user_store import (
    add_job_strategy_event_for_user,
    get_quality_report_for_user,
    link_discovered_role_strategy_brief_for_user,
    save_quality_report_for_user,
    update_job_strategy_brief_status_for_user,
)


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
        self.google_services = google_services  # Session-based only; pass from get_google_services_from_request
        
        # Initialize all agents (7 specialized agents)
        self.resume_parser = ResumeParserAgent(self.llm_service)      # Agent 1: Parse resume
        self.jd_analyzer = JDAnalyzerAgent(self.llm_service)          # Agent 2: Analyze JD
        self.fit_evaluator = FitEvaluatorAgent(self.llm_service)      # Agent 3: Evaluate fit
        self.ats_scorer = ATSScorerAgent(self.llm_service)            # Agent 4: Score ATS
        self.resume_tailor = ResumeTailorAgent(self.llm_service)      # Agent 5: Tailor resume
        self.review_agent = ReviewAgent(self.llm_service)             # Agent 6: Review/validate
        self.resume_fixer = ResumeFixerAgent(self.llm_service)        # Agent 7: Fix errors
        self.resume_humanizer = ResumeHumanizerAgent(self.llm_service)  # Agent 8: Humanize output
        self.profile_context_service = ProfileContextService()
        self.strategy_brief_service = StrategyBriefService(self.llm_service)

    def _load_profile_context(self, request: TailorResumeRequest) -> UserProfileContext:
        """Load persisted profile context once per workflow from the authenticated local profile."""
        try:
            return self.profile_context_service.load(request.local_user_id)
        except Exception as e:
            logger.warning(f"Failed to load profile context for user {request.local_user_id}: {e}")
            return UserProfileContext(local_user_id=request.local_user_id)

    def _apply_profile_context(self, profile_context: Optional[UserProfileContext]) -> None:
        """Inject persisted profile context into agents that still use skill state."""
        if not profile_context:
            return
        deduped_skills = sorted({skill for skill in profile_context.confirmed_skills if skill})
        self.resume_parser.confirmed_skills = deduped_skills
        self.fit_evaluator.confirmed_skills = deduped_skills
        self.resume_tailor.confirmed_skills = deduped_skills
    
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
        if request.local_user_id and result.profile_context is None:
            result.profile_context = self._load_profile_context(request)
            self._apply_profile_context(result.profile_context)
        
        try:
            if current_step == WorkflowStep.LOADING_RESUME:
                logger.info("Multi-Agent Workflow: Loading resume")
                if progress_callback:
                    progress_callback("Loading resume...")
                profile_context = self._load_profile_context(request)
                result.profile_context = profile_context
                self._apply_profile_context(profile_context)
                resume_text, error = self._load_resume(request.resume_doc_id)
                if error:
                    result.error = error
                    result.current_step = WorkflowStep.ERROR
                    return result
                result.resume_text = resume_text
                result.original_resume_text = resume_text
                result.resume_source_cache_key = self._build_resume_source_cache_key(request.resume_doc_id)
                if progress_callback:
                    progress_callback("✅ Resume loaded")
                # Load cached resume quality report (or run if missing)
                try:
                    doc_id = request.resume_doc_id or RESUME_DOC_ID or "latest"
                    profile_context = result.profile_context
                    if profile_context and profile_context.local_user_id:
                        cached = get_quality_report_for_user(profile_context.local_user_id, doc_id)
                    else:
                        from ..storage.user_memory import get_quality_report
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
                        report_payload = {
                            "overall_score": report.overall_score,
                            "ats_score": report.ats_score,
                            "metrics_count": report.metrics_count,
                            "improvement_priority": report.improvement_priority,
                            "estimated_impact": report.estimated_impact
                        }
                        if profile_context and profile_context.local_user_id:
                            saved = save_quality_report_for_user(profile_context.local_user_id, doc_id, report_payload)
                        else:
                            from ..storage.user_memory import save_quality_report
                            saved = save_quality_report(doc_id=doc_id, report=report_payload)
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
                result.current_step = WorkflowStep.PARSING_RESUME
                logger.info("Multi-Agent Workflow: Resume loaded", resume_length=len(resume_text))
            
            elif current_step == WorkflowStep.PARSING_RESUME:
                logger.info("Multi-Agent Workflow: Parsing resume and analyzing JD")
                self._apply_profile_context(result.profile_context)
                if not result.resume_text:
                    result.error = "Resume not loaded"
                    result.current_step = WorkflowStep.ERROR
                    return result
                
                # Parse Resume and Analyze JD in PARALLEL (they're independent!)
                from concurrent.futures import ThreadPoolExecutor
                
                logger.info("Agent 1 & 2: Parsing resume and analyzing JD in parallel")
                with ThreadPoolExecutor(max_workers=2) as executor:
                    parse_future = executor.submit(
                        self.resume_parser.parse,
                        result.resume_text,
                        True,
                        result.resume_source_cache_key,
                    )
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
                            parse_future = executor.submit(
                                self.resume_parser.parse,
                                result.resume_text,
                                True,
                                result.resume_source_cache_key,
                            )
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
                    if result.profile_context and result.profile_context.target_archetype_preferences:
                        fit_evaluation, target_alignment = apply_target_alignment(
                            fit_evaluation,
                            archetype=detect_job_archetype(analyzed_jd),
                            preferences=result.profile_context.target_archetype_preferences,
                        )
                        existing_reasoning = fit_evaluation.reasoning or ""
                        fit_evaluation = FitEvaluation(
                            score=fit_evaluation.score,
                            should_apply=fit_evaluation.should_apply,
                            confidence=fit_evaluation.confidence,
                            matching_areas=fit_evaluation.matching_areas,
                            missing_areas=fit_evaluation.missing_areas,
                            recommendations=fit_evaluation.recommendations[:8],
                            reasoning=f"{existing_reasoning} Target alignment: {target_alignment}.".strip()
                        )
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
                
                if request.evaluate_only:
                    result.current_step = WorkflowStep.COMPLETED
                else:
                    result.current_step = WorkflowStep.BUILDING_STRATEGY

            elif current_step == WorkflowStep.BUILDING_STRATEGY:
                logger.info("Multi-Agent Workflow: Building strategy brief")
                if not result.resume_text:
                    result.error = "Resume not loaded"
                    result.current_step = WorkflowStep.ERROR
                    return result

                parsed_resume = result.parsed_resume
                analyzed_jd = result.analyzed_jd
                fit_evaluation = result.evaluation
                if not parsed_resume or not analyzed_jd or not fit_evaluation:
                    result.error = "Strategy brief requires parsed resume, analyzed JD, and fit evaluation"
                    result.current_step = WorkflowStep.ERROR
                    return result

                if progress_callback:
                    progress_callback("Building job strategy brief...")

                try:
                    brief = self.strategy_brief_service.find_existing_brief(
                        request.local_user_id,
                        company=request.company or analyzed_jd.company or "",
                        job_title=request.job_title or analyzed_jd.job_title or "",
                    )
                    if brief is None:
                        brief = self.strategy_brief_service.build_brief(
                            company=request.company,
                            job_title=request.job_title,
                            job_url=request.job_url,
                            jd_text=request.jd_text,
                            parsed_resume=parsed_resume,
                            analyzed_jd=analyzed_jd,
                            fit_evaluation=fit_evaluation,
                            profile_context=result.profile_context,
                        )
                    brief = self.strategy_brief_service.persist_brief(request.local_user_id, brief)
                    result.strategy_brief = brief
                    result.strategy_brief_id = brief.id
                    if request.local_user_id and request.discovered_role_id and result.strategy_brief_id:
                        link_discovered_role_strategy_brief_for_user(
                            request.local_user_id,
                            request.discovered_role_id,
                            result.strategy_brief_id,
                        )
                    if request.local_user_id and result.strategy_brief_id:
                        add_job_strategy_event_for_user(
                            request.local_user_id,
                            strategy_brief_id=result.strategy_brief_id,
                            event_type="strategy_brief_review_requested",
                            payload={"gating_decision": brief.gating_decision},
                        )
                    result.approval_required = True
                    result.approval_status = "pending"
                    result.approval_stage = "strategy"
                    result.current_step = WorkflowStep.PREVIEW
                    if progress_callback:
                        progress_callback("✅ Strategy brief ready for review")
                except Exception as e:
                    logger.error(f"Strategy brief generation failed: {e}", exc_info=True)
                    result.error = f"Failed to build strategy brief: {e}"
                    result.current_step = WorkflowStep.ERROR
                    return result
            
            elif current_step == WorkflowStep.TAILORING_RESUME:
                logger.info("Multi-Agent Workflow: Starting tailoring")
                self._apply_profile_context(result.profile_context)
                if not result.resume_text:
                    result.error = "Resume not loaded"
                    result.current_step = WorkflowStep.ERROR
                    return result
                
                # Get parsed data from previous step
                parsed_resume = result.parsed_resume
                analyzed_jd = result.analyzed_jd
                fit_evaluation = result.evaluation
                strategy_brief = result.strategy_brief

                if not request.refinement_feedback and not request.revert_target_entry:
                    if not strategy_brief:
                        result.error = "Strategy brief not available"
                        result.current_step = WorkflowStep.ERROR
                        return result
                    if result.approval_stage == "strategy" and result.approval_status != "approved":
                        result.error = "Strategy brief must be approved before tailoring"
                        result.current_step = WorkflowStep.ERROR
                        return result

                # If not from previous step, parse now (with error handling)
                if not parsed_resume:
                    logger.info("Agent 1: Resume Parser - Parsing resume (late)")
                    try:
                        parsed_resume = self.resume_parser.parse(
                            result.resume_text,
                            source_cache_key=result.resume_source_cache_key,
                        )
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
                sections_to_tailor = self._normalize_section_targets(request.sections_to_tailor)
                current_draft_text = result.tailored_resume if (request.refinement_feedback or request.revert_target_entry) else None
                editing_baseline = current_draft_text or result.original_resume_text or result.resume_text
                
                # Exact cache: same resume + same job → use cached result, no LLM call
                from ..utils.agent_cache import get_agent_cache
                agent_cache = get_agent_cache()
                cached_tailored = agent_cache.get_tailored_result(
                    result.resume_text,
                    request.jd_text,
                    intensity=intensity,
                    refinement_feedback=request.refinement_feedback,
                    sections_to_tailor=sections_to_tailor,
                    current_draft_text=current_draft_text,
                    target_entry_text=request.target_entry_text,
                    protected_entry_texts=request.protected_entry_texts,
                    revert_target_entry=request.revert_target_entry,
                )
                if cached_tailored is not None:
                    tailored_resume = cached_tailored
                    logger.info("Using cached tailored resume (same resume + same job)")
                else:
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
                    
                    if current_draft_text and request.target_entry_text and request.revert_target_entry:
                        targeted_resume = self.resume_tailor.revert_single_entry(
                            current_resume_text=editing_baseline,
                            original_resume_text=result.original_resume_text or result.resume_text,
                            target_entry_text=request.target_entry_text,
                            preserve_sections=request.preserve_sections,
                            protected_entry_texts=request.protected_entry_texts,
                        )
                        full_tailored = targeted_resume or editing_baseline
                    elif request.refinement_feedback and current_draft_text and request.target_entry_text:
                        targeted_resume = self.resume_tailor.refine_single_entry(
                            current_resume_text=editing_baseline,
                            original_resume_text=result.original_resume_text or result.resume_text,
                            target_entry_text=request.target_entry_text,
                            feedback=request.refinement_feedback,
                            analyzed_jd=analyzed_jd,
                            preserve_sections=request.preserve_sections,
                            protected_entry_texts=request.protected_entry_texts,
                        )
                        full_tailored = targeted_resume or editing_baseline
                    else:
                        full_tailored = self.resume_tailor.tailor(
                            result.original_resume_text or result.resume_text,
                            parsed_resume,
                            analyzed_jd,
                            fit_evaluation,
                            None,  # ATS score calculated after tailoring
                            strategy_brief=strategy_brief,
                            intensity=intensity,
                            refinement_feedback=request.refinement_feedback,
                            current_draft_text=current_draft_text,
                            preserve_sections=request.preserve_sections,
                            protected_entry_texts=request.protected_entry_texts,
                        )
                    if sections_to_tailor and not request.target_entry_text:
                        from ..utils.resume_parser import parse_resume_sections, merge_resume_sections

                        baseline_sections = parse_resume_sections(editing_baseline)
                        tailored_sections = parse_resume_sections(full_tailored)
                        tailored_resume = merge_resume_sections(
                            baseline_sections,
                            tailored_sections,
                            sections_to_tailor,
                        )
                    else:
                        tailored_resume = full_tailored
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

                    # Cache so repeat tailor for same resume + same job skips LLM
                    agent_cache.set_tailored_result(
                        result.resume_text,
                        request.jd_text,
                        tailored_resume,
                        intensity=intensity,
                        refinement_feedback=request.refinement_feedback,
                        sections_to_tailor=sections_to_tailor,
                        current_draft_text=current_draft_text,
                        target_entry_text=request.target_entry_text,
                        protected_entry_texts=request.protected_entry_texts,
                        revert_target_entry=request.revert_target_entry,
                    )

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
                            ats_msg = f"✅ ATS score: {ats_score.score}/100"
                            if ats_score.score < getattr(settings, 'ats_min_score', 70):
                                ats_msg += " (below recommended 70 — consider improving format/keywords)"
                            progress_callback(ats_msg)
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
                            ats_score,
                            user_skills=(result.profile_context.confirmed_skills if result.profile_context else []),
                            verified_metric_records=(result.profile_context.confirmed_metric_records if result.profile_context else []),
                            strategy_brief=strategy_brief,
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
                            review_bundle=build_review_bundle(
                                tailored_resume=tailored_resume,
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
                                ats_score=ats_score,
                                fit_evaluation=fit_evaluation,
                                analyzed_jd=analyzed_jd,
                                strategy_brief=strategy_brief,
                            ),
                            changes_made=[],
                            final_quality_score=50.0
                        )

                    # Update result with reviewed resume
                    result.tailored_resume = review_result.reviewed_resume
                    result.validation = review_result.validation
                    result.review_bundle = review_result.review_bundle
                else:
                    result.validation = None
                    result.review_bundle = None
                
                result.current_step = WorkflowStep.VALIDATING_RESUME
                logger.info(
                    "Multi-Agent Workflow: Tailoring complete",
                    quality_score=result.validation.quality_score if result.validation else None,
                    changes_made=len(review_result.changes_made) if 'review_result' in locals() else 0
                )
            
            elif current_step == WorkflowStep.VALIDATING_RESUME:
                # Validation already done in review agent - check for ERRORs and auto-fix
                from ..agents.resume_validator import has_critical_errors, validate_resume_quality

                user_skills = result.profile_context.confirmed_skills if result.profile_context else []
                
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
                                result.original_resume_text or result.resume_text or "",
                                fix_result.fixed_resume,
                                request.jd_text or "",
                                user_skills=user_skills,
                                verified_metric_records=(result.profile_context.confirmed_metric_records if result.profile_context else []),
                            )
                            result.validation = new_validation
                            if result.analyzed_jd and result.parsed_resume:
                                try:
                                    refreshed_ats = self.ats_scorer.score(
                                        fix_result.fixed_resume,
                                        result.analyzed_jd,
                                        result.parsed_resume
                                    )
                                    result.ats_score_object = refreshed_ats
                                    result.ats_score = refreshed_ats.score
                                except Exception as ats_error:
                                    logger.warning(f"ATS rescoring after fix failed: {ats_error}", exc_info=True)
                            result.review_bundle = build_review_bundle(
                                tailored_resume=fix_result.fixed_resume,
                                validation=new_validation,
                                ats_score=result.ats_score_object,
                                fit_evaluation=result.evaluation,
                                analyzed_jd=result.analyzed_jd,
                                strategy_brief=result.strategy_brief,
                            )
                            
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
                result.approval_stage = "final_resume"
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
                    result.error = "Save folder ID not provided. Set GOOGLE_FOLDER_ID in settings or choose a folder when tailoring."
                    result.current_step = WorkflowStep.ERROR
                    return result
                source_file_id = request.resume_doc_id or RESUME_DOC_ID
                if not source_file_id:
                    result.error = "Source resume document ID not available. Set RESUME_DOC_ID in settings or select a resume when tailoring."
                    result.current_step = WorkflowStep.ERROR
                    return result
                # Fallback for optional company/job title (used for folder and doc naming only)
                job_display = (request.job_title or "").strip() or "Job_Application"
                company_display = (request.company or "").strip() or "Application"
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
                    # Use company/role from request or extract from JD (analyzed_jd) to avoid duplicates
                    job_title = (request.job_title or "").strip() or (
                        getattr(result.analyzed_jd, "job_title", None) or "Job Application"
                    )
                    company = (request.company or "").strip() or (
                        getattr(result.analyzed_jd, "company", None) or "Unknown"
                    )
                    application_id = add_or_update_application(
                        job_title=job_title,
                        company=company,
                        user_id=request.local_user_id,
                        job_url=request.job_url or "",
                        fit_score=result.evaluation.score,
                        strategy_brief_id=result.strategy_brief_id,
                        resume_doc_id=result.tailored_doc_id,
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
        """Load resume from Google Docs or PDF"""
        try:
            doc_id = resume_doc_id or RESUME_DOC_ID
            if not doc_id:
                return None, "Resume document ID not provided"
            if not self.google_services:
                return None, "Google services not available"
            drive_service, docs_service = self.google_services
            resume_text = read_resume_file(drive_service, docs_service, doc_id)
            if not resume_text:
                return None, "Resume text is empty"
            logger.info("Resume loaded", doc_id=doc_id, length=len(resume_text))
            return resume_text, None
        except Exception as e:
            logger.error(f"Failed to load resume: {e}", exc_info=True)
            return None, f"Failed to load resume: {e}"

    def _build_resume_source_cache_key(self, resume_doc_id: Optional[str] = None) -> Optional[str]:
        """Build a stable source-version cache key for Drive-backed resumes."""
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

    def _normalize_section_targets(self, sections_to_tailor: Optional[List[str]]) -> Optional[List[str]]:
        if sections_to_tailor is None:
            return None
        normalized = []
        for section in sections_to_tailor:
            clean = (section or "").strip().lower()
            if clean and clean not in normalized:
                normalized.append(clean)
        return normalized

    def mark_strategy_approval(
        self,
        result: TailorResumeResult,
        *,
        approved: bool,
        overridden: bool = False,
        user_id: Optional[int] = None,
    ) -> TailorResumeResult:
        """Update in-memory and persisted strategy approval state."""
        if not result.strategy_brief:
            return result

        status = "rejected"
        if approved:
            status = "override_approved" if overridden else "approved"

        result.strategy_brief.approval_status = status
        result.approval_status = "approved" if approved else "rejected"
        if user_id and result.strategy_brief_id:
            try:
                update_job_strategy_brief_status_for_user(user_id, result.strategy_brief_id, status)
                add_job_strategy_event_for_user(
                    user_id,
                    strategy_brief_id=result.strategy_brief_id,
                    event_type=f"strategy_{status}",
                    payload={"stage": "strategy"},
                )
            except Exception as e:
                logger.warning(f"Failed to persist strategy brief approval status: {e}")
        return result
