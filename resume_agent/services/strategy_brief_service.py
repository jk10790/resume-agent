"""
Strategy brief generation and persistence.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from ..models.agent_models import (
    GapAssessment,
    JobStrategyBrief,
    RequirementEvidence,
    StrategyDirective,
    UserProfileContext,
)
from ..storage.user_store import (
    find_job_strategy_brief_for_user,
    save_job_strategy_brief_for_user,
)
from ..utils.logger import logger
from .archetype_strategy import (
    detect_job_archetype,
    get_target_alignment,
    infer_gap_reason_code,
    normalize_target_archetype_preferences,
)


class StrategyBriefService:
    """Build the canonical per-job strategy brief before tailoring starts."""

    def __init__(self, llm_service):
        self.llm_service = llm_service

    def build_brief(
        self,
        *,
        company: str,
        job_title: str,
        job_url: Optional[str],
        jd_text: Optional[str],
        parsed_resume,
        analyzed_jd,
        fit_evaluation,
        profile_context: Optional[UserProfileContext] = None,
    ) -> JobStrategyBrief:
        confirmed_skills = ", ".join((profile_context.confirmed_skills if profile_context else [])[:25]) or "None confirmed"
        confirmed_metrics = ", ".join(
            record.get("raw", "")
            for record in (profile_context.confirmed_metric_records if profile_context else [])[:15]
            if record.get("raw")
        ) or "None confirmed"
        confirmed_evidence = " | ".join(
            f"{record.get('kind', 'evidence')}: {record.get('title', '')} - {record.get('content', '')}"
            for record in (profile_context.confirmed_evidence_records if profile_context else [])[:10]
            if record.get("title") and record.get("content")
        ) or "None confirmed"
        target_preferences = normalize_target_archetype_preferences(
            profile_context.target_archetype_preferences if profile_context else []
        )
        detected_archetype = detect_job_archetype(analyzed_jd)
        target_alignment = get_target_alignment(detected_archetype, target_preferences)
        target_preferences_summary = ", ".join(
            f"{item['archetype']} ({item['tier']})" for item in target_preferences
        ) or "None saved"

        prompt = SystemMessage(
            content="""You are a recruiting strategist for a human-in-the-loop resume tool.

Build a structured strategy brief for one job. The brief must be truthful, grounded, and useful for resume tailoring.

Rules:
1. Never invent experience or skills.
2. If evidence is indirect, mark it as status "adjacent", not "matched".
3. If a requirement is unsupported, add it to gap_assessments with a truthful mitigation.
4. Tailoring directives must be concrete and section-specific.
5. Use one archetype from:
   - software_engineering
   - platform_infrastructure
   - data_ml_ai
   - applied_ai_llmops
   - product_technical_product
   - solutions_customer_engineering
6. If fit_score < 5 or should_apply=false, set gating_decision to "stop_and_ask". Otherwise use "proceed".
7. Include target_alignment using one of: primary, secondary, adjacent, unranked.
8. For notable blockers, include a reason_code when possible:
   stack_mismatch, seniority_mismatch, geo_restriction, onsite_requirement, domain_mismatch, people_management_gap, education_requirement, clearance_requirement.

Return valid JSON only with this shape:
{
  "archetype": "...",
  "target_alignment": "primary|secondary|adjacent|unranked",
  "role_summary": "...",
  "gating_decision": "proceed|stop_and_ask",
  "requirement_evidence": [{"requirement":"...","status":"matched|adjacent|gap","evidence":"...","source_section":"..."}],
  "gap_assessments": [{"requirement":"...","severity":"hard_blocker|stretch|nice_to_have","mitigation":"...","reason_code":"optional_code"}],
  "positioning_strategy": ["...", "..."],
  "tailoring_directives": [{"id":"dir_1","section":"summary|experience|skills|projects","action":"...","rationale":"...","enabled":true}],
  "interview_seeds": ["...", "..."],
  "risk_notes": ["...", "..."]
}"""
        )

        human = HumanMessage(
            content=f"""Company: {company or analyzed_jd.company or "Unknown"}
Job title: {job_title or analyzed_jd.job_title or "Unknown"}
Job URL: {job_url or "None"}

Fit evaluation:
- Score: {fit_evaluation.score}/10
- Should apply: {fit_evaluation.should_apply}
- Confidence: {fit_evaluation.confidence}
- Matching areas: {", ".join(fit_evaluation.matching_areas[:8]) or "None"}
- Missing areas: {", ".join(fit_evaluation.missing_areas[:8]) or "None"}
- Recommendations: {", ".join(fit_evaluation.recommendations[:8]) or "None"}
- Reasoning: {fit_evaluation.reasoning or "None"}

Resume summary:
- Skills: {", ".join(parsed_resume.all_skills[:25])}
- Job titles: {", ".join(parsed_resume.job_titles[:6])}
- Experience years: {parsed_resume.total_years_experience or "Not explicitly stated"}
- Summary: {parsed_resume.experience_summary or parsed_resume.summary or "None"}

JD summary:
- Required skills: {", ".join(analyzed_jd.required_skills[:20]) or "None"}
- Preferred skills: {", ".join(analyzed_jd.preferred_skills[:15]) or "None"}
- Responsibilities: {", ".join(analyzed_jd.key_responsibilities[:10]) or "None"}
- Technologies: {", ".join(analyzed_jd.technologies_needed[:15]) or "None"}
- Raw JD excerpt:
{analyzed_jd.raw_text[:3500]}

Confirmed user skills: {confirmed_skills}
Confirmed user metrics/evidence: {confirmed_metrics}
Confirmed reusable evidence/story inventory: {confirmed_evidence}
User target archetypes: {target_preferences_summary}
Detected role archetype from system heuristics: {detected_archetype}
"""
        )

        response = self.llm_service.invoke_with_retry([prompt, human]).strip()
        match = re.search(r"\{.*\}", response, re.DOTALL)
        payload: Dict[str, Any]
        if not match:
            logger.warning("Strategy brief LLM response missing JSON; using fallback")
            payload = {}
        else:
            payload = json.loads(match.group(0))

        requirement_evidence = [
            RequirementEvidence(**item)
            for item in payload.get("requirement_evidence", [])[:12]
            if isinstance(item, dict) and item.get("requirement")
        ]
        gap_assessments = [
            GapAssessment(
                **{
                    **item,
                    "reason_code": item.get("reason_code") or infer_gap_reason_code(item.get("requirement"), item.get("mitigation")),
                }
            )
            for item in payload.get("gap_assessments", [])[:8]
            if isinstance(item, dict) and item.get("requirement")
        ]
        directives = [
            StrategyDirective(**item)
            for item in payload.get("tailoring_directives", [])[:10]
            if isinstance(item, dict) and item.get("id") and item.get("section") and item.get("action")
        ]

        if not directives:
            directives = self._fallback_directives(analyzed_jd, fit_evaluation)

        if not requirement_evidence:
            requirement_evidence = self._fallback_requirement_evidence(analyzed_jd, fit_evaluation)

        resolved_target_alignment = str(payload.get("target_alignment") or target_alignment).strip()
        resolved_gating_decision = str(payload.get("gating_decision") or ("stop_and_ask" if (fit_evaluation.score < 5 or not fit_evaluation.should_apply) else "proceed"))
        requirement_evidence = self._normalize_requirement_evidence(requirement_evidence)
        directives = self._normalize_directives(directives)
        role_summary = self._sharpen_role_summary(
            str(payload.get("role_summary") or "").strip(),
            fit_evaluation=fit_evaluation,
            target_alignment=resolved_target_alignment,
            gating_decision=resolved_gating_decision,
            directives=directives,
            gaps=gap_assessments,
        )

        return JobStrategyBrief(
            company=company or analyzed_jd.company or "",
            job_title=job_title or analyzed_jd.job_title or "",
            job_url=job_url,
            jd_text=jd_text or analyzed_jd.raw_text,
            archetype=str(payload.get("archetype") or detected_archetype).strip(),
            target_alignment=resolved_target_alignment,
            role_summary=role_summary,
            fit_score=fit_evaluation.score,
            should_apply=fit_evaluation.should_apply,
            confidence=fit_evaluation.confidence,
            gating_decision=resolved_gating_decision,
            requirement_evidence=requirement_evidence,
            gap_assessments=gap_assessments,
            positioning_strategy=[str(item).strip() for item in payload.get("positioning_strategy", [])[:6] if str(item).strip()],
            tailoring_directives=directives,
            interview_seeds=[str(item).strip() for item in payload.get("interview_seeds", [])[:6] if str(item).strip()],
            risk_notes=[str(item).strip() for item in payload.get("risk_notes", [])[:6] if str(item).strip()],
        )

    def _normalize_requirement_evidence(self, evidence_items: List[RequirementEvidence]) -> List[RequirementEvidence]:
        normalized: List[RequirementEvidence] = []
        seen_requirements: set[str] = set()
        for item in evidence_items:
            requirement = str(item.requirement or "").strip()
            if not requirement:
                continue
            key = requirement.lower()
            if key in seen_requirements:
                continue
            seen_requirements.add(key)

            status = str(item.status or "gap").strip().lower()
            if status not in {"matched", "adjacent", "gap"}:
                status = "gap"

            evidence = str(item.evidence or "").strip()
            source_section = str(item.source_section or "").strip() or None
            lowered_evidence = evidence.lower()
            if status == "matched" and (
                not source_section
                or len(evidence) < 18
                or any(phrase in lowered_evidence for phrase in {"relevant experience", "similar work", "transferable", "related background"})
            ):
                status = "adjacent"

            normalized.append(
                RequirementEvidence(
                    requirement=requirement,
                    status=status,
                    evidence=evidence,
                    source_section=source_section,
                )
            )
        return normalized

    def _normalize_directives(self, directives: List[StrategyDirective]) -> List[StrategyDirective]:
        normalized: List[StrategyDirective] = []
        seen_ids: set[str] = set()
        for index, directive in enumerate(directives, start=1):
            directive_id = str(directive.id or f"dir_{index}").strip()
            if directive_id in seen_ids:
                directive_id = f"{directive_id}_{index}"
            seen_ids.add(directive_id)
            normalized.append(
                StrategyDirective(
                    id=directive_id,
                    section=str(directive.section or "experience").strip().lower(),
                    action=str(directive.action or "").strip(),
                    rationale=str(directive.rationale or "").strip(),
                    enabled=bool(directive.enabled),
                )
            )
        return normalized

    def _sharpen_role_summary(
        self,
        summary: str,
        *,
        fit_evaluation,
        target_alignment: str,
        gating_decision: str,
        directives: List[StrategyDirective],
        gaps: List[GapAssessment],
    ) -> str:
        base = summary.strip()
        if len(base) > 220:
            base = base[:217].rstrip(" ,.;:") + "."
        if not base:
            base = "Use the strongest grounded experience and avoid stretching unsupported claims."

        directive_hint = directives[0].action if directives else "emphasize the strongest relevant experience"
        gap_hint = gaps[0].requirement if gaps else ""
        opening = "Proceed" if gating_decision == "proceed" else "Stop and review"
        alignment = target_alignment.replace("_", " ") if target_alignment else "unranked"
        sharpened = f"{opening}: this is a {alignment} target role. Lead with {directive_hint.lower()}."
        if gap_hint:
            sharpened += f" Do not overclaim around {gap_hint.lower()}."

        if base.lower().startswith(opening.lower()):
            return base
        return f"{sharpened} {base}".strip()

    def persist_brief(self, user_id: Optional[int], brief: JobStrategyBrief) -> JobStrategyBrief:
        if not user_id:
            return brief
        stored = save_job_strategy_brief_for_user(user_id, brief.model_dump(), brief_id=brief.id)
        return JobStrategyBrief(**stored)

    def find_existing_brief(self, user_id: Optional[int], *, company: str, job_title: str) -> Optional[JobStrategyBrief]:
        if not user_id or not company.strip() or not job_title.strip():
            return None
        existing = find_job_strategy_brief_for_user(user_id, company=company, job_title=job_title)
        if not existing:
            return None
        return JobStrategyBrief(**existing)

    def regenerate_section(
        self,
        *,
        brief: JobStrategyBrief,
        section: str,
        parsed_resume,
        analyzed_jd,
        fit_evaluation,
        profile_context: Optional[UserProfileContext] = None,
    ) -> JobStrategyBrief:
        section = (section or "").strip().lower()
        if section not in {
            "role_summary",
            "requirement_evidence",
            "gap_assessments",
            "positioning_strategy",
            "tailoring_directives",
            "interview_seeds",
            "risk_notes",
        }:
            raise ValueError(f"Unsupported strategy section: {section}")

        prompt = SystemMessage(
            content="""You are updating one section of an existing job strategy brief for a human-in-the-loop resume product.

Rules:
1. Return valid JSON only.
2. Update only the requested section.
3. Keep the content truthful and grounded in the resume/profile/JD.
4. If support is indirect, use adjacent framing instead of overstating equivalence.
5. Preserve target_alignment and include blocker reason_code values when the section supports it."""
        )
        human = HumanMessage(
            content=f"""Requested section: {section}

Existing strategy brief:
{brief.model_dump_json(indent=2)}

Fit evaluation:
- Score: {fit_evaluation.score}/10
- Should apply: {fit_evaluation.should_apply}
- Matching areas: {", ".join(fit_evaluation.matching_areas[:8]) or "None"}
- Missing areas: {", ".join(fit_evaluation.missing_areas[:8]) or "None"}

Resume skills: {", ".join(parsed_resume.all_skills[:25])}
Resume titles: {", ".join(parsed_resume.job_titles[:8])}
JD required skills: {", ".join(analyzed_jd.required_skills[:20])}
JD preferred skills: {", ".join(analyzed_jd.preferred_skills[:15])}
Confirmed metrics/evidence: {", ".join(record.get("raw", "") for record in (profile_context.confirmed_metric_records if profile_context else [])[:12] if record.get("raw")) or "None"}
Confirmed evidence/story inventory: {" | ".join(f"{record.get('kind', 'evidence')}: {record.get('title', '')} - {record.get('content', '')}" for record in (profile_context.confirmed_evidence_records if profile_context else [])[:8] if record.get('title') and record.get('content')) or "None"}

Return JSON with exactly one top-level key named "{section}". For list sections, return the full regenerated list."""
        )

        response = self.llm_service.invoke_with_retry([prompt, human]).strip()
        match = re.search(r"\{.*\}", response, re.DOTALL)
        if not match:
            raise ValueError("Section regeneration response did not contain valid JSON")
        payload = json.loads(match.group(0))
        updated = brief.model_copy(deep=True)
        value = payload.get(section)

        if section == "role_summary":
            updated.role_summary = str(value or updated.role_summary).strip()
        elif section == "requirement_evidence":
            updated.requirement_evidence = [
                RequirementEvidence(**item)
                for item in (value or [])
                if isinstance(item, dict) and item.get("requirement")
            ]
        elif section == "gap_assessments":
            updated.gap_assessments = [
                GapAssessment(
                    **{
                        **item,
                        "reason_code": item.get("reason_code") or infer_gap_reason_code(item.get("requirement"), item.get("mitigation")),
                    }
                )
                for item in (value or [])
                if isinstance(item, dict) and item.get("requirement")
            ]
        elif section == "positioning_strategy":
            updated.positioning_strategy = [str(item).strip() for item in (value or []) if str(item).strip()]
        elif section == "tailoring_directives":
            updated.tailoring_directives = [
                StrategyDirective(**item)
                for item in (value or [])
                if isinstance(item, dict) and item.get("id") and item.get("section") and item.get("action")
            ]
        elif section == "interview_seeds":
            updated.interview_seeds = [str(item).strip() for item in (value or []) if str(item).strip()]
        elif section == "risk_notes":
            updated.risk_notes = [str(item).strip() for item in (value or []) if str(item).strip()]

        return updated

    def _fallback_requirement_evidence(self, analyzed_jd, fit_evaluation) -> List[RequirementEvidence]:
        evidence: List[RequirementEvidence] = []
        missing = {item.lower() for item in fit_evaluation.missing_areas[:10]}
        for requirement in analyzed_jd.required_skills[:10]:
            lowered = requirement.lower()
            status = "gap" if any(lowered in item for item in missing) else "matched"
            evidence.append(
                RequirementEvidence(
                    requirement=requirement,
                    status=status,
                    evidence="Ground this during tailoring using confirmed resume evidence." if status != "gap" else "No direct support detected yet.",
                    source_section="experience" if status != "gap" else None,
                )
            )
        return evidence

    def _fallback_directives(self, analyzed_jd, fit_evaluation) -> List[StrategyDirective]:
        directives = [
            StrategyDirective(
                id="summary_positioning",
                section="summary",
                action="Rewrite the summary to foreground the strongest role-aligned experience and technologies already supported by the source resume.",
                rationale="The summary should frame the candidate for this role before the reader scans details.",
            ),
            StrategyDirective(
                id="experience_alignment",
                section="experience",
                action="Prioritize bullets that best match the most important JD responsibilities and requirements.",
                rationale="Experience bullets carry the strongest proof for fit and seniority.",
            ),
        ]
        if analyzed_jd.required_skills:
            directives.append(
                StrategyDirective(
                    id="skills_alignment",
                    section="skills",
                    action=f"Make sure the skills section clearly surfaces the strongest truthful matches to: {', '.join(analyzed_jd.required_skills[:6])}.",
                    rationale="Recruiters and ATS both rely on visible skill coverage.",
                )
            )
        if fit_evaluation.missing_areas:
            directives.append(
                StrategyDirective(
                    id="gap_mitigation",
                    section="experience",
                    action=f"Use adjacent evidence to address this gap without overstating experience: {fit_evaluation.missing_areas[0]}.",
                    rationale="The resume should mitigate gaps truthfully rather than ignore them.",
                )
            )
        return directives
