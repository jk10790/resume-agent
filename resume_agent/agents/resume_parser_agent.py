"""
Resume Parser Agent
Strictly responsible for parsing resume, understanding skillset, and user experience.
This agent ONLY parses and extracts - it does NOT modify or tailor anything.
"""

from typing import Dict, List, Any, Optional
from pydantic import ValidationError
from ..services.llm_service import LLMService
from ..utils.logger import logger
from ..storage.user_memory import get_skills
from ..models.agent_models import ParsedResume, ParsedResumeStructured
import json
import re


class ResumeParserAgent:
    """
    Agent responsible ONLY for parsing and understanding a resume.
    This agent does NOT modify, tailor, or suggest changes.
    """
    
    def __init__(self, llm_service: LLMService):
        self.llm_service = llm_service
        self.confirmed_skills = get_skills()  # User's confirmed skills
    
    def parse(self, resume_text: str, use_cache: bool = True) -> ParsedResume:
        """
        Parse resume and extract all relevant information in a SINGLE LLM call.
        Uses caching to avoid redundant parsing.
        
        Args:
            resume_text: Raw resume text
            use_cache: Whether to use cache (default: True)
            
        Returns:
            ParsedResume with structured data
        """
        logger.info("Resume Parser Agent: Starting parse", resume_length=len(resume_text))
        
        # Check cache first
        if use_cache:
            from ..utils.agent_cache import get_agent_cache
            cache = get_agent_cache()
            cached_data = cache.get_parsed_resume(resume_text)
            if cached_data:
                logger.info("Resume Parser Agent: Using cached parsed resume")
                parsed_data = cached_data
            else:
                # Extract everything in ONE structured LLM call
                parsed_data = self._extract_all_structured(resume_text)
                # Cache the result
                cache.set_parsed_resume(resume_text, parsed_data)
        else:
            # Extract everything in ONE structured LLM call
            parsed_data = self._extract_all_structured(resume_text)
        
        # Extract other sections (non-LLM, fast)
        certifications = self._extract_certifications(resume_text)
        projects = self._extract_projects(resume_text)
        summary = self._extract_summary(resume_text)
        sections = self._parse_sections(resume_text)
        
        # Handle case where LLM returns a list instead of dict
        if isinstance(parsed_data, list):
            logger.warning("Resume parser returned list instead of dict, extracting first element")
            parsed_data = parsed_data[0] if parsed_data else {}
        
        if not isinstance(parsed_data, dict):
            logger.error(f"Resume parser returned unexpected type: {type(parsed_data)}")
            parsed_data = {}
        
        # Extract from structured data with defensive type checking
        skills_data = parsed_data.get("skills", {})
        if isinstance(skills_data, list):
            skills_data = skills_data[0] if skills_data else {}
        if not isinstance(skills_data, dict):
            skills_data = {}
            
        experience_data = parsed_data.get("experience", {})
        if isinstance(experience_data, list):
            experience_data = experience_data[0] if experience_data else {}
        if not isinstance(experience_data, dict):
            experience_data = {}
            
        education_data = parsed_data.get("education", {})
        # Education might legitimately be a list of entries
        if isinstance(education_data, list):
            education_data = {"education": education_data}
        if not isinstance(education_data, dict):
            education_data = {}
        
        # Flatten all skills
        all_skills = []
        for category in [skills_data.get("programming_languages", []),
                        skills_data.get("frameworks", []),
                        skills_data.get("tools", []),
                        skills_data.get("databases", []),
                        skills_data.get("cloud_platforms", []),
                        skills_data.get("testing_tools", []),
                        skills_data.get("other_technologies", []),
                        skills_data.get("methodologies", [])]:
            all_skills.extend(category)
        
        # Add confirmed skills that might not be in resume
        for skill in self.confirmed_skills:
            if skill not in all_skills:
                all_skills.append(skill)
        
        # Convert education entries to dict format and clean None values
        education_dicts = []
        for edu in education_data.get("education", []):
            if isinstance(edu, dict):
                # Clean None values to empty strings for Pydantic compatibility
                cleaned_edu = {k: (v if v is not None else "") for k, v in edu.items()}
                education_dicts.append(cleaned_edu)
            elif hasattr(edu, 'model_dump'):
                # If it's a Pydantic model, convert to dict
                education_dicts.append(edu.model_dump())
        
        parsed = ParsedResume(
            programming_languages=skills_data.get("programming_languages", []),
            frameworks=skills_data.get("frameworks", []),
            tools=skills_data.get("tools", []),
            databases=skills_data.get("databases", []),
            cloud_platforms=skills_data.get("cloud_platforms", []),
            testing_tools=skills_data.get("testing_tools", []),
            other_technologies=skills_data.get("other_technologies", []),
            methodologies=skills_data.get("methodologies", []),
            all_skills=sorted(list(set(all_skills))),
            total_years_experience=experience_data.get("total_years"),
            years_mentioned=experience_data.get("years_mentioned", []),
            job_titles=experience_data.get("job_titles", []),
            companies=experience_data.get("companies", []),
            experience_summary=experience_data.get("summary", ""),
            education=education_dicts,
            certifications=certifications,
            projects=projects,
            summary=summary,
            raw_text=resume_text,
            sections=sections
        )
        
        logger.info(
            "Resume Parser Agent: Parse complete",
            skills_count=len(parsed.all_skills),
            experience_years=parsed.total_years_experience,
            job_titles_count=len(parsed.job_titles)
        )
        
        return parsed
    
    def _extract_all_structured(self, resume_text: str) -> Dict[str, Any]:
        """
        Extract ALL resume information in a SINGLE structured LLM call.
        This replaces 3+ separate LLM calls with one efficient call.
        
        First tries to use the skill_extractor if available for skills,
        then uses LLM for the rest.
        """
        from langchain_core.messages import SystemMessage, HumanMessage
        
        # Try to use skill extractor first (if it exists and is more efficient)
        # For now, we'll use LLM for everything, but this could be optimized
        # to use skill_extractor for skills and LLM for experience/education
        
        prompt = SystemMessage(content="""You are a RESUME PARSER. Extract ALL information from the resume in a SINGLE structured response.

CRITICAL RULES:
- Extract EXACTLY as written (preserve capitalization)
- Only extract what is EXPLICITLY mentioned - DO NOT infer or add anything
- For years of experience: only extract if explicitly stated (e.g., "5 years", "8 years of experience")
- DO NOT calculate or infer years from dates
- Extract job titles, companies, and education exactly as written

Respond with valid JSON only in this EXACT structure:
{
    "skills": {
        "programming_languages": ["Java", "Python", ...],
        "frameworks": ["Spring Boot", "React", ...],
        "tools": ["Docker", "Kubernetes", ...],
        "databases": ["Oracle", "MySQL", ...],
        "cloud_platforms": ["AWS", "Azure", ...],
        "testing_tools": ["Selenium", "JUnit", ...],
        "other_technologies": ["Kafka", "Apache NiFi", ...],
        "methodologies": ["Agile", "Scrum", ...]
    },
    "experience": {
        "total_years": <number or null if not explicitly stated>,
        "years_mentioned": ["6 years", "8 years of experience", ...],
        "job_titles": ["Software Engineer", ...],
        "companies": ["Company Name 1", ...],
        "summary": "<brief 2-3 sentence summary of experience>"
    },
    "education": [
        {
            "degree": "Master of Science",
            "field": "Data Science",
            "institution": "Maryville University",
            "dates": "September 2019 – April 2021"
        }
    ]
}""")
        
        human_prompt = HumanMessage(content=f"""Resume:
---
{resume_text}

Extract ALL information: skills, technologies, tools, experience, job titles, companies, and education. Return as structured JSON.""")
        
        # Note: retry logic handled by invoke_structured, not duplicated here
        def _extract_and_validate():
            # Use structured output method for better reliability
            parsed_dict = self.llm_service.invoke_structured([prompt, human_prompt], validation_retries=2)
            
            # Validate using Pydantic model (strict validation with automatic defaults)
            try:
                parsed_structured = ParsedResumeStructured.model_validate(parsed_dict)
                logger.info("Resume Parser Agent: Structured extraction successful and validated with Pydantic")
                
                # Convert to dict format for backward compatibility
                return {
                    "skills": parsed_structured.skills.model_dump(),
                    "experience": parsed_structured.experience.model_dump(),
                    "education": [edu.model_dump() for edu in parsed_structured.education]
                }
            except ValidationError as validation_error:
                logger.warning(f"Pydantic validation failed, attempting to fix: {validation_error}")
                # Try to fix common issues and re-validate
                if not isinstance(parsed_dict, dict):
                    raise ValueError("LLM response is not a dictionary")
                
                # Ensure basic structure exists
                parsed_dict.setdefault("skills", {})
                parsed_dict.setdefault("experience", {})
                parsed_dict.setdefault("education", [])
                
                # Re-validate with fixed structure
                parsed_structured = ParsedResumeStructured.model_validate(parsed_dict)
                return {
                    "skills": parsed_structured.skills.model_dump(),
                    "experience": parsed_structured.experience.model_dump(),
                    "education": [edu.model_dump() for edu in parsed_structured.education]
                }
        
        try:
            return _extract_and_validate()
            
        except Exception as e:
            logger.error(f"Structured extraction failed: {e}, falling back to individual extractions", exc_info=True)
            # Fallback to old method if structured fails
            skills_result = self._extract_skills(resume_text)
            if not isinstance(skills_result, dict):
                skills_result = {}
            
            experience_result = self._extract_experience(resume_text)
            if not isinstance(experience_result, dict):
                experience_result = {}
            
            education_result = self._extract_education(resume_text)
            if isinstance(education_result, list):
                education_list = education_result
            elif isinstance(education_result, dict):
                education_list = education_result.get("education", [])
            else:
                education_list = []
            
            return {
                "skills": skills_result,
                "experience": experience_result,
                "education": education_list
            }
    
    def _extract_skills(self, resume_text: str) -> Dict[str, List[str]]:
        """Extract skills using LLM with strict instructions"""
        from langchain_core.messages import SystemMessage, HumanMessage
        
        prompt = SystemMessage(content="""You are a RESUME PARSER. Your ONLY job is to extract skills, technologies, and tools that are EXPLICITLY mentioned in the resume.

CRITICAL RULES:
- Extract EXACTLY as written (preserve capitalization)
- Only extract what is EXPLICITLY mentioned - DO NOT infer or add anything
- Include all variations (e.g., "Java 8" and "Java" if both mentioned)
- Be comprehensive but accurate

Respond with valid JSON only:
{
    "programming_languages": ["Java", "Python", ...],
    "frameworks": ["Spring Boot", "React", ...],
    "tools": ["Docker", "Kubernetes", ...],
    "databases": ["Oracle", "MySQL", ...],
    "cloud_platforms": ["AWS", "Azure", ...],
    "testing_tools": ["Selenium", "JUnit", ...],
    "other_technologies": ["Kafka", "Apache NiFi", ...],
    "methodologies": ["Agile", "Scrum", ...]
}""")
        
        human_prompt = HumanMessage(content=f"""Resume:
---
{resume_text}

Extract ALL skills, technologies, tools, frameworks, programming languages, databases, cloud platforms, and methodologies that are EXPLICITLY mentioned in this resume. Return them categorized as JSON.""")
        
        try:
            response = self.llm_service.invoke_with_retry([prompt, human_prompt])
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                return json.loads(json_match.group(0))
        except Exception as e:
            logger.error(f"Skill extraction failed: {e}", exc_info=True)
        
        return {}
    
    def _extract_experience(self, resume_text: str) -> Dict[str, Any]:
        """Extract experience information"""
        from langchain_core.messages import SystemMessage, HumanMessage
        
        prompt = SystemMessage(content="""You are a RESUME PARSER. Extract experience-related information from the resume.

CRITICAL RULES:
- Only extract what is EXPLICITLY stated
- For years of experience: only extract if explicitly stated (e.g., "5 years", "8 years of experience")
- DO NOT calculate or infer years from dates
- Extract job titles and companies exactly as written

Respond with valid JSON only:
{
    "total_years": <number or null if not explicitly stated>,
    "years_mentioned": ["6 years", "8 years of experience", ...],
    "job_titles": ["Software Engineer", ...],
    "companies": ["Company Name 1", ...],
    "summary": "<brief 2-3 sentence summary of experience>"
}""")
        
        human_prompt = HumanMessage(content=f"""Resume:
---
{resume_text}

Extract experience information: years of experience (ONLY if explicitly stated), job titles, companies, and a brief summary.""")
        
        try:
            response = self.llm_service.invoke_with_retry([prompt, human_prompt])
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                return json.loads(json_match.group(0))
        except Exception as e:
            logger.error(f"Experience extraction failed: {e}", exc_info=True)
        
        return {"total_years": None, "years_mentioned": [], "job_titles": [], "companies": [], "summary": ""}
    
    def _extract_education(self, resume_text: str) -> Dict[str, List[Dict]]:
        """Extract education information"""
        from langchain_core.messages import SystemMessage, HumanMessage
        
        prompt = SystemMessage(content="""You are a RESUME PARSER. Extract education information from the resume.

CRITICAL RULES:
- Extract EXACTLY as written - DO NOT change degree names, fields, or institutions
- Preserve all details exactly

Respond with valid JSON only:
{
    "education": [
        {
            "degree": "Master of Science",
            "field": "Data Science",
            "institution": "Maryville University",
            "dates": "September 2019 – April 2021"
        }
    ]
}""")
        
        human_prompt = HumanMessage(content=f"""Resume:
---
{resume_text}

Extract education details exactly as written.""")
        
        try:
            response = self.llm_service.invoke_with_retry([prompt, human_prompt])
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                return json.loads(json_match.group(0))
        except Exception as e:
            logger.error(f"Education extraction failed: {e}", exc_info=True)
        
        return {"education": []}
    
    def _extract_certifications(self, resume_text: str) -> List[str]:
        """Extract certifications"""
        # Simple extraction - can be enhanced with LLM if needed
        certs = []
        cert_keywords = ["certified", "certification", "certificate"]
        lines = resume_text.split('\n')
        for line in lines:
            if any(keyword in line.lower() for keyword in cert_keywords):
                certs.append(line.strip())
        return certs[:10]  # Limit to 10
    
    def _extract_projects(self, resume_text: str) -> List[Dict[str, str]]:
        """Extract project information"""
        # Can be enhanced with LLM parsing
        return []
    
    def _extract_summary(self, resume_text: str) -> Optional[str]:
        """Extract professional summary if present"""
        # Look for summary/objective section
        summary_keywords = ["summary", "objective", "profile", "about"]
        lines = resume_text.split('\n')
        in_summary = False
        summary_lines = []
        
        for line in lines[:20]:  # Check first 20 lines
            if any(keyword in line.lower() for keyword in summary_keywords):
                in_summary = True
                continue
            if in_summary and line.strip():
                if line.strip().isupper() or line.strip().startswith('#'):
                    break  # Hit next section
                summary_lines.append(line.strip())
            elif in_summary and not line.strip():
                if summary_lines:
                    break
        
        return ' '.join(summary_lines) if summary_lines else None
    
    def _parse_sections(self, resume_text: str) -> Dict[str, str]:
        """Parse resume into sections"""
        sections = {}
        lines = resume_text.split('\n')
        current_section = None
        current_content = []
        
        for line in lines:
            # Check if line is a section header (all caps, short, or markdown heading)
            stripped = line.strip()
            if stripped.isupper() and len(stripped) < 50:
                # Save previous section
                if current_section:
                    sections[current_section] = '\n'.join(current_content).strip()
                # Start new section
                current_section = stripped
                current_content = []
            elif stripped.startswith('#'):
                # Markdown heading
                if current_section:
                    sections[current_section] = '\n'.join(current_content).strip()
                current_section = stripped.lstrip('#').strip()
                current_content = []
            else:
                if current_section:
                    current_content.append(line)
        
        # Save last section
        if current_section:
            sections[current_section] = '\n'.join(current_content).strip()
        
        return sections
