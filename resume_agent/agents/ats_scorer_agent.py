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


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def _collect_date_styles(text: str) -> set[str]:
    styles: set[str] = set()
    lowered = text.lower()
    if re.search(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+\d{4}\b", lowered):
        styles.add("month_year")
    if re.search(r"\b\d{1,2}/\d{4}\b", lowered):
        styles.add("numeric_month_year")
    if re.search(r"\b\d{4}\s*[-/]\s*\d{2}\b", lowered):
        styles.add("iso_month")
    if re.search(r"\b\d{4}\s*[-–]\s*(?:present|\d{4})\b", lowered):
        styles.add("year_range")
    return styles


def keyword_density_score(keyword_matches: Dict[str, int], missing_keywords: List[str]) -> float:
    total_keywords = len(keyword_matches) + len(missing_keywords)
    if total_keywords <= 0:
        return 0.0
    return len(keyword_matches) / total_keywords


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

        deterministic = self._calculate_deterministic_signals(
            resume_text=resume_text,
            analyzed_jd=analyzed_jd,
            parsed_resume=parsed_resume,
            keyword_matches=keyword_matches,
            missing_keywords=missing_keywords,
        )
        
        # Get detailed scoring from LLM
        detailed_score = self._calculate_detailed_score(resume_text, analyzed_jd, keyword_matches, missing_keywords)
        
        # Calculate overall ATS score
        # Weight: deterministic discoverability dominates, LLM content review supports.
        keyword_score = deterministic["keyword_score"]
        format_score = int(round((deterministic["format_score"] * 0.7) + (detailed_score.get("format_score", 75) * 0.3)))
        content_score = int(round((deterministic["content_score"] * 0.65) + (detailed_score.get("content_score", 75) * 0.35)))
        
        ats_score = int(
            (keyword_score * 0.45) +
            (format_score * 0.3) +
            (content_score * 0.25)
        )
        recommendations = list(dict.fromkeys(deterministic["recommendations"] + detailed_score.get("recommendations", [])))
        
        score = ATSScore(
            score=ats_score,
            keyword_density=keyword_density,
            keyword_matches=keyword_matches,
            missing_keywords=missing_keywords,
            format_score=format_score,
            content_score=content_score,
            recommendations=recommendations
        )
        
        logger.info(
            "ATS Scorer Agent: Scoring complete",
            ats_score=ats_score,
            keyword_density=keyword_density,
            missing_keywords_count=len(missing_keywords),
            exact_title_match=deterministic["exact_title_match"],
            deterministic_format_score=deterministic["format_score"],
            deterministic_content_score=deterministic["content_score"],
        )
        
        return score

    def _calculate_deterministic_signals(
        self,
        resume_text: str,
        analyzed_jd: "AnalyzedJD",
        parsed_resume: "ParsedResume",
        keyword_matches: Dict[str, int],
        missing_keywords: List[str],
    ) -> Dict[str, Any]:
        header_lines = [line.strip() for line in resume_text.splitlines()[:8] if line.strip()]
        header_text = "\n".join(header_lines)
        lower_text = resume_text.lower()
        core_sections = ("experience", "education", "skills")
        found_sections = [section for section in core_sections if re.search(rf"\b{re.escape(section)}\b", lower_text)]
        date_styles = _collect_date_styles(resume_text)

        exact_title_match = self._score_exact_title_match(header_text, resume_text, analyzed_jd.job_title)
        section_score = 100 if len(found_sections) == len(core_sections) else (80 if len(found_sections) == 2 else 50)
        date_score = 100 if len(date_styles) <= 1 else (78 if len(date_styles) == 2 else 55)
        parse_score = self._score_parse_safety(resume_text, header_text)
        stuffing_penalty = self._keyword_stuffing_penalty(keyword_matches, resume_text)
        keyword_score = int(keyword_density_score(keyword_matches, missing_keywords) * 100)
        content_score = int(round((exact_title_match * 0.35) + (keyword_score * 0.45) + (date_score * 0.20))) - stuffing_penalty
        format_score = int(round((parse_score * 0.45) + (section_score * 0.3) + (date_score * 0.25)))

        recommendations: List[str] = []
        if exact_title_match < 100 and analyzed_jd.job_title:
            recommendations.append(f"Use the exact job title '{analyzed_jd.job_title}' in the resume header or summary.")
        if len(found_sections) < len(core_sections):
            recommendations.append("Use standard section headers like Work Experience, Education, and Skills.")
        if len(date_styles) > 1:
            recommendations.append("Standardize date formatting across roles, such as 'Jan 2024 - Present'.")
        if parse_score < 90:
            recommendations.append("Keep the layout ATS-safe: single column, plain bullets, and no decorative symbols or table-like formatting.")
        if missing_keywords:
            recommendations.append(f"Add missing JD terms where truthful: {', '.join(missing_keywords[:6])}{'...' if len(missing_keywords) > 6 else ''}")
        if stuffing_penalty > 0:
            recommendations.append("Reduce repeated keyword phrasing and keep JD terms integrated naturally.")

        return {
            "exact_title_match": exact_title_match,
            "format_score": max(0, min(100, format_score)),
            "content_score": max(0, min(100, content_score)),
            "keyword_score": max(0, min(100, keyword_score - stuffing_penalty)),
            "recommendations": recommendations,
        }

    def _score_exact_title_match(self, header_text: str, resume_text: str, jd_title: Optional[str]) -> int:
        title = (jd_title or "").strip()
        if not title:
            return 80
        normalized_title = _normalize_whitespace(title)
        normalized_header = _normalize_whitespace(header_text)
        normalized_resume = _normalize_whitespace(resume_text[:1200])
        if normalized_title in normalized_header:
            return 100
        if normalized_title in normalized_resume:
            return 88
        title_tokens = {token for token in re.findall(r"\w+", normalized_title) if len(token) > 2}
        if not title_tokens:
            return 80
        header_tokens = set(re.findall(r"\w+", normalized_header))
        overlap = len(title_tokens & header_tokens) / len(title_tokens)
        if overlap >= 0.75:
            return 72
        if overlap >= 0.5:
            return 55
        return 30

    def _score_parse_safety(self, resume_text: str, header_text: str) -> int:
        score = 100
        if re.search(r"[│┆┊┃┏┓┗┛╭╮╯╰■◆●☑✓★😀-🙏]", resume_text):
            score -= 10
        if re.search(r"\|.+\|", resume_text) or re.search(r"<table|<tr|<td", resume_text, re.IGNORECASE):
            score -= 18
        if re.search(r"^\s{20,}.*\s{20,}", resume_text, re.MULTILINE):
            score -= 12
        has_email_in_header = bool(re.search(r"[\w.\-+]+@[\w.\-]+\.\w+", header_text))
        has_phone_in_header = bool(re.search(r"(?:\+?\d[\d\s().-]{8,}\d)", header_text))
        if not has_email_in_header or not has_phone_in_header:
            score -= 8
        return max(0, score)

    def _keyword_stuffing_penalty(self, keyword_matches: Dict[str, int], resume_text: str) -> int:
        if not keyword_matches:
            return 0
        word_count = max(1, len(re.findall(r"\b\w+\b", resume_text)))
        repeated_terms = sum(1 for count in keyword_matches.values() if count >= 6)
        total_hits = sum(keyword_matches.values())
        density = total_hits / word_count
        penalty = 0
        if repeated_terms >= 4:
            penalty += 8
        if density > 0.12:
            penalty += 8
        elif density > 0.09:
            penalty += 4
        return penalty
    
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
