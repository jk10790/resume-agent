"""
Resume Tailor Agent
Strictly responsible for updating/tailoring the resume with all available information.
This agent ONLY tailors - it does NOT parse, analyze, or validate.
"""

from typing import Optional, Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..models.agent_models import ParsedResume, AnalyzedJD, ATSScore
    from ..models.resume import FitEvaluation
from ..services.llm_service import LLMService
from ..utils.logger import logger
from ..storage.user_memory import get_skills
from ..storage.memory import load_memory
from ..prompts.templates import get_prompt
from ..config import settings
from ..utils.llm_factory import create_llm_service_with_fallback


class ResumeTailorAgent:
    """
    Agent responsible ONLY for tailoring/updating the resume.
    This agent receives all parsed and analyzed information and updates the resume accordingly.
    """
    
    def __init__(self, llm_service: LLMService):
        self.llm_service = llm_service
        self.confirmed_skills = get_skills()
        self.critic_llm = create_llm_service_with_fallback(
            fallback=llm_service,
            provider=settings.tailoring_critic_provider,
            model=settings.tailoring_critic_model,
            temperature=settings.tailoring_critic_temperature,
            top_p=settings.tailoring_critic_top_p,
            max_tokens=settings.tailoring_critic_max_tokens,
            tag="tailor_critic"
        )
        self.revision_llm = create_llm_service_with_fallback(
            fallback=llm_service,
            provider=settings.tailoring_revision_provider,
            model=settings.tailoring_revision_model,
            temperature=settings.tailoring_revision_temperature,
            top_p=settings.tailoring_revision_top_p,
            max_tokens=settings.tailoring_revision_max_tokens,
            tag="tailor_revision"
        )
    
    def tailor(
        self,
        original_resume_text: str,
        parsed_resume: "ParsedResume",
        analyzed_jd: "AnalyzedJD",
        fit_evaluation: "FitEvaluation",
        ats_score: Optional["ATSScore"] = None,
        intensity: str = "medium",
        refinement_feedback: Optional[str] = None
    ) -> str:
        """
        Tailor resume based on all available information.
        
        Args:
            original_resume_text: Original resume text
            parsed_resume: ParsedResume from ResumeParserAgent
            analyzed_jd: AnalyzedJD from JDAnalyzerAgent
            fit_evaluation: FitEvaluation from FitEvaluatorAgent
            ats_score: Optional ATSScore from ATSScorerAgent
            intensity: Tailoring intensity ("light", "medium", "heavy")
            refinement_feedback: Optional feedback for refinement
            
        Returns:
            Tailored resume text
        """
        logger.info("Resume Tailor Agent: Starting tailoring", intensity=intensity)
        
        # Build comprehensive context for tailoring
        context = self._build_tailoring_context(
            parsed_resume,
            analyzed_jd,
            fit_evaluation,
            ats_score
        )
        
        # Get user memory and clarifications
        memory = load_memory()
        clarification_lines = [f"- {k.replace('_', ' ').capitalize()}: {v}" for k, v in memory.items() if v and isinstance(v, str) and v.strip()]
        clarifications = "\n".join(clarification_lines) if clarification_lines else "None"
        
        # Add confirmed skills
        if self.confirmed_skills:
            skills_list = ", ".join(self.confirmed_skills)
            clarifications = f"{clarifications}\n\n✅ USER CONFIRMED SKILLS (you can add these even if not in original resume):\n{skills_list}\n\n⚠️ CRITICAL: ONLY use skills from this list or skills already present in the original resume. DO NOT add skills that are not in this list and not in the original resume."
        
        # Add refinement feedback if provided
        if refinement_feedback:
            clarifications = f"{clarifications}\n\n⚠️⚠️⚠️ USER FEEDBACK FOR REFINEMENT - FOLLOW THESE INSTRUCTIONS CAREFULLY ⚠️⚠️⚠️\n{refinement_feedback}\n\nPlease apply these changes to the resume. This feedback takes priority over general instructions."
        
        # Select prompt based on intensity
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
        else:
            # Use versioned prompt template
            PROMPT_VERSION = getattr(settings, 'resume_tailoring_prompt_version', 'latest')
            prompt_template = get_prompt("resume_tailoring", PROMPT_VERSION)
        
        # Format messages with comprehensive context
        messages = prompt_template.format_messages(
            job_description=analyzed_jd.raw_text,
            resume=original_resume_text,
            clarifications=clarifications
        )
        
        # Add context as additional system message
        from langchain_core.messages import SystemMessage
        context_message = SystemMessage(content=f"""ADDITIONAL CONTEXT FOR TAILORING:

{context}

Use this context to make informed tailoring decisions.""")
        messages.insert(1, context_message)  # Insert after system prompt
        
        # Invoke LLM for initial draft
        logger.info("Calling LLM service to tailor resume")
        draft = self.llm_service.invoke_with_retry(messages).strip()

        # Optional critique/revise loop for realism
        if settings.tailoring_enable_critique:
            critique = self._critique_tailoring(
                original_resume_text,
                draft,
                analyzed_jd.raw_text,
                clarifications
            )
            if critique:
                revised = self._revise_with_critique(
                    original_resume_text,
                    draft,
                    critique,
                    analyzed_jd.raw_text,
                    clarifications
                )
                if revised:
                    draft = revised

        # Clean output
        result = self._clean_resume_output(draft, analyzed_jd.raw_text)

        logger.info("Resume Tailor Agent: Tailoring complete", result_length=len(result))
        return result
    
    def _build_tailoring_context(
        self,
        parsed_resume: "ParsedResume",
        analyzed_jd: "AnalyzedJD",
        fit_evaluation: "FitEvaluation",
        ats_score: Optional["ATSScore"]
    ) -> str:
        """Build concise context for tailoring (optimized to avoid token limits)"""
        context_parts = []
        
        # Resume analysis (truncated)
        skills_summary = ', '.join(parsed_resume.all_skills[:15])
        if len(parsed_resume.all_skills) > 15:
            skills_summary += f" (+{len(parsed_resume.all_skills) - 15} more)"
        
        context_parts.append("RESUME ANALYSIS:")
        context_parts.append(f"- Skills: {skills_summary}")
        context_parts.append(f"- Experience: {parsed_resume.total_years_experience or 'Not stated'} years")
        context_parts.append(f"- Job Titles: {', '.join(parsed_resume.job_titles[:3])}")
        
        # JD requirements (truncated)
        required_skills_summary = ', '.join(analyzed_jd.required_skills[:15])
        if len(analyzed_jd.required_skills) > 15:
            required_skills_summary += f" (+{len(analyzed_jd.required_skills) - 15} more)"
        
        context_parts.append("\nJOB REQUIREMENTS:")
        context_parts.append(f"- Required Skills: {required_skills_summary}")
        context_parts.append(f"- Preferred Skills: {', '.join(analyzed_jd.preferred_skills[:10])}")
        context_parts.append(f"- Required Experience: {analyzed_jd.required_experience_years or 'Not specified'} years")
        context_parts.append(f"- Technologies: {', '.join(analyzed_jd.technologies_needed[:10])}")
        
        # Fit analysis (concise)
        context_parts.append("\nFIT ANALYSIS:")
        context_parts.append(f"- Fit Score: {fit_evaluation.score}/10 ({'✅ Good fit' if fit_evaluation.should_apply else '⚠️ Low fit'})")
        context_parts.append(f"- Matching: {', '.join(fit_evaluation.matching_areas[:3])}")
        context_parts.append(f"- Missing: {', '.join(fit_evaluation.missing_areas[:3])}")
        
        # ATS score if available (concise)
        if ats_score:
            context_parts.append(f"\nATS SCORE: {ats_score.score}/100")
            if ats_score.missing_keywords:
                missing_summary = ', '.join(ats_score.missing_keywords[:5])
                if len(ats_score.missing_keywords) > 5:
                    missing_summary += f" (+{len(ats_score.missing_keywords) - 5} more)"
                context_parts.append(f"- Missing Keywords: {missing_summary}")
        
        return "\n".join(context_parts)
    
    def _clean_resume_output(self, result: str, jd_text: str) -> str:
        """Clean resume output to remove any job description content"""
        # Remove any obvious JD content that might have leaked in
        jd_sentences = jd_text.split('.')[:5]  # First few sentences
        for sentence in jd_sentences:
            if len(sentence.strip()) > 20:  # Only check substantial sentences
                if sentence.strip() in result:
                    result = result.replace(sentence.strip(), '')
        
        # Remove any markdown code blocks if present
        if result.startswith('```'):
            lines = result.split('\n')
            if lines[0].startswith('```'):
                result = '\n'.join(lines[1:])
            if result.endswith('```'):
                result = result[:-3]
        
        return result.strip()

    def _critique_tailoring(
        self,
        original_resume: str,
        tailored_resume: str,
        jd_text: str,
        clarifications: str
    ) -> str:
        """Critique tailored resume for realism and human tone."""
        from langchain_core.messages import SystemMessage, HumanMessage

        prompt = SystemMessage(content="""You are a RESUME CRITIC.

Focus ONLY on:
1. Unverified numeric claims or fabricated metrics
2. Phrases that sound AI-generated or overly templated
3. Repetition or unnatural cadence in bullet points

Rules:
- Do NOT propose new skills or new facts
- Provide concise, actionable revision notes
- Output a short bullet list only
""")

        human_prompt = HumanMessage(content=f"""Original Resume (reference for facts):
---
{original_resume[:2500]}
---

Tailored Draft:
---
{tailored_resume[:3500]}
---

Supplemental Clarifications:
{clarifications}

Return critique notes as bullet points only.""")

        try:
            critique = self.critic_llm.invoke_with_retry([prompt, human_prompt]).strip()
            # Clean code fences
            if critique.startswith("```"):
                lines = critique.split("\n")
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                critique = "\n".join(lines).strip()
            return critique
        except Exception as e:
            logger.warning(f"Critique failed: {e}")
            return ""

    def _revise_with_critique(
        self,
        original_resume: str,
        tailored_resume: str,
        critique_notes: str,
        jd_text: str,
        clarifications: str
    ) -> str:
        """Apply critique notes to revise the tailored resume."""
        from langchain_core.messages import SystemMessage, HumanMessage

        prompt = SystemMessage(content="""You are a RESUME REVISER.

Apply the critique notes to improve realism and human tone.

STRICT RULES:
1. Preserve ALL factual content (companies, titles, dates, skills, tools)
2. DO NOT add new metrics or numbers
3. If a metric is unverified, soften it to qualitative language
4. Keep the resume structure and formatting intact
5. Return ONLY the revised resume text
""")

        human_prompt = HumanMessage(content=f"""Original Resume (fact reference):
---
{original_resume[:2500]}
---

Tailored Draft:
---
{tailored_resume}
---

Critique Notes:
{critique_notes}

Supplemental Clarifications:
{clarifications}

Return the revised resume only.""")

        try:
            revised = self.revision_llm.invoke_with_retry([prompt, human_prompt]).strip()

            if revised.startswith("```"):
                lines = revised.split("\n")
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                revised = "\n".join(lines).strip()

            if len(revised) < len(tailored_resume) * 0.7:
                logger.warning(
                    "Revised resume too short; using draft",
                    draft_len=len(tailored_resume),
                    revised_len=len(revised)
                )
                return ""

            return revised
        except Exception as e:
            logger.warning(f"Revision failed: {e}")
            return ""
