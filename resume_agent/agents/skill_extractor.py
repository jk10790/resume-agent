"""
Skill Extractor
Extracts skills, technologies, and tools from a resume using LLM.
"""

from typing import List, Dict, Set
from ..services.llm_service import LLMService
from ..utils.logger import logger
import json
import re


def extract_skills_from_resume(llm_service: LLMService, resume_text: str) -> Dict[str, List[str]]:
    """
    Extract skills, technologies, and tools from a resume.
    
    Args:
        llm_service: LLMService instance
        resume_text: Resume content
        
    Returns:
        Dictionary with categorized skills:
        {
            "programming_languages": ["Java", "Python", ...],
            "frameworks": ["Spring Boot", "React", ...],
            "tools": ["Docker", "Kubernetes", ...],
            "databases": ["Oracle", "MySQL", ...],
            "cloud_platforms": ["AWS", "Azure", ...],
            "other_skills": ["Agile", "Scrum", ...]
        }
    """
    from langchain_core.messages import SystemMessage, HumanMessage
    
    prompt = SystemMessage(content="""You are a resume parser. Extract ALL skills, technologies, tools, frameworks, programming languages, databases, and platforms mentioned in the resume.

Be thorough - extract everything technical mentioned, even if it's just mentioned once.

Respond with valid JSON only:
{
    "programming_languages": ["Java", "Python", ...],
    "frameworks": ["Spring Boot", "React", "Cucumber", ...],
    "tools": ["Docker", "Kubernetes", "Jenkins", "Maven", ...],
    "databases": ["Oracle", "MySQL", "MongoDB", ...],
    "cloud_platforms": ["AWS", "Azure", "GCP", "PCF", ...],
    "testing_tools": ["Selenium", "JUnit", "TestNG", "JMeter", ...],
    "other_technologies": ["Kafka", "Apache NiFi", ...],
    "methodologies": ["Agile", "Scrum", "TDD", "BDD", ...]
}

IMPORTANT:
- Extract EXACTLY as written in the resume (preserve capitalization)
- Include all variations (e.g., "Java 8" and "Java" if both mentioned)
- Be comprehensive - don't miss anything
- Only extract what's explicitly mentioned in the resume""")
    
    human_prompt = HumanMessage(content=f"""Resume:
---
{resume_text}

Extract all skills, technologies, tools, frameworks, programming languages, databases, cloud platforms, and methodologies mentioned in this resume. Return them categorized as JSON.""")
    
    try:
        response = llm_service.invoke_with_retry([prompt, human_prompt])
        
        # Parse JSON response
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            skills_data = json.loads(json_match.group(0))
            
            # Flatten and deduplicate all skills
            all_skills = set()
            for category, skills in skills_data.items():
                if isinstance(skills, list):
                    all_skills.update(skills)
            
            logger.info(f"Extracted {len(all_skills)} unique skills from resume")
            return {
                "categorized": skills_data,
                "all_skills": sorted(list(all_skills)),
                "total_count": len(all_skills)
            }
        else:
            logger.warning("Could not parse skill extraction response")
            return {"categorized": {}, "all_skills": [], "total_count": 0}
            
    except Exception as e:
        logger.error(f"Skill extraction failed: {e}", exc_info=True)
        return {"categorized": {}, "all_skills": [], "total_count": 0}


def extract_experience_info(llm_service: LLMService, resume_text: str) -> Dict[str, any]:
    """
    Extract experience-related information from resume.
    
    Returns:
        Dictionary with experience details:
        {
            "total_years": <number or None>,
            "years_mentioned": <list of year strings found>,
            "job_titles": ["Software Engineer", ...],
            "companies": ["Company Name", ...],
            "education": [{"degree": "...", "field": "...", "institution": "..."}, ...]
        }
    """
    from langchain_core.messages import SystemMessage, HumanMessage
    
    prompt = SystemMessage(content="""You are a resume parser. Extract experience-related information from the resume.

Respond with valid JSON only:
{
    "total_years": <number or null if not explicitly stated>,
    "years_mentioned": ["6 years", "8 years of experience", ...],
    "job_titles": ["Software Engineer", "Senior Software Engineer", ...],
    "companies": ["Company Name 1", "Company Name 2", ...],
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

Extract experience information: years of experience (if explicitly stated), job titles, companies, and education details.""")
    
    try:
        response = llm_service.invoke_with_retry([prompt, human_prompt])
        
        # Parse JSON response
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            return json.loads(json_match.group(0))
        else:
            logger.warning("Could not parse experience extraction response")
            return {"total_years": None, "years_mentioned": [], "job_titles": [], "companies": [], "education": []}
            
    except Exception as e:
        logger.error(f"Experience extraction failed: {e}", exc_info=True)
        return {"total_years": None, "years_mentioned": [], "job_titles": [], "companies": [], "education": []}
