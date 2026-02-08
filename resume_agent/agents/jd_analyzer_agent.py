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
        """
        Extract ALL JD information in a SINGLE structured LLM call.
        This replaces 4+ separate LLM calls with one efficient call.
        """
        from langchain_core.messages import SystemMessage, HumanMessage
        
        prompt = SystemMessage(content="""You are a JOB DESCRIPTION ANALYZER. Extract ALL information from the job description in a SINGLE structured response.

CRITICAL RULES:
- Distinguish between REQUIRED (must have) and PREFERRED (nice to have)
- For experience years: only extract if explicitly stated (e.g., "5+ years", "minimum 3 years")
- DO NOT infer or calculate years
- Extract education requirements exactly as stated
- Extract technologies, tools, and frameworks EXACTLY as mentioned

Respond with valid JSON only in this EXACT structure:
{
    "requirements": {
        "required_skills": ["Java", "Spring Boot", ...],
        "preferred_skills": ["Kubernetes", "AWS", ...],
        "required_experience_years": <number or null>,
        "required_education": [
            {"level": "Bachelor's", "field": "Computer Science"},
            {"level": "Master's", "field": null}
        ]
    },
    "responsibilities": [
        "Design and develop scalable applications",
        "Collaborate with cross-functional teams",
        ...
    ],
    "technologies": {
        "technologies": ["Java", "Python", ...],
        "tools": ["Docker", "Jenkins", ...],
        "frameworks": ["Spring Boot", "React", ...]
    },
    "role_info": {
        "job_title": "...",
        "company": "...",
        "role_type": "Full-time|Contract|Part-time|Remote",
        "location": "...",
        "industry": "...",
        "team_size": "...",
        "summary": "<2-3 sentence summary of the role>"
    }
}""")
        
        human_prompt = HumanMessage(content=f"""Job Description:
---
{jd_text}

Extract all requirements, responsibilities, technologies, and role information. Return as structured JSON.""")
        
        # Note: retry logic handled by invoke_structured, not duplicated here
        def _extract_and_validate():
            # Use structured output method
            parsed_dict = self.llm_service.invoke_structured([prompt, human_prompt], validation_retries=2)
            
            # Validate using Pydantic model (strict validation with automatic defaults)
            try:
                parsed_structured = AnalyzedJDStructured.model_validate(parsed_dict)
                logger.info("JD Analyzer Agent: Structured extraction successful and validated with Pydantic")
                
                # Convert to dict format for backward compatibility
                return {
                    "requirements": parsed_structured.requirements.model_dump(),
                    "responsibilities": parsed_structured.responsibilities,
                    "technologies": parsed_structured.technologies.model_dump(),
                    "role_info": parsed_structured.role_info.model_dump()
                }
            except ValidationError as validation_error:
                logger.warning(f"Pydantic validation failed, attempting to fix: {validation_error}")
                # Try to fix common issues and re-validate
                if not isinstance(parsed_dict, dict):
                    raise ValueError("LLM response is not a dictionary")
                
                # Ensure basic structure exists
                parsed_dict.setdefault("requirements", {})
                parsed_dict.setdefault("responsibilities", [])
                parsed_dict.setdefault("technologies", {})
                parsed_dict.setdefault("role_info", {})
                
                # Re-validate with fixed structure
                parsed_structured = AnalyzedJDStructured.model_validate(parsed_dict)
                return {
                    "requirements": parsed_structured.requirements.model_dump(),
                    "responsibilities": parsed_structured.responsibilities,
                    "technologies": parsed_structured.technologies.model_dump(),
                    "role_info": parsed_structured.role_info.model_dump()
                }
        
        try:
            return _extract_and_validate()
            
        except Exception as e:
            logger.error(f"Structured extraction failed: {e}, falling back to individual extractions", exc_info=True)
            # Fallback to old method if structured fails with defensive type checking
            requirements_result = self._extract_requirements(jd_text)
            if not isinstance(requirements_result, dict):
                requirements_result = {}
            
            responsibilities_result = self._extract_responsibilities(jd_text)
            if not isinstance(responsibilities_result, list):
                responsibilities_result = [responsibilities_result] if responsibilities_result else []
            
            technologies_result = self._extract_technologies(jd_text)
            if not isinstance(technologies_result, dict):
                technologies_result = {}
            
            role_info_result = self._extract_role_info(jd_text, job_title, company)
            if not isinstance(role_info_result, dict):
                role_info_result = {}
            
            return {
                "requirements": requirements_result,
                "responsibilities": responsibilities_result,
                "technologies": technologies_result,
                "role_info": role_info_result
            }
    
    def _extract_requirements(self, jd_text: str) -> Dict[str, Any]:
        """Extract job requirements with strict parsing"""
        from langchain_core.messages import SystemMessage, HumanMessage
        
        prompt = SystemMessage(content="""You are a JOB DESCRIPTION ANALYZER. Extract requirements from the job description.

CRITICAL RULES:
- Distinguish between REQUIRED (must have) and PREFERRED (nice to have)
- For experience years: only extract if explicitly stated (e.g., "5+ years", "minimum 3 years")
- DO NOT infer or calculate years
- Extract education requirements exactly as stated
- Be precise and accurate

Respond with valid JSON only:
{
    "required_skills": ["Java", "Spring Boot", ...],
    "preferred_skills": ["Kubernetes", "AWS", ...],
    "required_experience_years": <number or null>,
    "required_education": [
        {"level": "Bachelor's", "field": "Computer Science"},
        {"level": "Master's", "field": null}
    ]
}""")
        
        human_prompt = HumanMessage(content=f"""Job Description:
---
{jd_text}

Extract all requirements: required skills, preferred skills, experience years (if explicitly stated), and education requirements.""")
        
        try:
            response = self.llm_service.invoke_with_retry([prompt, human_prompt])
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                return json.loads(json_match.group(0))
        except Exception as e:
            logger.error(f"Requirements extraction failed: {e}", exc_info=True)
        
        return {"required_skills": [], "preferred_skills": [], "required_experience_years": None, "required_education": []}
    
    def _extract_responsibilities(self, jd_text: str) -> List[str]:
        """Extract key responsibilities"""
        from langchain_core.messages import SystemMessage, HumanMessage
        
        prompt = SystemMessage(content="""You are a JOB DESCRIPTION ANALYZER. Extract key responsibilities from the job description.

Extract the main responsibilities as a list. Be concise but comprehensive.

Respond with valid JSON only:
{
    "responsibilities": [
        "Design and develop scalable applications",
        "Collaborate with cross-functional teams",
        ...
    ]
}""")
        
        human_prompt = HumanMessage(content=f"""Job Description:
---
{jd_text}

Extract key responsibilities as a list.""")
        
        try:
            response = self.llm_service.invoke_with_retry([prompt, human_prompt])
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group(0))
                return data.get("responsibilities", [])
        except Exception as e:
            logger.error(f"Responsibilities extraction failed: {e}", exc_info=True)
        
        return []
    
    def _extract_technologies(self, jd_text: str) -> Dict[str, List[str]]:
        """Extract technologies, tools, and frameworks needed"""
        from langchain_core.messages import SystemMessage, HumanMessage
        
        prompt = SystemMessage(content="""You are a JOB DESCRIPTION ANALYZER. Extract technologies, tools, and frameworks mentioned in the job description.

Extract EXACTLY as mentioned - do not infer.

Respond with valid JSON only:
{
    "technologies": ["Java", "Python", ...],
    "tools": ["Docker", "Jenkins", ...],
    "frameworks": ["Spring Boot", "React", ...]
}""")
        
        human_prompt = HumanMessage(content=f"""Job Description:
---
{jd_text}

Extract all technologies, tools, and frameworks mentioned.""")
        
        try:
            response = self.llm_service.invoke_with_retry([prompt, human_prompt])
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                return json.loads(json_match.group(0))
        except Exception as e:
            logger.error(f"Technologies extraction failed: {e}", exc_info=True)
        
        return {"technologies": [], "tools": [], "frameworks": []}
    
    def _extract_role_info(self, jd_text: str, job_title: Optional[str], company: Optional[str]) -> Dict[str, Any]:
        """Extract role information"""
        from langchain_core.messages import SystemMessage, HumanMessage
        
        prompt = SystemMessage(content="""You are a JOB DESCRIPTION ANALYZER. Extract role information from the job description.

Extract: job title, company, role type (Full-time, Contract, etc.), location, industry, team size, and a brief summary.

Respond with valid JSON only:
{
    "job_title": "...",
    "company": "...",
    "role_type": "Full-time|Contract|Part-time|Remote",
    "location": "...",
    "industry": "...",
    "team_size": "...",
    "summary": "<2-3 sentence summary of the role>"
}""")
        
        human_prompt = HumanMessage(content=f"""Job Description:
---
{jd_text}

Extract role information.""")
        
        try:
            response = self.llm_service.invoke_with_retry([prompt, human_prompt])
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group(0))
                # Override with provided values if available
                if job_title:
                    data["job_title"] = job_title
                if company:
                    data["company"] = company
                return data
        except Exception as e:
            logger.error(f"Role info extraction failed: {e}", exc_info=True)
        
        return {
            "job_title": job_title or "Unknown",
            "company": company,
            "role_type": "Unknown",
            "location": None,
            "industry": None,
            "team_size": None,
            "summary": ""
        }
