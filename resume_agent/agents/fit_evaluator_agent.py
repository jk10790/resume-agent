"""
Fit Evaluator Agent
Strictly responsible for mapping parsed resume to analyzed JD and determining fit.
This agent ONLY evaluates fit - it does NOT tailor or modify anything.
"""

from typing import Dict, List, Any, TYPE_CHECKING
from pydantic import ValidationError
from ..services.llm_service import LLMService
from ..utils.logger import logger
from ..models.resume import FitEvaluation
from ..models.agent_models import FitAnalysis, FitAnalysisStructured
from ..storage.user_memory import get_skills
import json
import re

if TYPE_CHECKING:
    from ..models.agent_models import ParsedResume, AnalyzedJD


def _normalize_skill(skill: str) -> str:
    """Normalize skill for case-insensitive matching"""
    return skill.lower().strip()


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
    
    def __init__(self, llm_service: LLMService):
        self.llm_service = llm_service
        self.confirmed_skills = get_skills()
    
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
        """Perform detailed fit analysis using LLM"""
        from langchain_core.messages import SystemMessage, HumanMessage
        
        # Prepare structured data for LLM
        resume_skills = set(parsed_resume.all_skills)
        resume_skills.update(self.confirmed_skills)  # Include confirmed skills
        
        jd_required = set(analyzed_jd.required_skills)
        jd_preferred = set(analyzed_jd.preferred_skills)
        
        # Use case-insensitive matching for skills
        matching_skills, missing_required = _case_insensitive_skill_match(resume_skills, jd_required)
        matching_preferred, _ = _case_insensitive_skill_match(resume_skills, jd_preferred)
        
        prompt = SystemMessage(content="""You are a FIT EVALUATOR. Your ONLY job is to evaluate how well a candidate's resume matches a job description.

CRITICAL RULES:
1. DISTINGUISH between skill types:
   - TECHNICAL SKILLS: Java, Python, Docker, Kubernetes, AWS, Spring Boot, etc. (match literally)
   - CONTEXTUAL REQUIREMENTS: "large codebases", "distributed systems", "cross-functional teams" (INFER from technical skills)
   - SOFT SKILLS: Communication, teamwork, leadership (give less weight)

2. INFERENCE RULES for contextual requirements:
   - Docker + Kubernetes + Kafka + AWS → IMPLIES "distributed systems" experience
   - Spring Boot + Microservices + Docker → IMPLIES "modern software development"
   - Any enterprise tech stack → IMPLIES "large codebases" experience
   - Worked at multiple companies → IMPLIES "cross-functional" experience
   - CI/CD + Jenkins + Git → IMPLIES "software development lifecycle" experience

3. DO NOT penalize for missing generic/contextual requirements if the candidate has technical skills that demonstrate that experience

4. FOCUS on actual technical skill gaps - these matter more than generic phrases

5. Score Guidelines:
   - 7-10: Strong match on core technical skills
   - 5-6: Missing some technical skills but has related experience
   - 3-4: Missing significant technical skills
   - 1-2: Completely different domain/skills

Respond with valid JSON only:
{
    "fit_score": <1-10>,
    "should_apply": <true/false>,
    "confidence": <0.0-1.0>,
    "experience_match": "exceeds|meets|below",
    "experience_gap_years": <number or null>,
    "education_match": <true/false>,
    "strengths": ["...", ...],
    "weaknesses": ["...", ...],
    "recommendations": ["...", ...],
    "matching_areas": ["Include INFERRED matches like 'Distributed Systems (from Docker, Kubernetes, Kafka)'", ...],
    "missing_areas": ["Only list ACTUAL technical skill gaps, not generic phrases", ...]
}""")
        
        # Build concise comparison (truncate to avoid token limits)
        skills_summary = ', '.join(parsed_resume.all_skills[:20])
        if len(parsed_resume.all_skills) > 20:
            skills_summary += f" (and {len(parsed_resume.all_skills) - 20} more)"
        
        required_skills_summary = ', '.join(analyzed_jd.required_skills[:15])
        if len(analyzed_jd.required_skills) > 15:
            required_skills_summary += f" (and {len(analyzed_jd.required_skills) - 15} more)"
        
        matching_summary = ', '.join(matching_skills[:10]) if matching_skills else 'None'
        missing_summary = ', '.join(missing_required[:10]) if missing_required else 'None'
        
        # Categorize technical vs contextual requirements for better analysis
        technical_skills_in_resume = [s for s in parsed_resume.all_skills if len(s) < 30]  # Technical skills are usually short
        
        comparison_text = f"""RESUME ANALYSIS:
- Technical Skills: {skills_summary}
- Total Skills Count: {len(parsed_resume.all_skills)} skills
- Experience: {parsed_resume.total_years_experience or 'Not explicitly stated'} years
- Job Titles: {', '.join(parsed_resume.job_titles[:3])}
- Education: {', '.join([f"{e.get('degree', '')} in {e.get('field', '')}" for e in parsed_resume.education[:2]])}

JOB REQUIREMENTS:
- Required Skills/Requirements: {required_skills_summary}
- Preferred Skills: {', '.join(analyzed_jd.preferred_skills[:10])}
- Required Experience: {analyzed_jd.required_experience_years or 'Not specified'} years

LITERAL MATCHING (case-insensitive):
- Direct Matches: {matching_summary}
- Not Literally Matched: {missing_summary}

IMPORTANT: Many "missing" items above may be GENERIC REQUIREMENTS (like "large codebases", "distributed systems") 
that should be INFERRED from the candidate's technical skills. 

For example, if candidate has Docker, Kubernetes, Kafka, AWS, Spring Boot - they clearly have experience with 
distributed systems and modern software development, even if those exact phrases aren't in their resume.

Evaluate the TRUE fit considering both literal and inferred matches."""
        
        human_prompt = HumanMessage(content=comparison_text)
        
        # Note: retry logic is handled at module level, not per-call
        def _analyze_and_validate():
            response = self.llm_service.invoke_with_retry([prompt, human_prompt])
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group(0))
                
                # Validate using Pydantic model
                try:
                    structured_analysis = FitAnalysisStructured.model_validate(data)
                    
                    # Build complete FitAnalysis with matching data
                    return FitAnalysis(
                        fit_score=structured_analysis.fit_score,
                        should_apply=structured_analysis.should_apply,
                        confidence=structured_analysis.confidence,
                        matching_skills=matching_skills,
                        missing_required_skills=missing_required,
                        matching_preferred_skills=matching_preferred,
                        experience_match=structured_analysis.experience_match,
                        experience_gap_years=structured_analysis.experience_gap_years,
                        education_match=structured_analysis.education_match,
                        missing_education=structured_analysis.missing_education,
                        strengths=structured_analysis.strengths,
                        weaknesses=structured_analysis.weaknesses,
                        recommendations=structured_analysis.recommendations,
                        matching_areas=structured_analysis.matching_areas,
                        missing_areas=structured_analysis.missing_areas
                    )
                except ValidationError as validation_error:
                    logger.warning(f"Pydantic validation failed for fit analysis, using fallback: {validation_error}")
                    # Fallback to manual construction with defaults
                    return FitAnalysis(
                        fit_score=data.get("fit_score", 5),
                        should_apply=data.get("should_apply", False),
                        confidence=data.get("confidence", 0.5),
                        matching_skills=matching_skills,
                        missing_required_skills=missing_required,
                        matching_preferred_skills=matching_preferred,
                        experience_match=data.get("experience_match", "unknown"),
                        experience_gap_years=data.get("experience_gap_years"),
                        education_match=data.get("education_match", False),
                        missing_education=data.get("missing_education", []),
                        strengths=data.get("strengths", []),
                        weaknesses=data.get("weaknesses", []),
                        recommendations=data.get("recommendations", []),
                        matching_areas=data.get("matching_areas", []),
                        missing_areas=data.get("missing_areas", [])
                    )
            raise ValueError("No JSON found in LLM response")
        
        try:
            return _analyze_and_validate()
        except Exception as e:
            logger.error(f"Fit analysis failed: {e}", exc_info=True)
        
        # Fallback analysis
        return FitAnalysis(
            fit_score=5,
            should_apply=len(missing_required) == 0,
            confidence=0.5,
            matching_skills=matching_skills,
            missing_required_skills=missing_required,
            matching_preferred_skills=matching_preferred,
            experience_match="unknown",
            experience_gap_years=None,
            education_match=False,
            missing_education=[],
            strengths=[],
            weaknesses=missing_required,
            recommendations=[],
            matching_areas=matching_skills[:5],
            missing_areas=missing_required[:5]
        )
