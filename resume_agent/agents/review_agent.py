"""
Review Agent
Strictly responsible for final review, validation, and any necessary updates.
This agent reviews the tailored resume and makes final adjustments if needed.
"""

from typing import Dict, List, Any, Optional, TYPE_CHECKING
from ..services.llm_service import LLMService
from ..utils.logger import logger
from ..models.agent_models import ReviewResult, ResumeValidation, ValidationIssue
from ..review.bundle_builder import build_review_bundle
from ..review.ats_parse import review_ats_parse
import json
import re

if TYPE_CHECKING:
    from ..models.agent_models import ParsedResume, AnalyzedJD, ATSScore
    from ..models.resume import FitEvaluation
    """Result of review agent"""
    reviewed_resume: str  # Final reviewed resume
    validation: ResumeValidation  # Validation results
    changes_made: List[str]  # List of changes made during review
    final_quality_score: float  # Final quality score


class ReviewAgent:
    """
    Agent responsible ONLY for final review and validation.
    This agent reviews the tailored resume and makes final adjustments.
    """
    
    def __init__(self, llm_service: LLMService):
        self.llm_service = llm_service
    
    def review(
        self,
        original_resume_text: str,
        tailored_resume_text: str,
        parsed_resume: "ParsedResume",
        analyzed_jd: "AnalyzedJD",
        fit_evaluation: "FitEvaluation",
        ats_score: Optional["ATSScore"] = None,
        user_skills: Optional[List[str]] = None,
        verified_metric_records: Optional[List[Dict[str, Any]]] = None,
        strategy_brief=None,
    ) -> ReviewResult:
        """
        Review tailored resume and make final adjustments.
        
        Args:
            original_resume_text: Original resume
            tailored_resume_text: Tailored resume to review
            parsed_resume: ParsedResume for context
            analyzed_jd: AnalyzedJD for context
            fit_evaluation: FitEvaluation for context
            ats_score: Optional ATSScore for context
            
        Returns:
            ReviewResult with reviewed resume and validation
        """
        logger.info("Review Agent: Starting review")
        
        # First, validate the tailored resume
        validation = self._validate_resume(
            original_resume_text,
            tailored_resume_text,
            analyzed_jd,
            user_skills=user_skills,
            verified_metric_records=verified_metric_records,
        )
        
        # If there are critical errors, attempt to fix them
        from ..models.agent_models import Severity
        error_issues = [issue for issue in validation.issues if issue.severity == Severity.ERROR]
        reviewed_resume = tailored_resume_text
        changes_made = []
        
        if error_issues:
            logger.info(f"Review Agent: Found {len(error_issues)} error-level issues, attempting fixes")
            fixed_resume, fixes = self._fix_errors(
                original_resume_text,
                tailored_resume_text,
                error_issues,
                parsed_resume,
                analyzed_jd
            )
            reviewed_resume = fixed_resume
            changes_made.extend(fixes)
        
        # Re-validate after fixes
        if changes_made:
            validation = self._validate_resume(
                original_resume_text,
                reviewed_resume,
                analyzed_jd,
                user_skills=user_skills,
                verified_metric_records=verified_metric_records,
            )
        
        result = ReviewResult(
            reviewed_resume=reviewed_resume,
            validation=validation,
            review_bundle=build_review_bundle(
                tailored_resume=reviewed_resume,
                validation=validation,
                ats_score=ats_score,
                fit_evaluation=fit_evaluation,
                analyzed_jd=analyzed_jd,
                strategy_brief=strategy_brief,
            ),
            changes_made=changes_made,
            final_quality_score=validation.quality_score
        )
        
        logger.info(
            "Review Agent: Review complete",
            quality_score=validation.quality_score,
            issues_count=len(validation.issues),
            changes_made=len(changes_made)
        )
        
        return result
    
    def _validate_resume(
        self,
        original_resume: str,
        tailored_resume: str,
        analyzed_jd: "AnalyzedJD",
        user_skills: Optional[List[str]] = None,
        verified_metric_records: Optional[List[Dict[str, Any]]] = None,
    ) -> ResumeValidation:
        """Validate resume quality using specialized validators"""
        from ..agents.resume_validator import (
            validate_resume_quality,
            _validate_skill_authenticity,
            _validate_experience_consistency,
            _validate_jd_coverage,
            _validate_format_structure,
            _validate_metric_provenance,
            _basic_validation,
        )
        user_skills = list(user_skills or [])
        
        # Basic validation first
        basic_issues = _basic_validation(tailored_resume, analyzed_jd.raw_text, original_resume, user_skills)
        all_issues = basic_issues.copy()
        
        # Run specialized validations (these are already optimized)
        skill_issues = _validate_skill_authenticity(
            self.llm_service, original_resume, tailored_resume, user_skills
        )
        all_issues.extend(skill_issues)
        
        experience_issues = _validate_experience_consistency(
            self.llm_service, original_resume, tailored_resume
        )
        all_issues.extend(experience_issues)
        
        jd_coverage_result = _validate_jd_coverage(
            self.llm_service, tailored_resume, analyzed_jd.raw_text
        )
        jd_coverage = jd_coverage_result.get("coverage", {})
        
        format_issues = _validate_format_structure(
            self.llm_service, tailored_resume
        )
        all_issues.extend(format_issues)

        metric_issues, metric_provenance = _validate_metric_provenance(
            original_resume, tailored_resume, verified_metric_records=verified_metric_records
        )
        all_issues.extend(metric_issues)
        
        # Calculate quality score
        from ..models.agent_models import Severity
        error_count = sum(1 for issue in all_issues if issue.severity == Severity.ERROR)
        warning_count = sum(1 for issue in all_issues if issue.severity == Severity.WARNING)
        quality_score = max(0, 100 - (error_count * 20) - (warning_count * 5))
        
        # Calculate deterministic ATS parse score for backwards-compatible validation payloads
        ats_parse_score = review_ats_parse(tailored_resume).score
        
        # Get recommendations
        recommendations = [issue.suggestion for issue in all_issues if issue.suggestion]
        
        # Calculate length metrics
        from ..config import settings
        word_count = len(tailored_resume.split())
        length_check = {
            "word_count": word_count,
            "char_count": len(tailored_resume),
            "is_reasonable": settings.resume_recommended_min_words <= word_count <= settings.resume_recommended_max_words,
            "recommended_range": f"{settings.resume_recommended_min_words}-{settings.resume_recommended_max_words} words"
        }
        
        return ResumeValidation(
            quality_score=quality_score,
            is_valid=error_count == 0,
            issues=all_issues,
            jd_coverage=jd_coverage,
            keyword_density=0.0,  # Could be calculated separately
            length_check=length_check,
            recommendations=recommendations,
            ats_score=ats_parse_score,
            metric_provenance=metric_provenance
        )
    
    def _fix_errors(
        self,
        original_resume: str,
        tailored_resume: str,
        error_issues: List[ValidationIssue],
        parsed_resume: "ParsedResume",
        analyzed_jd: "AnalyzedJD"
    ) -> tuple[str, List[str]]:
        """Attempt to fix error-level issues"""
        from langchain_core.messages import SystemMessage, HumanMessage
        
        # Build error summary
        error_summary = "\n".join([
            f"- {issue.message}" + (f" ({issue.suggestion})" if issue.suggestion else "")
            for issue in error_issues
        ])
        
        prompt = SystemMessage(content="""You are a RESUME REVIEWER. Your job is to fix critical errors in a tailored resume.

CRITICAL RULES:
- Fix ONLY the errors listed - do not make other changes
- Preserve all correct content
- Maintain resume format and structure
- DO NOT add fabricated information
- Only fix what is explicitly listed as an error

Return the corrected resume.""")
        
        human_prompt = HumanMessage(content=f"""Original Resume:
---
{original_resume[:1500]}

Tailored Resume (with errors):
---
{tailored_resume}

CRITICAL ERRORS TO FIX:
{error_summary}

Fix these errors in the tailored resume. Return the corrected version.""")
        
        try:
            response = self.llm_service.invoke_with_retry([prompt, human_prompt])
            
            # Extract resume from response (might have markdown formatting)
            if response.startswith('```'):
                lines = response.split('\n')
                if lines[0].startswith('```'):
                    response = '\n'.join(lines[1:])
                if response.endswith('```'):
                    response = response[:-3]
            
            fixes = [f"Fixed: {issue.message}" for issue in error_issues]
            return response.strip(), fixes
            
        except Exception as e:
            logger.error(f"Error fixing failed: {e}", exc_info=True)
            return tailored_resume, []  # Return original if fix fails
