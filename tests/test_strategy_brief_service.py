from unittest.mock import Mock

from resume_agent.models.agent_models import JobStrategyBrief
from resume_agent.models.resume import FitEvaluation
from resume_agent.services.strategy_brief_service import StrategyBriefService


def test_build_brief_persists_canonical_jd_text_and_uses_fallbacks():
    llm = Mock()
    llm.invoke_with_retry.return_value = "{}"
    service = StrategyBriefService(llm)

    brief = service.build_brief(
        company="Acme",
        job_title="Platform Engineer",
        job_url="https://example.com/job",
        jd_text="Canonical JD text",
        parsed_resume=Mock(
            all_skills=["Python", "AWS"],
            job_titles=["Engineer"],
            total_years_experience=6,
            experience_summary="Backend/platform engineer",
            summary="Backend/platform engineer",
        ),
        analyzed_jd=Mock(
            company="Acme",
            job_title="Platform Engineer",
            required_skills=["Python", "Kubernetes"],
            preferred_skills=["Terraform"],
            key_responsibilities=["Build platform services"],
            technologies_needed=["Python", "AWS"],
            raw_text="JD raw text",
            summary="Platform role",
        ),
        fit_evaluation=FitEvaluation(
            score=4,
            should_apply=False,
            matching_areas=["Python"],
            missing_areas=["Kubernetes"],
            recommendations=["Proceed only with care"],
            confidence=0.8,
        ),
        profile_context=Mock(
            confirmed_skills=["Python"],
            confirmed_metric_records=[{"raw": "35%"}],
            confirmed_evidence_records=[{"kind": "achievement", "title": "Scaled APIs", "content": "Scaled APIs to high traffic"}],
            target_archetype_preferences=[{"archetype": "platform_infrastructure", "tier": "primary"}],
        ),
    )

    assert brief.jd_text == "Canonical JD text"
    assert brief.gating_decision == "stop_and_ask"
    assert brief.target_alignment == "primary"
    assert len(brief.tailoring_directives) > 0
    assert len(brief.requirement_evidence) > 0
    assert brief.gap_assessments == [] or all(gap.reason_code is None or isinstance(gap.reason_code, str) for gap in brief.gap_assessments)


def test_regenerate_section_updates_requested_section_only():
    llm = Mock()
    llm.invoke_with_retry.return_value = '{"risk_notes":["Clarify depth in SRE ownership."]}'
    service = StrategyBriefService(llm)

    brief = JobStrategyBrief(
        id=1,
        company="Acme",
        job_title="Platform Engineer",
        jd_text="JD text",
        fit_score=6,
        should_apply=True,
        confidence=0.8,
        role_summary="Old summary",
        requirement_evidence=[],
        gap_assessments=[],
        positioning_strategy=["Lead with backend systems."],
        tailoring_directives=[],
        interview_seeds=[],
        risk_notes=["Old note"],
    )

    updated = service.regenerate_section(
        brief=brief,
        section="risk_notes",
        parsed_resume=Mock(all_skills=["Python"], job_titles=["Engineer"]),
        analyzed_jd=Mock(required_skills=["Python"], preferred_skills=[], raw_text="JD text"),
        fit_evaluation=FitEvaluation(
            score=6,
            should_apply=True,
            matching_areas=["Python"],
            missing_areas=[],
            recommendations=[],
            confidence=0.8,
        ),
        profile_context=Mock(confirmed_metric_records=[], confirmed_evidence_records=[]),
    )

    assert updated.risk_notes == ["Clarify depth in SRE ownership."]
    assert updated.role_summary == "Old summary"


def test_build_brief_infers_gap_reason_codes_from_llm_output():
    llm = Mock()
    llm.invoke_with_retry.return_value = """
    {
      "archetype": "platform_infrastructure",
      "target_alignment": "secondary",
      "role_summary": "Good fit with one location blocker.",
      "gating_decision": "stop_and_ask",
      "requirement_evidence": [],
      "gap_assessments": [
        {"requirement": "US-only remote role", "severity": "hard_blocker", "mitigation": "Proceed only if relocation is realistic."}
      ],
      "positioning_strategy": [],
      "tailoring_directives": [],
      "interview_seeds": [],
      "risk_notes": []
    }
    """
    service = StrategyBriefService(llm)

    brief = service.build_brief(
        company="Acme",
        job_title="Platform Engineer",
        job_url=None,
        jd_text="JD text",
        parsed_resume=Mock(
            all_skills=["Python", "AWS"],
            job_titles=["Engineer"],
            total_years_experience=6,
            experience_summary="Backend/platform engineer",
            summary="Backend/platform engineer",
        ),
        analyzed_jd=Mock(
            company="Acme",
            job_title="Platform Engineer",
            required_skills=["Python"],
            preferred_skills=[],
            key_responsibilities=["Build platform services"],
            technologies_needed=["Python", "AWS"],
            raw_text="JD raw text",
            summary="Platform role",
        ),
        fit_evaluation=FitEvaluation(
            score=6,
            should_apply=True,
            matching_areas=["Python"],
            missing_areas=["US-only remote role"],
            recommendations=[],
            confidence=0.8,
        ),
        profile_context=Mock(
            confirmed_skills=["Python"],
            confirmed_metric_records=[],
            confirmed_evidence_records=[],
            target_archetype_preferences=[{"archetype": "platform_infrastructure", "tier": "secondary"}],
        ),
    )

    assert brief.target_alignment == "secondary"
    assert brief.gap_assessments[0].reason_code == "geo_restriction"


def test_build_brief_sharpens_summary_and_downgrades_vague_matches():
    llm = Mock()
    llm.invoke_with_retry.return_value = """
    {
      "archetype": "platform_infrastructure",
      "target_alignment": "primary",
      "role_summary": "This looks promising for platform work.",
      "gating_decision": "proceed",
      "requirement_evidence": [
        {"requirement": "Kubernetes", "status": "matched", "evidence": "Relevant experience", "source_section": ""}
      ],
      "gap_assessments": [
        {"requirement": "Deep Kubernetes operations", "severity": "stretch", "mitigation": "Frame adjacent platform ownership truthfully."}
      ],
      "positioning_strategy": [],
      "tailoring_directives": [
        {"id": "dir_1", "section": "summary", "action": "Lead with platform engineering depth", "rationale": "Best fit angle", "enabled": true}
      ],
      "interview_seeds": [],
      "risk_notes": []
    }
    """
    service = StrategyBriefService(llm)

    brief = service.build_brief(
        company="Acme",
        job_title="Platform Engineer",
        job_url=None,
        jd_text="JD text",
        parsed_resume=Mock(
            all_skills=["Python", "AWS"],
            job_titles=["Engineer"],
            total_years_experience=6,
            experience_summary="Backend/platform engineer",
            summary="Backend/platform engineer",
        ),
        analyzed_jd=Mock(
            company="Acme",
            job_title="Platform Engineer",
            required_skills=["Python", "Kubernetes"],
            preferred_skills=[],
            key_responsibilities=["Build platform services"],
            technologies_needed=["Python", "AWS"],
            raw_text="JD raw text",
            summary="Platform role",
        ),
        fit_evaluation=FitEvaluation(
            score=7,
            should_apply=True,
            matching_areas=["Python"],
            missing_areas=["Kubernetes depth"],
            recommendations=[],
            confidence=0.8,
        ),
        profile_context=Mock(
            confirmed_skills=["Python"],
            confirmed_metric_records=[],
            confirmed_evidence_records=[],
            target_archetype_preferences=[{"archetype": "platform_infrastructure", "tier": "primary"}],
        ),
    )

    assert brief.role_summary.startswith("Proceed:")
    assert "Do not overclaim" in brief.role_summary
    assert brief.requirement_evidence[0].status == "adjacent"
