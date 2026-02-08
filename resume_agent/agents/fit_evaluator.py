# fit_evaluator.py
"""
Legacy facade: evaluates resume fit from raw resume and JD text.
Delegates to ResumeParserAgent, JDAnalyzerAgent, and FitEvaluatorAgent.
"""

from ..storage.user_memory import get_skills
from ..models.resume import FitEvaluation
from ..services.llm_service import LLMService
from ..utils.logger import logger
from ..config import settings

PROMPT_VERSION = getattr(settings, "fit_evaluation_prompt_version", "latest")


def evaluate_resume_fit(
    model,
    resume_text: str,
    jd_text: str,
    prompt_version: str = PROMPT_VERSION,
) -> FitEvaluation:
    """
    Evaluate resume fit against job description (legacy entrypoint).
    Uses FitEvaluatorAgent with parsed resume and analyzed JD.
    """
    llm_service = model if isinstance(model, LLMService) else LLMService()
    try:
        from .resume_parser_agent import ResumeParserAgent
        from .jd_analyzer_agent import JDAnalyzerAgent
        from .fit_evaluator_agent import FitEvaluatorAgent

        parser = ResumeParserAgent(llm_service)
        jd_analyzer = JDAnalyzerAgent(llm_service)
        fit_agent = FitEvaluatorAgent(llm_service)
        parsed = parser.parse(resume_text, use_cache=False)
        analyzed = jd_analyzer.analyze(jd_text, use_cache=False)
        evaluation = fit_agent.evaluate_fit(parsed, analyzed)
        logger.info(
            "Fit evaluation completed (agent)",
            score=evaluation.score,
            should_apply=evaluation.should_apply,
        )
        return evaluation
    except Exception as e:
        logger.warning("Agent-based fit evaluation failed, using LLM fallback", error=str(e))
        return _evaluate_fit_structured_fallback(llm_service, resume_text, jd_text, prompt_version)


def _evaluate_fit_structured_fallback(
    llm_service: LLMService,
    resume_text: str,
    jd_text: str,
    prompt_version: str,
) -> FitEvaluation:
    """Fallback using LLMService structured evaluation."""
    known_skills = get_skills()
    return llm_service.evaluate_fit_structured(
        resume_text=resume_text,
        jd_text=jd_text,
        known_skills=known_skills,
        prompt_version=prompt_version,
    )
