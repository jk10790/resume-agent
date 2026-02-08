from langchain_core.messages import SystemMessage, HumanMessage
from typing import Optional
from ..storage.memory import load_memory
from ..storage.user_memory import get_skills
from ..prompts.templates import get_prompt
from ..services.llm_service import LLMService
from ..config import settings
from ..utils.logger import logger

# Use prompt versioning (can be configured via env var)
PROMPT_VERSION = getattr(settings, 'resume_tailoring_prompt_version', 'latest')

def tailor_resume(llm_service, resume_text, jd_text, prompt_version: Optional[str] = None, intensity: str = "medium", refinement_feedback: Optional[str] = None):
    """
    Tailor resume for a job using LLM service.
    
    Args:
        llm_service: LLMService instance
        resume_text: Original resume content
        jd_text: Job description content
        prompt_version: Prompt version to use
    
    Returns:
        Tailored resume text
    """
    memory = load_memory()
    clarification_lines = [f"- {k.replace('_', ' ').capitalize()}: {v}" for k, v in memory.items() if v and isinstance(v, str) and v.strip()]
    clarifications = "\n".join(clarification_lines) if clarification_lines else "None"
    
    # Add user's confirmed skills to clarifications
    user_skills = get_skills()
    if user_skills:
        skills_list = ", ".join(user_skills)
        clarifications = f"{clarifications}\n\n✅ USER CONFIRMED SKILLS (you can add these even if not in original resume):\n{skills_list}\n\n⚠️ CRITICAL: ONLY use skills from this list or skills already present in the original resume. DO NOT add skills that are not in this list and not in the original resume."
    
    # Add refinement feedback if provided - make it very prominent
    if refinement_feedback:
        clarifications = f"{clarifications}\n\n⚠️⚠️⚠️ USER FEEDBACK FOR REFINEMENT - FOLLOW THESE INSTRUCTIONS CAREFULLY ⚠️⚠️⚠️\n{refinement_feedback}\n\nPlease apply these changes to the resume. This feedback takes priority over general instructions."

    # Select prompt based on intensity or version
    if intensity in ["light", "medium", "heavy"]:
        from ..prompts.tailoring_intensity import (
            RESUME_TAILORING_LIGHT,
            RESUME_TAILORING_MEDIUM,
            RESUME_TAILORING_HEAVY
        )
        intensity_map = {
            "light": RESUME_TAILORING_LIGHT,
            "medium": RESUME_TAILORING_MEDIUM,
            "heavy": RESUME_TAILORING_HEAVY
        }
        prompt_template = intensity_map[intensity]
        logger.info("Using intensity-based prompt", intensity=intensity)
    else:
        # Use versioned prompt template
        if prompt_version is None:
            prompt_version = PROMPT_VERSION
        prompt_template = get_prompt("resume_tailoring", prompt_version)
        logger.info("Using versioned prompt", prompt_version=prompt_version)
    
    logger.info("Preparing resume tailoring prompt", resume_length=len(resume_text), jd_length=len(jd_text), intensity=intensity)
    messages = prompt_template.format_messages(
        job_description=jd_text,
        resume=resume_text,
        clarifications=clarifications
    )

    # Use LLMService if available, otherwise fallback to direct invoke
    if isinstance(llm_service, LLMService):
        logger.info("Calling LLM service to tailor resume")
        result = llm_service.invoke_with_retry(messages).strip()
        logger.info("Resume tailoring completed", result_length=len(result))
    else:
        # Legacy support for direct model instances
        logger.info("Calling LLM (legacy mode) to tailor resume")
        result = llm_service.invoke(messages).strip()
        logger.info("Resume tailoring completed (legacy)", result_length=len(result))
    
    # Post-process to remove any job description content that might have leaked in
    result = _clean_resume_output(result, jd_text)
    logger.info("Resume cleaned and validated", final_length=len(result))
    return result


def get_llm_acknowledgment(llm_service, action: str, context: Optional[str] = None) -> str:
    """
    Get a brief acknowledgment message from the LLM after completing an action.
    
    Args:
        llm_service: LLMService instance
        action: The action that was completed (e.g., "tailored", "refined", "updated")
        context: Optional context about what was done (e.g., feedback provided)
    
    Returns:
        Brief acknowledgment message from the LLM
    """
    try:
        if action == "tailored":
            prompt = SystemMessage(content="You are a helpful assistant. Provide a brief, friendly acknowledgment (1-2 sentences) confirming that the resume has been tailored successfully.")
            message = HumanMessage(content="The resume has been tailored for the job. Please provide a brief acknowledgment.")
        elif action == "refined":
            prompt = SystemMessage(content="You are a helpful assistant. Provide a brief, friendly acknowledgment (1-2 sentences) confirming that the feedback has been understood and applied to the resume.")
            message = HumanMessage(content=f"User provided feedback: {context or 'Feedback received'}. The resume has been updated. Please acknowledge that you've understood and applied the changes.")
        else:
            prompt = SystemMessage(content="You are a helpful assistant. Provide a brief, friendly acknowledgment (1-2 sentences).")
            message = HumanMessage(content=f"Action completed: {action}. {context or ''}")
        
        acknowledgment = llm_service.invoke_with_retry([prompt, message]).strip()
        # Clean up any extra formatting
        acknowledgment = acknowledgment.replace("**", "").strip()
        if len(acknowledgment) > 200:
            acknowledgment = acknowledgment[:200] + "..."
        return acknowledgment
    except Exception as e:
        logger.warning(f"Failed to get LLM acknowledgment: {e}")
        # Fallback to simple message
        if action == "tailored":
            return "Resume tailored successfully. Ready for review."
        elif action == "refined":
            return "Feedback received and applied. Resume updated."
        else:
            return "Done."


def _clean_resume_output(resume_text: str, jd_text: str) -> str:
    """
    Clean the LLM output to ensure it only contains resume content.
    Removes any job description text that might have been included.
    """
    import re
    
    # Remove common prefixes/suffixes that LLMs sometimes add
    prefixes_to_remove = [
        r"^Here is the revised resume:?\s*",
        r"^Revised Resume:?\s*",
        r"^Tailored Resume:?\s*",
        r"^Based on the job description.*?:\s*",
        r"^Job Description:.*?\n",
    ]
    
    cleaned = resume_text
    for pattern in prefixes_to_remove:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE | re.MULTILINE | re.DOTALL)
    
    # Remove any section that looks like it contains the full job description
    # Look for patterns like "Job Description:" followed by content
    jd_patterns = [
        r"(?i)job\s+description:.*?(?=\n\n|\n#|$)",
        r"(?i)---\s*Job\s+Description.*?(?=\n\n|\n#|$)",
    ]
    
    for pattern in jd_patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.DOTALL)
    
    # If the output contains a large chunk of the JD text verbatim, remove it
    # Check if more than 200 characters of JD appear consecutively in the output
    if len(jd_text) > 100:  # Only check if JD is substantial
        jd_snippets = []
        # Extract 200-char snippets from JD to check against
        for i in range(0, len(jd_text) - 200, 100):
            snippet = jd_text[i:i+200].strip()
            if len(snippet) > 150:  # Only check substantial snippets
                jd_snippets.append(re.escape(snippet))
        
        # Remove any section containing substantial JD text
        for snippet_pattern in jd_snippets[:5]:  # Check first 5 snippets to avoid performance issues
            if re.search(snippet_pattern, cleaned, re.IGNORECASE):
                logger.warning("Detected job description content in resume output, removing")
                # Find and remove the paragraph/section containing this
                # This is a simple approach - could be improved
                cleaned = re.sub(
                    f".*?{snippet_pattern}.*?(?=\n\n|\n#|$)",
                    "",
                    cleaned,
                    flags=re.DOTALL | re.IGNORECASE
                )
    
    # Remove any trailing explanatory text
    # Look for patterns like "Note:" or "Remember:" at the end
    cleaned = re.sub(
        r"\n\n(Note|Remember|Important):.*$",
        "",
        cleaned,
        flags=re.IGNORECASE | re.DOTALL
    )
    
    # NOTE: We no longer strip bold formatting here because:
    # 1. User refinement feedback may request specific formatting (e.g., bold job titles)
    # 2. The LLM should follow user instructions dynamically
    # 3. The prompt already instructs appropriate bold usage (job titles only)
    # If the LLM adds unwanted bold, it's better to fix the prompt than strip everything
    logger.info("Preserving formatting in resume output (including user-requested bold)")
    
    return cleaned.strip()


# ✅ Public entry point
def tailor_resume_for_job(
    resume_text: str, 
    jd_text: str, 
    llm_service: Optional[LLMService] = None,
    intensity: str = "medium",
    refinement_feedback: Optional[str] = None
) -> str:
    """
    Tailor resume for a job.
    
    Args:
        resume_text: Original resume content
        jd_text: Job description content
        llm_service: Optional LLMService instance (creates one if not provided)
        intensity: Tailoring intensity ("light", "medium", "heavy")
        refinement_feedback: Optional feedback for iterative refinement
    
    Returns:
        Tailored resume text
    """
    if llm_service is None:
        llm_service = LLMService()  # Uses provider from settings
    return tailor_resume(llm_service, resume_text, jd_text, intensity=intensity, refinement_feedback=refinement_feedback)
