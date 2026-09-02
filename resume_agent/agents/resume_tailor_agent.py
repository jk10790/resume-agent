"""
Resume Tailor Agent
Strictly responsible for updating/tailoring the resume with all available information.
This agent ONLY tailors - it does NOT parse, analyze, or validate.
"""

from difflib import SequenceMatcher
from typing import Optional, Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..models.agent_models import ParsedResume, AnalyzedJD, ATSScore
    from ..models.resume import FitEvaluation
from ..services.llm_service import LLMService
from ..utils.logger import logger
from ..storage.memory import load_memory
from ..config import settings
from ..utils.resume_document import parse_resume_document
from ..utils.resume_parser import parse_resume_sections


class ResumeTailorAgent:
    """
    Agent responsible ONLY for tailoring/updating the resume.
    This agent receives all parsed and analyzed information and updates the resume accordingly.
    """
    
    def __init__(self, llm_service: LLMService, confirmed_skills: Optional[list[str]] = None):
        self.llm_service = llm_service
        self.confirmed_skills = list(confirmed_skills or [])
    
    def tailor(
        self,
        original_resume_text: str,
        parsed_resume: "ParsedResume",
        analyzed_jd: "AnalyzedJD",
        fit_evaluation: "FitEvaluation",
        ats_score: Optional["ATSScore"] = None,
        strategy_brief=None,
        intensity: str = "medium",
        refinement_feedback: Optional[str] = None,
        current_draft_text: Optional[str] = None,
        preserve_sections: Optional[list[str]] = None,
        protected_entry_texts: Optional[list[str]] = None,
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
            preserve_sections: Sections that must remain unchanged
            
        Returns:
            Tailored resume text
        """
        logger.info("Resume Tailor Agent: Starting tailoring", intensity=intensity)
        
        # Build comprehensive context for tailoring
        context = self._build_tailoring_context(
            parsed_resume,
            analyzed_jd,
            fit_evaluation,
            ats_score,
            strategy_brief=strategy_brief,
        )
        
        # Get user memory and clarifications
        memory = load_memory()
        clarification_lines = [f"- {k.replace('_', ' ').capitalize()}: {v}" for k, v in memory.items() if v and isinstance(v, str) and v.strip()]
        clarifications = "\n".join(clarification_lines) if clarification_lines else "None"
        
        # Add confirmed skills
        if self.confirmed_skills:
            skills_list = ", ".join(self.confirmed_skills)
            clarifications = (
                f"{clarifications}\n\nSkills the user has confirmed. These may be used even if they "
                f"do not appear in the original resume:\n{skills_list}\n\n"
                "The usable set of skills is exactly this list plus whatever is already in the "
                "original resume. Nothing outside it."
            )
        
        # Add refinement feedback if provided
        if refinement_feedback:
            clarifications = (
                f"{clarifications}\n\nUSER FEEDBACK FOR REFINEMENT:\n"
                f"{refinement_feedback}\n\n"
                "This feedback takes priority over general instructions. "
                "You must preserve all core resume sections from the source resume unless the feedback explicitly asks to remove them."
            )
        if current_draft_text:
            clarifications = (
                f"{clarifications}\n\nCURRENT TAILORED DRAFT TO REFINE:\n"
                f"{current_draft_text}\n\n"
                "Use the current tailored draft as the editing baseline, but preserve factual content and missing sections from the original source resume."
            )
        normalized_preserve_sections = self._normalize_section_names(preserve_sections or [])
        if normalized_preserve_sections:
            clarifications = (
                f"{clarifications}\n\nSECTION PRESERVATION RULES:\n"
                + "\n".join(
                    f"- Preserve the {section} section exactly as it appears in the source resume."
                    for section in normalized_preserve_sections
                )
            )
        
        # One task per intensity, each declaring its own tier and cache policy.
        task_id = f"tailor.{intensity}" if intensity in ("light", "medium", "heavy") else "tailor.medium"

        logger.info("Calling LLM service to tailor resume", task=task_id)
        draft = self.llm_service.run_task(
            task_id,
            job_description=analyzed_jd.raw_text,
            resume=original_resume_text,
            clarifications=f"{clarifications}\n\nANALYSIS CONTEXT:\n{context}",
        ).strip()

        # The draft is the resume. A critique/revise pass used to run here and
        # overwrite it, but the critic only ever saw the first 3500 characters
        # while the reviser rewrote the whole document -- the tail was rewritten
        # against notes that never covered it. Voice survives by not rewriting
        # the same text three times; the humanizer is the one remaining pass.

        # Clean output
        result = self._clean_resume_output(draft, analyzed_jd.raw_text)
        result = self._restore_missing_core_sections(original_resume_text, result)
        result = self._restore_preserved_sections(
            original_resume_text,
            result,
            normalized_preserve_sections,
        )
        result = self._restore_protected_entries(
            current_draft_text or original_resume_text,
            result,
            protected_entry_texts or [],
        )

        logger.info("Resume Tailor Agent: Tailoring complete", result_length=len(result))
        return result

    def strip_unverified_metrics(self, resume_text: str, metrics: list[str]) -> Optional[str]:
        """Soften the listed numeric claims, leaving everything else alone.

        The caller supplies the exact strings its deterministic check flagged, so
        this never has to decide what counts as unverified -- only how to reword
        the lines carrying them.
        """
        if not metrics:
            return None
        try:
            cleaned = self.llm_service.run_task(
                "quality.strip_metrics",
                metrics="\n".join(f"- {m}" for m in metrics),
                resume_text=resume_text,
            ).strip()
        except Exception as e:
            logger.warning(f"Metric removal pass failed: {e}")
            return None

        cleaned = self._clean_resume_output(cleaned, "")
        # A collapse here would silently truncate the resume; better to keep the
        # flagged draft and let validation report it than to ship a stub.
        if len(cleaned) < len(resume_text) * 0.7:
            logger.warning(
                "Metric removal returned a much shorter resume; keeping the draft",
                before=len(resume_text), after=len(cleaned),
            )
            return None
        return cleaned

    def refine_single_entry(
        self,
        current_resume_text: str,
        original_resume_text: str,
        target_entry_text: str,
        feedback: str,
        analyzed_jd: "AnalyzedJD",
        preserve_sections: Optional[list[str]] = None,
        protected_entry_texts: Optional[list[str]] = None,
    ) -> Optional[str]:
        """Refine one specific bullet/paragraph in-place instead of rewriting the whole resume."""
        target_entry = parse_resume_document(current_resume_text).find_entry_by_text(target_entry_text)
        if not target_entry:
            logger.warning("Resume Tailor Agent: Target entry not found for single-entry refinement")
            return None

        rewritten_entry = self.llm_service.run_task(
            "tailor.refine_entry",
            jd_excerpt=analyzed_jd.raw_text[:2500],
            original_excerpt=original_resume_text[:3500],
            current_excerpt=current_resume_text[:4000],
            section_name=target_entry.section_name,
            target_entry=target_entry.text,
            feedback=feedback,
        ).strip()
        rewritten_entry = self._clean_single_entry_output(rewritten_entry)
        if not rewritten_entry:
            return None

        updated_resume = current_resume_text.replace(target_entry.text, rewritten_entry, 1)
        effective_preserve = [
            section for section in self._normalize_section_names(preserve_sections or [])
            if section != target_entry.section_name
        ]
        protected_entries = [
            entry_text for entry_text in (protected_entry_texts or [])
            if (entry_text or "").strip() and (entry_text or "").strip() != target_entry.text.strip()
        ]
        updated_resume = self._restore_preserved_sections(
            original_resume_text,
            updated_resume,
            effective_preserve,
        )
        return self._restore_protected_entries(
            current_resume_text,
            updated_resume,
            protected_entries,
        )

    def revert_single_entry(
        self,
        current_resume_text: str,
        original_resume_text: str,
        target_entry_text: str,
        preserve_sections: Optional[list[str]] = None,
        protected_entry_texts: Optional[list[str]] = None,
    ) -> Optional[str]:
        """Restore one targeted entry toward its best matching original wording."""
        current_doc = parse_resume_document(current_resume_text)
        original_doc = parse_resume_document(original_resume_text)
        target_entry = current_doc.find_entry_by_text(target_entry_text)
        if not target_entry:
            logger.warning("Resume Tailor Agent: Target entry not found for deterministic revert")
            return None

        replacement_entry = self._find_best_original_entry_match(target_entry, original_doc)
        if not replacement_entry:
            logger.warning("Resume Tailor Agent: No original entry match found for deterministic revert")
            return None

        updated_resume = current_resume_text.replace(target_entry.text, replacement_entry.text, 1)
        effective_preserve = [
            section for section in self._normalize_section_names(preserve_sections or [])
            if section != target_entry.section_name
        ]
        protected_entries = [
            entry_text for entry_text in (protected_entry_texts or [])
            if (entry_text or "").strip() and (entry_text or "").strip() != target_entry.text.strip()
        ]
        updated_resume = self._restore_preserved_sections(
            original_resume_text,
            updated_resume,
            effective_preserve,
        )
        return self._restore_protected_entries(
            current_resume_text,
            updated_resume,
            protected_entries,
        )
    
    def _build_tailoring_context(
        self,
        parsed_resume: "ParsedResume",
        analyzed_jd: "AnalyzedJD",
        fit_evaluation: "FitEvaluation",
        ats_score: Optional["ATSScore"],
        strategy_brief=None,
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
        context_parts.append(f"- Fit Score: {fit_evaluation.score}/10 ({'good fit' if fit_evaluation.should_apply else 'low fit'})")
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

        if strategy_brief:
            context_parts.append("\nAPPROVED STRATEGY BRIEF:")
            context_parts.append(f"- Archetype: {getattr(strategy_brief, 'archetype', 'general')}")
            context_parts.append(f"- Role Summary: {getattr(strategy_brief, 'role_summary', '')}")

            positioning = [
                str(item).strip()
                for item in getattr(strategy_brief, "positioning_strategy", [])[:5]
                if str(item).strip()
            ]
            if positioning:
                context_parts.append("- Positioning Themes:")
                context_parts.extend(f"  - {item}" for item in positioning)

            enabled_directives = [
                directive
                for directive in getattr(strategy_brief, "tailoring_directives", [])
                if getattr(directive, "enabled", True)
            ]
            if enabled_directives:
                context_parts.append("- Approved Tailoring Directives:")
                for directive in enabled_directives[:8]:
                    rationale = f" ({directive.rationale})" if getattr(directive, "rationale", "") else ""
                    context_parts.append(f"  - [{directive.section}] {directive.action}{rationale}")

            # Directives the user switched off are filtered out here rather than
            # listed as "do not apply". Naming an instruction in the prompt makes
            # it likelier to be followed, whatever the surrounding negation says.

            gap_assessments = getattr(strategy_brief, "gap_assessments", [])[:6]
            if gap_assessments:
                context_parts.append("- Gap Mitigation Policy:")
                for gap in gap_assessments:
                    context_parts.append(
                        f"  - {gap.requirement} [{gap.severity}]: {gap.mitigation or 'Acknowledge the gap and use only adjacent evidence.'}"
                    )

            unsupported_requirements = [
                evidence.requirement
                for evidence in getattr(strategy_brief, "requirement_evidence", [])
                if getattr(evidence, "status", "") == "gap"
            ]
            if unsupported_requirements:
                unsupported_summary = ", ".join(unsupported_requirements[:8])
                context_parts.append(f"- Do Not Add Unsupported Claims For: {unsupported_summary}")

            risk_notes = [
                str(item).strip()
                for item in getattr(strategy_brief, "risk_notes", [])[:5]
                if str(item).strip()
            ]
            if risk_notes:
                context_parts.append("- Risk Notes:")
                context_parts.extend(f"  - {item}" for item in risk_notes)

            context_parts.append(
                "- Truthfulness Rule: use the strategy to choose emphasis and wording, but never fabricate unsupported technologies, metrics, or equivalent experience."
            )
        
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

    def _restore_missing_core_sections(self, source_resume_text: str, tailored_resume_text: str) -> str:
        """Restore required sections if a rewrite accidentally dropped them."""
        source_doc = parse_resume_document(source_resume_text)
        tailored_doc = parse_resume_document(tailored_resume_text)

        source_sections = {section.name: section for section in source_doc.sections if section.name != "header"}
        tailored_section_names = {section.name for section in tailored_doc.sections if section.name != "header"}
        restored_blocks = []
        for section_name in ("summary", "education", "experience", "skills"):
            if section_name in source_sections and section_name not in tailored_section_names:
                section = source_sections[section_name]
                block_lines = [section.title] if section.title else []
                block_lines.extend(entry.text for entry in section.entries if entry.text.strip())
                restored_blocks.append("\n".join(block_lines).strip())

        if restored_blocks:
            logger.warning(
                "Resume Tailor Agent: Restored missing core sections after rewrite",
                restored_sections=[block.splitlines()[0] for block in restored_blocks if block],
            )
            return f"{tailored_resume_text.strip()}\n\n" + "\n\n".join(block for block in restored_blocks if block).strip()

        return tailored_resume_text

    def _normalize_section_names(self, section_names: list[str]) -> list[str]:
        normalized = []
        for name in section_names:
            clean = (name or "").strip().lower()
            if clean and clean not in normalized:
                normalized.append(clean)
        return normalized

    def _restore_preserved_sections(
        self,
        source_resume_text: str,
        tailored_resume_text: str,
        preserve_sections: list[str],
    ) -> str:
        """Restore user-preserved sections if a rewrite changed them materially."""
        if not preserve_sections:
            return tailored_resume_text

        source_sections = parse_resume_sections(source_resume_text)
        tailored_sections = parse_resume_sections(tailored_resume_text)
        source_lines = source_resume_text.splitlines()
        updated_lines = tailored_resume_text.splitlines()
        restored = []

        for section_name in sorted(
            preserve_sections,
            key=lambda name: tailored_sections.get(name).start_index if tailored_sections.get(name) else 10**9,
            reverse=True,
        ):
            source_section = source_sections.get(section_name)
            tailored_section = tailored_sections.get(section_name)
            if not source_section:
                continue

            source_block = self._render_section_from_lines(source_lines, source_section)
            tailored_block = self._render_section_from_lines(updated_lines, tailored_section) if tailored_section else ""

            if not tailored_section:
                updated_lines = self._append_section_lines(updated_lines, source_block.splitlines())
                restored.append(section_name)
                continue

            if self._section_changed_materially(source_block, tailored_block):
                updated_lines = (
                    updated_lines[:tailored_section.start_index]
                    + source_block.splitlines()
                    + updated_lines[tailored_section.end_index:]
                )
                restored.append(section_name)

        if restored:
            logger.warning(
                "Resume Tailor Agent: Restored preserved sections after refinement",
                restored_sections=restored,
            )

        return "\n".join(updated_lines).strip() + ("\n" if tailored_resume_text.endswith("\n") else "")

    def _restore_protected_entries(
        self,
        baseline_resume_text: str,
        tailored_resume_text: str,
        protected_entry_texts: list[str],
    ) -> str:
        """Restore exact baseline entries that the user explicitly locked."""
        normalized_entries = []
        for entry_text in protected_entry_texts:
            clean = (entry_text or "").strip()
            if clean and clean not in normalized_entries:
                normalized_entries.append(clean)
        if not normalized_entries:
            return tailored_resume_text

        baseline_doc = parse_resume_document(baseline_resume_text)
        updated_resume = tailored_resume_text
        restored_entries = []

        for protected_text in normalized_entries:
            if protected_text in updated_resume:
                continue
            baseline_entry = baseline_doc.find_entry_by_text(protected_text)
            if not baseline_entry:
                continue
            updated_doc = parse_resume_document(updated_resume)
            replacement_target = self._find_best_entry_match(baseline_entry, updated_doc)
            if not replacement_target or replacement_target.text.strip() == protected_text:
                continue
            updated_resume = updated_resume.replace(replacement_target.text, protected_text, 1)
            restored_entries.append(protected_text[:80])

        if restored_entries:
            logger.warning(
                "Resume Tailor Agent: Restored protected entries after rewrite",
                restored_entries=restored_entries,
            )
        return updated_resume

    def _render_section_from_lines(self, lines: list[str], section) -> str:
        if not section:
            return ""
        return "\n".join(lines[section.start_index:section.end_index]).strip()

    def _append_section_lines(self, resume_lines: list[str], section_lines: list[str]) -> list[str]:
        if not section_lines:
            return resume_lines
        base_lines = list(resume_lines)
        if base_lines and base_lines[-1].strip():
            base_lines.append("")
        return base_lines + section_lines

    def _section_changed_materially(self, source_block: str, tailored_block: str) -> bool:
        source_normalized = " ".join(source_block.split())
        tailored_normalized = " ".join(tailored_block.split())
        if source_normalized == tailored_normalized:
            return False
        similarity = SequenceMatcher(None, source_normalized, tailored_normalized).ratio()
        return similarity < 0.995

    def _find_best_entry_match(self, reference_entry, document) -> Optional[Any]:
        candidates = [
            entry for entry in document.iter_entries()
            if entry.section_name == reference_entry.section_name
        ]
        if not candidates:
            candidates = list(document.iter_entries())
        if not candidates:
            return None

        def score(entry) -> tuple[float, int]:
            ratio = SequenceMatcher(None, reference_entry.text.strip(), entry.text.strip()).ratio()
            kind_penalty = 0 if entry.kind == reference_entry.kind else 1
            return (ratio, -kind_penalty)

        best_entry = max(candidates, key=score)
        best_ratio = SequenceMatcher(None, reference_entry.text.strip(), best_entry.text.strip()).ratio()
        if best_ratio < 0.2:
            return None
        return best_entry

    def _find_best_original_entry_match(self, current_entry, original_doc) -> Optional[Any]:
        exact_match = original_doc.find_entry_by_text(current_entry.text)
        if exact_match:
            return exact_match
        return self._find_best_entry_match(current_entry, original_doc)

    def _clean_single_entry_output(self, text: str) -> str:
        cleaned = (text or "").strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()
        cleaned = cleaned.removeprefix("Rewritten entry:").strip()
        cleaned = cleaned.removeprefix("Revised entry:").strip()
        return cleaned
