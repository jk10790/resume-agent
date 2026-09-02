"""
Skill Extractor
Extracts skills, technologies, and tools from a resume using LLM.
"""

from typing import Any, Dict, List

from ..services.llm_service import LLMService
from ..utils.logger import logger

_EMPTY_SKILLS: Dict[str, Any] = {"categorized": {}, "all_skills": [], "total_count": 0}
_EMPTY_EXPERIENCE: Dict[str, Any] = {
    "total_years": None,
    "years_mentioned": [],
    "job_titles": [],
    "companies": [],
    "education": [],
}


def extract_skills_from_resume(llm_service: LLMService, resume_text: str) -> Dict[str, Any]:
    """Extract skills, technologies, and tools from a resume.

    Returns the categorised skills as the model reported them, plus a flattened,
    de-duplicated list and its size.
    """
    try:
        categorized = llm_service.run_task("skills.extract", resume_text=resume_text)
        if not isinstance(categorized, dict):
            logger.warning("Skill extraction did not return an object")
            return dict(_EMPTY_SKILLS)

        all_skills = {
            skill
            for skills in categorized.values()
            if isinstance(skills, list)
            for skill in skills
        }
        logger.info(f"Extracted {len(all_skills)} unique skills from resume")
        return {
            "categorized": categorized,
            "all_skills": sorted(all_skills),
            "total_count": len(all_skills),
        }
    except Exception as e:
        logger.error(f"Skill extraction failed: {e}", exc_info=True)
        return dict(_EMPTY_SKILLS)


def extract_experience_info(llm_service: LLMService, resume_text: str) -> Dict[str, Any]:
    """Extract stated years, job titles, companies and education from a resume."""
    try:
        info = llm_service.run_task("skills.experience", resume_text=resume_text)
        if not isinstance(info, dict):
            logger.warning("Experience extraction did not return an object")
            return dict(_EMPTY_EXPERIENCE)
        return info
    except Exception as e:
        logger.error(f"Experience extraction failed: {e}", exc_info=True)
        return dict(_EMPTY_EXPERIENCE)
