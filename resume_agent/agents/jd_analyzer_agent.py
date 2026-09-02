"""
JD Analyzer Agent
Strictly responsible for understanding the job description, role requirements, and skills/experience needed.
This agent ONLY analyzes the JD - it does NOT compare to resume or determine fit.
"""

from typing import Dict, List, Any, Optional
from pydantic import ValidationError
from ..services.llm_service import LLMService
from ..utils.logger import logger
from ..models.agent_models import AnalyzedJD, AnalyzedJDStructured
import json
import re


class JDAnalyzerAgent:
    """
    Agent responsible ONLY for analyzing and understanding a job description.
    This agent does NOT compare to resume or determine fit.
    """
    
    def __init__(self, llm_service: LLMService):
        self.llm_service = llm_service
    
    def analyze(self, jd_text: str, job_title: Optional[str] = None, company: Optional[str] = None, use_cache: bool = True) -> AnalyzedJD:
        """
        Analyze job description and extract all requirements in a SINGLE LLM call.
        Uses caching to avoid redundant analysis.
        
        Args:
            jd_text: Raw job description text
            job_title: Optional job title (if known)
            company: Optional company name (if known)
            use_cache: Whether to use cache (default: True)
            
        Returns:
            AnalyzedJD with structured requirements
        """
        logger.info("JD Analyzer Agent: Starting analysis", jd_length=len(jd_text))
        
        # Check cache first
        if use_cache:
            from ..utils.agent_cache import get_agent_cache
            cache = get_agent_cache()
            cached_data = cache.get_analyzed_jd(jd_text)
            if cached_data:
                logger.info("JD Analyzer Agent: Using cached analyzed JD")
                analyzed_data = cached_data
            else:
                # Extract everything in ONE structured LLM call
                analyzed_data = self._extract_all_structured(jd_text, job_title, company)
                # Cache the result
                cache.set_analyzed_jd(jd_text, analyzed_data)
        else:
            # Extract everything in ONE structured LLM call
            analyzed_data = self._extract_all_structured(jd_text, job_title, company)
        
        # Handle case where LLM returns a list instead of dict
        if isinstance(analyzed_data, list):
            logger.warning("JD analyzer returned list instead of dict, extracting first element")
            analyzed_data = analyzed_data[0] if analyzed_data else {}
        
        if not isinstance(analyzed_data, dict):
            logger.error(f"JD analyzer returned unexpected type: {type(analyzed_data)}")
            analyzed_data = {}
        
        # Extract from structured data with defensive type checking
        requirements = analyzed_data.get("requirements", {})
        if isinstance(requirements, list):
            requirements = requirements[0] if requirements else {}
        if not isinstance(requirements, dict):
            requirements = {}
            
        responsibilities = analyzed_data.get("responsibilities", [])
        if not isinstance(responsibilities, list):
            responsibilities = [responsibilities] if responsibilities else []
            
        technologies = analyzed_data.get("technologies", {})
        if isinstance(technologies, list):
            technologies = technologies[0] if technologies else {}
        if not isinstance(technologies, dict):
            technologies = {}
            
        role_info = analyzed_data.get("role_info", {})
        if isinstance(role_info, list):
            role_info = role_info[0] if role_info else {}
        if not isinstance(role_info, dict):
            role_info = {}
        
        # Override with provided values if available
        if job_title:
            role_info["job_title"] = job_title
        if company:
            role_info["company"] = company
        
        # Clean education entries - convert None values to empty strings
        raw_education = requirements.get("required_education", [])
        cleaned_education = []
        for edu in raw_education:
            if isinstance(edu, dict):
                cleaned_edu = {k: (v if v is not None else "") for k, v in edu.items()}
                cleaned_education.append(cleaned_edu)
        
        analyzed = AnalyzedJD(
            job_title=role_info.get("job_title", job_title or "Unknown"),
            company=role_info.get("company", company),
            role_type=role_info.get("role_type", "Unknown"),
            location=role_info.get("location"),
            required_skills=requirements.get("required_skills", []),
            preferred_skills=requirements.get("preferred_skills", []),
            required_experience_years=requirements.get("required_experience_years"),
            required_education=cleaned_education,
            key_responsibilities=responsibilities,
            technologies_needed=technologies.get("technologies", []),
            tools_needed=technologies.get("tools", []),
            frameworks_needed=technologies.get("frameworks", []),
            industry=role_info.get("industry"),
            team_size=role_info.get("team_size"),
            summary=role_info.get("summary", ""),
            raw_text=jd_text
        )
        
        logger.info(
            "JD Analyzer Agent: Analysis complete",
            required_skills_count=len(analyzed.required_skills),
            responsibilities_count=len(analyzed.key_responsibilities),
            technologies_count=len(analyzed.technologies_needed)
        )
        
        return analyzed
    
    def _extract_all_structured(self, jd_text: str, job_title: Optional[str], company: Optional[str]) -> Dict[str, Any]:
        """Extract requirements, responsibilities, technologies and role info in one call.

        The prompt, tier and output format are declared in the task library.

        The four per-field fallback extractions that used to follow a schema
        failure are gone: they cost four more LLM calls to produce a partial
        analysis that downstream steps could not distinguish from a complete one.
        """
        parsed_dict = self.llm_service.run_task("jd.analyze", jd_text=jd_text)
        if not isinstance(parsed_dict, dict):
            raise ValueError("JD analyzer did not return a JSON object")

        parsed_dict.setdefault("requirements", {})
        parsed_dict.setdefault("responsibilities", [])
        parsed_dict.setdefault("technologies", {})
        parsed_dict.setdefault("role_info", {})

        parsed_structured = AnalyzedJDStructured.model_validate(parsed_dict)
        logger.info("JD Analyzer Agent: Structured extraction validated")
        return {
            "requirements": parsed_structured.requirements.model_dump(),
            "responsibilities": parsed_structured.responsibilities,
            "technologies": parsed_structured.technologies.model_dump(),
            "role_info": parsed_structured.role_info.model_dump(),
        }
