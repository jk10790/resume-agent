"""
Resume Humanizer Agent
Single responsibility: make tailored resume read more natural and human-written.
"""

from ..services.llm_service import LLMService
from ..utils.logger import logger


class ResumeHumanizerAgent:
    """Agent responsible ONLY for humanizing the tailored resume text."""

    def __init__(self, llm_service: LLMService):
        # The shared service, not a private one. A separate instance built from
        # per-agent overrides kept its own usage totals, so this rewrite -- a
        # whole resume on the expensive tier -- reported as costing nothing.
        # Model choice belongs to the task's declared tier now.
        self.llm_service = llm_service

    def humanize(self, original_resume: str, tailored_resume: str) -> str:
        """Humanize the tailored resume while preserving facts."""
        try:
            result = self.llm_service.run_task(
                "resume.humanize",
                original_resume=original_resume,
                tailored_resume=tailored_resume,
            ).strip()

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
