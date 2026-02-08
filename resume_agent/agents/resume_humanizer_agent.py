"""
Resume Humanizer Agent
Single responsibility: make tailored resume read more natural and human-written.
"""

from typing import Optional
from ..services.llm_service import LLMService
from ..utils.logger import logger
from ..utils.llm_factory import create_llm_service_with_fallback
from ..config import settings


class ResumeHumanizerAgent:
    """Agent responsible ONLY for humanizing the tailored resume text."""

    def __init__(self, llm_service: LLMService):
        self.base_llm = llm_service
        self.llm_service = create_llm_service_with_fallback(
            fallback=llm_service,
            provider=settings.humanizer_provider,
            model=settings.humanizer_model,
            temperature=settings.humanizer_temperature,
            top_p=settings.humanizer_top_p,
            max_tokens=settings.humanizer_max_tokens,
            tag="humanizer"
        )

    def humanize(self, original_resume: str, tailored_resume: str) -> str:
        """Humanize the tailored resume while preserving facts."""
        from langchain_core.messages import SystemMessage, HumanMessage

        prompt = SystemMessage(content="""You are a RESUME HUMANIZER.

GOAL:
- Make the resume sound natural and human-written
- Reduce templated/robotic phrasing (e.g., repeated "resulting in X%")
- Vary sentence structure and action verb cadence

STRICT RULES:
1. Preserve ALL factual content (companies, titles, dates, tools, metrics)
2. DO NOT add new metrics, numbers, skills, or responsibilities
3. DO NOT remove valid facts; only adjust wording for naturalness
4. Preserve structure, section order, and bullet formatting
5. Keep job titles and headers as-is

Return ONLY the revised resume text.""")

        human_prompt = HumanMessage(content=f"""Original Resume (for factual reference):
---
{original_resume[:3000]}
---

Tailored Resume to humanize:
---
{tailored_resume}
---

Return the humanized resume text only.""")

        try:
            result = self.llm_service.invoke_with_retry([prompt, human_prompt]).strip()

            # Clean code fences if present
            if result.startswith("```"):
                lines = result.split("\n")
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                result = "\n".join(lines).strip()

            # Safety: if output is too short, fall back to original tailored resume
            if len(result) < len(tailored_resume) * 0.7:
                logger.warning(
                    "Humanized resume too short; falling back",
                    original_len=len(tailored_resume),
                    humanized_len=len(result)
                )
                return tailored_resume

            return result
        except Exception as e:
            logger.warning(f"Humanizer failed: {e}")
            return tailored_resume
