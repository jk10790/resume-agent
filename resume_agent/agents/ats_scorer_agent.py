"""
ATS Scorer Agent
Strictly responsible for calculating ATS (Applicant Tracking System) score.
This agent ONLY scores - it does NOT modify or tailor anything.
"""

from typing import Dict, List, Any, Optional, TYPE_CHECKING
from pydantic import ValidationError
from ..services.llm_service import LLMService
from ..utils.logger import logger
from ..models.agent_models import ATSScore, ATSScoreStructured
import json
import re

if TYPE_CHECKING:
    from ..models.agent_models import ParsedResume, AnalyzedJD


def _count_keyword_matches(text: str, keyword: str) -> int:
    """
    Count keyword occurrences using word boundaries to avoid false matches.
    E.g., 'Java' won't match inside 'JavaScript'.
    """
    # Escape special regex characters in keyword
    escaped_keyword = re.escape(keyword)
    # Use word boundaries for whole-word matching (case-insensitive)
    pattern = rf'\b{escaped_keyword}\b'
    matches = re.findall(pattern, text, re.IGNORECASE)
    return len(matches)


class ATSScorerAgent:
    """
    Agent responsible ONLY for calculating ATS score.
    This agent does NOT modify or tailor the resume.
    """
    
    def __init__(self, llm_service: LLMService):
        self.llm_service = llm_service
    
    def score(
        self,
        resume_text: str,
        analyzed_jd: "AnalyzedJD",
        parsed_resume: "ParsedResume"
    ) -> ATSScore:
        """
        Calculate ATS score for resume against job description.
        
        Args:
            resume_text: Tailored resume text
            analyzed_jd: AnalyzedJD object
            parsed_resume: ParsedResume object (for context)
            
        Returns:
            ATSScore with detailed scoring
        """
        logger.info("ATS Scorer Agent: Starting scoring")
        
        # Extract keywords from JD
        all_keywords = set()
        all_keywords.update(analyzed_jd.required_skills)
        all_keywords.update(analyzed_jd.preferred_skills)
        all_keywords.update(analyzed_jd.technologies_needed)
        all_keywords.update(analyzed_jd.tools_needed)
        all_keywords.update(analyzed_jd.frameworks_needed)
        
        # Count keyword matches using word boundaries (avoids 'Java' matching 'JavaScript')
        keyword_matches = {}
        for keyword in all_keywords:
            count = _count_keyword_matches(resume_text, keyword)
            if count > 0:
                keyword_matches[keyword] = count
        
        missing_keywords = [kw for kw in all_keywords if _count_keyword_matches(resume_text, kw) == 0]
        
        # Calculate keyword density
        total_keywords = len(all_keywords)
        matched_keywords = len(keyword_matches)
        keyword_density = matched_keywords / total_keywords if total_keywords > 0 else 0.0
        
        # Get detailed scoring from LLM
        detailed_score = self._calculate_detailed_score(resume_text, analyzed_jd, keyword_matches, missing_keywords)
        
        # Calculate overall ATS score
        # Weight: 40% keyword match, 30% format, 30% content
        keyword_score = int(keyword_density * 100)
        format_score = detailed_score.get("format_score", 75)
        content_score = detailed_score.get("content_score", 75)
        
        ats_score = int(
            (keyword_score * 0.4) +
            (format_score * 0.3) +
            (content_score * 0.3)
        )
        
        score = ATSScore(
            score=ats_score,
            keyword_density=keyword_density,
            keyword_matches=keyword_matches,
            missing_keywords=missing_keywords,
            format_score=format_score,
            content_score=content_score,
            recommendations=detailed_score.get("recommendations", [])
        )
        
        logger.info(
            "ATS Scorer Agent: Scoring complete",
            ats_score=ats_score,
            keyword_density=keyword_density,
            missing_keywords_count=len(missing_keywords)
        )
        
        return score
    
    def _calculate_detailed_score(
        self,
        resume_text: str,
        analyzed_jd: "AnalyzedJD",
        keyword_matches: Dict[str, int],
        missing_keywords: List[str]
    ) -> Dict[str, Any]:
        """Get detailed scoring from LLM"""
        from langchain_core.messages import SystemMessage, HumanMessage
        
        prompt = SystemMessage(content="""You are an ATS SCORER. Your ONLY job is to evaluate how ATS-friendly a resume is.

CRITICAL RULES:
- Evaluate format/structure (proper sections, clean formatting, ATS-friendly)
- Evaluate content quality (relevance, clarity, completeness)
- Be realistic - most resumes score 60-85, not 95+
- Provide specific recommendations for improvement

Respond with valid JSON only:
{
    "format_score": <0-100>,
    "content_score": <0-100>,
    "recommendations": ["...", ...]
}""")
        
        summary = f"""Resume length: {len(resume_text)} characters
Keyword matches: {len(keyword_matches)}/{len(keyword_matches) + len(missing_keywords)}
Missing keywords: {', '.join(missing_keywords[:10])}{'...' if len(missing_keywords) > 10 else ''}

Job Requirements:
- Required Skills: {', '.join(analyzed_jd.required_skills[:10])}
- Technologies: {', '.join(analyzed_jd.technologies_needed[:10])}

Evaluate the resume's ATS-friendliness and provide scores."""
        
        human_prompt = HumanMessage(content=f"""Resume (first 2000 chars):
---
{resume_text[:2000]}

{summary}

Provide format score, content score, and recommendations.""")
        
        # Note: retry logic handled by invoke_with_retry, not duplicated here
        def _score_and_validate():
            response = self.llm_service.invoke_with_retry([prompt, human_prompt])
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group(0))
                
                # Validate using Pydantic model
                try:
                    structured_score = ATSScoreStructured.model_validate(data)
                    logger.info("ATS Scorer Agent: Structured scoring validated with Pydantic")
                    return structured_score.model_dump()
                except ValidationError as validation_error:
                    logger.warning(f"Pydantic validation failed for ATS score, using fallback: {validation_error}")
                    # Fallback to manual defaults
                    return {
                        "format_score": data.get("format_score", 75),
                        "content_score": data.get("content_score", 75),
                        "recommendations": data.get("recommendations", [])
                    }
            raise ValueError("No JSON found in LLM response")
        
        try:
            return _score_and_validate()
        except Exception as e:
            logger.error(f"Detailed ATS scoring failed: {e}", exc_info=True)
            return {
                "format_score": 75,
                "content_score": 75,
                "recommendations": []
            }
