"""
Fit Evaluator Agent
Strictly responsible for mapping parsed resume to analyzed JD and determining fit.
This agent ONLY evaluates fit - it does NOT tailor or modify anything.
"""

from typing import Dict, List, Any, Optional, TYPE_CHECKING
from ..services.llm_service import LLMService
from ..utils.logger import logger
from ..utils.exceptions import FitEvaluationUnavailable
from ..models.resume import FitEvaluation
from ..models.agent_models import FitAnalysis, FitAnalysisStructured

if TYPE_CHECKING:
    from ..models.agent_models import ParsedResume, AnalyzedJD


def _normalize_skill(skill: str) -> str:
    """Normalize skill for case-insensitive matching"""
    return skill.lower().strip()


def _summarize(items: list, limit: int) -> str:
    """Comma-joined head of a list, noting how many were left out."""
    head = ", ".join(items[:limit])
    if len(items) > limit:
        head += f" (and {len(items) - limit} more)"
    return head or "None"


def _case_insensitive_skill_match(resume_skills: set, jd_skills: set) -> tuple:
    """
    Perform case-insensitive skill matching.
    Returns (matching_skills, missing_skills) with original casing preserved.
    """
    # Create lowercase mapping
    resume_lower = {_normalize_skill(s): s for s in resume_skills}
    jd_lower = {_normalize_skill(s): s for s in jd_skills}
    
    # Find matches (case-insensitive)
    matching_lower = set(resume_lower.keys()) & set(jd_lower.keys())
    missing_lower = set(jd_lower.keys()) - set(resume_lower.keys())
    
    # Return with original JD casing
    matching = [jd_lower[s] for s in matching_lower if s in jd_lower]
    missing = [jd_lower[s] for s in missing_lower if s in jd_lower]
    
    return matching, missing


class FitEvaluatorAgent:
    """
    Agent responsible ONLY for evaluating fit between resume and job description.
    This agent does NOT tailor or modify the resume.
    """
    
    def __init__(self, llm_service: LLMService, confirmed_skills: Optional[List[str]] = None):
        self.llm_service = llm_service
        self.confirmed_skills = list(confirmed_skills or [])
    
    def evaluate_fit(
        self,
        parsed_resume: "ParsedResume",
        analyzed_jd: "AnalyzedJD"
    ) -> FitEvaluation:
        """
        Evaluate fit between parsed resume and analyzed JD.
        
        Args:
            parsed_resume: ParsedResume object from ResumeParserAgent
            analyzed_jd: AnalyzedJD object from JDAnalyzerAgent
            
        Returns:
            FitEvaluation with score and recommendations
        """
        logger.info("Fit Evaluator Agent: Starting evaluation")
        
        # Perform detailed fit analysis
        fit_analysis = self._analyze_fit(parsed_resume, analyzed_jd)
        
        # Convert to FitEvaluation format
        evaluation = FitEvaluation(
            score=fit_analysis.fit_score,
            should_apply=fit_analysis.should_apply,
            confidence=fit_analysis.confidence,
            matching_areas=fit_analysis.matching_areas,
            missing_areas=fit_analysis.missing_areas,
            recommendations=fit_analysis.recommendations
        )
        
        logger.info(
            "Fit Evaluator Agent: Evaluation complete",
            score=fit_analysis.fit_score,
            should_apply=fit_analysis.should_apply,
            missing_required=len(fit_analysis.missing_required_skills)
        )
        
        return evaluation
    
    def _analyze_fit(self, parsed_resume: "ParsedResume", analyzed_jd: "AnalyzedJD") -> FitAnalysis:
        """Judge fit, grounded in the resume text and a deterministic skill overlap.

        The exact skill overlap is computed locally: it is free, it is not a
        guess, and it gives the model a factual starting point. The prompt itself
        lives in the task library.
        """
        resume_skills = set(parsed_resume.all_skills)
        resume_skills.update(self.confirmed_skills)

        matching_skills, missing_required = _case_insensitive_skill_match(
            resume_skills, set(analyzed_jd.required_skills)
        )
        matching_preferred, _ = _case_insensitive_skill_match(
            resume_skills, set(analyzed_jd.preferred_skills)
        )

        raw_text = parsed_resume.raw_text or ""
        education = ", ".join(
            f"{entry.get('degree', '')} in {entry.get('field', '')}"
            for entry in (parsed_resume.education or [])[:2]
        )

        try:
            data = self.llm_service.run_task(
                "fit.evaluate",
                resume_excerpt=raw_text[:6000] + ("\n[resume truncated]" if len(raw_text) > 6000 else ""),
                skills_summary=_summarize(parsed_resume.all_skills, 20),
                skills_count=len(parsed_resume.all_skills),
                experience_years=parsed_resume.total_years_experience or "Not explicitly stated",
                job_titles=", ".join(parsed_resume.job_titles[:3]),
                education=education,
                required_skills=_summarize(analyzed_jd.required_skills, 15),
                preferred_skills=", ".join(analyzed_jd.preferred_skills[:10]),
                required_experience_years=analyzed_jd.required_experience_years or "Not specified",
                matching_summary=", ".join(matching_skills[:10]) or "None",
                missing_summary=", ".join(missing_required[:10]) or "None",
            )
            structured = FitAnalysisStructured.model_validate(data)
        except Exception as e:
            logger.error(f"Fit analysis failed: {e}", exc_info=True)
            # No usable judgement came back, so there is no score to report. The
            # local skill overlap alone cannot stand in for one: a 5/10 built
            # from it is indistinguishable from a real 5/10 by the time it
            # reaches the UI or a persisted discovered_roles row.
            raise FitEvaluationUnavailable(
                f"The model did not return a usable fit analysis: {e}"
            ) from e

        return FitAnalysis(
            fit_score=structured.fit_score,
            should_apply=structured.should_apply,
            confidence=structured.confidence,
            matching_skills=matching_skills,
            missing_required_skills=missing_required,
            matching_preferred_skills=matching_preferred,
            recommendations=structured.recommendations,
            matching_areas=structured.matching_areas,
            missing_areas=structured.missing_areas,
        )
