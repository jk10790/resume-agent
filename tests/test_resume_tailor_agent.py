from unittest.mock import Mock

from resume_agent.agents.resume_tailor_agent import ResumeTailorAgent
from resume_agent.utils.agent_cache import AgentCache


def test_restore_missing_core_sections_restores_education():
    agent = ResumeTailorAgent(Mock(), confirmed_skills=[])
    source_resume = """Subhasmita Panda

## Summary
Software engineer with backend experience.

## Experience
Senior Software Engineer | Mastercard
- Built services

## Education
Master of Science in Data Science | Maryville University
Bachelor of Technology in Computer Science | Raajdhani Engineering College
"""
    tailored_resume = """Subhasmita Panda

## Summary
Software engineer with backend experience.

## Experience
Senior Software Engineer | Mastercard
- Built services
"""

    restored = agent._restore_missing_core_sections(source_resume, tailored_resume)

    assert "## Education" in restored or "**EDUCATION**" in restored
    assert "Master of Science in Data Science" in restored


def test_tailoring_cache_key_varies_with_current_draft():
    cache = AgentCache()
    key_a = cache._tailoring_cache_key(
        "original resume",
        "job description",
        "medium",
        "preserve education",
        None,
        "draft version a",
        None,
        None,
        False,
    )
    key_b = cache._tailoring_cache_key(
        "original resume",
        "job description",
        "medium",
        "preserve education",
        None,
        "draft version b",
        None,
        None,
        False,
    )

    assert key_a != key_b


def test_tailoring_cache_key_varies_with_target_and_protected_entries():
    cache = AgentCache()
    key_a = cache._tailoring_cache_key(
        "original resume",
        "job description",
        "medium",
        "tighten this line",
        None,
        "current draft",
        "- First line",
        ["- Keep this line"],
        False,
    )
    key_b = cache._tailoring_cache_key(
        "original resume",
        "job description",
        "medium",
        "tighten this line",
        None,
        "current draft",
        "- Second line",
        ["- Keep this line"],
        False,
    )
    key_c = cache._tailoring_cache_key(
        "original resume",
        "job description",
        "medium",
        "tighten this line",
        None,
        "current draft",
        "- First line",
        ["- Different preserved line"],
        False,
    )

    assert key_a != key_b
    assert key_a != key_c


def test_restore_preserved_sections_restores_changed_education():
    agent = ResumeTailorAgent(Mock(), confirmed_skills=[])
    source_resume = """Subhasmita Panda

## Summary
Software engineer with backend experience.

## Education
Master of Science in Data Science | Maryville University

## Experience
Senior Software Engineer | Mastercard
- Built services
"""
    tailored_resume = """Subhasmita Panda

## Summary
Software engineer with backend experience.

## Education
Master of Science in Analytics | Other University

## Experience
Senior Software Engineer | Mastercard
- Built services
"""

    restored = agent._restore_preserved_sections(source_resume, tailored_resume, ["education"])

    assert "Master of Science in Data Science | Maryville University" in restored
    assert "Master of Science in Analytics | Other University" not in restored


def test_refine_single_entry_only_rewrites_targeted_line():
    llm_service = Mock()
    llm_service.invoke_with_retry.return_value = "- Delivered scalable backend services with stronger technical emphasis"
    agent = ResumeTailorAgent(llm_service, confirmed_skills=[])
    current_resume = """## Summary
Software engineer with backend experience.

## Experience
- Built services
- Improved monitoring
"""

    updated = agent.refine_single_entry(
        current_resume_text=current_resume,
        original_resume_text=current_resume,
        target_entry_text="- Built services",
        feedback="Make this sound more technical",
        analyzed_jd=Mock(raw_text="Backend engineer role"),
        preserve_sections=["summary"],
    )

    assert "- Delivered scalable backend services with stronger technical emphasis" in updated
    assert "- Improved monitoring" in updated
    assert "Software engineer with backend experience." in updated


def test_restore_protected_entries_restores_locked_line():
    agent = ResumeTailorAgent(Mock(), confirmed_skills=[])
    baseline_resume = """## Summary
Backend engineer with cloud experience.

## Experience
- Built services
- Improved monitoring
"""
    rewritten_resume = """## Summary
Backend engineer with cloud experience.

## Experience
- Rewrote services with new phrasing
- Improved monitoring
"""

    restored = agent._restore_protected_entries(
        baseline_resume,
        rewritten_resume,
        ["- Built services"],
    )

    assert "- Built services" in restored
    assert "- Rewrote services with new phrasing" not in restored


def test_revert_single_entry_restores_best_original_match():
    agent = ResumeTailorAgent(Mock(), confirmed_skills=[])
    original_resume = """## Experience
- Built scalable backend services using Spring Boot
- Improved monitoring
"""
    current_resume = """## Experience
- Delivered scalable backend services with stronger technical emphasis
- Improved monitoring
"""

    reverted = agent.revert_single_entry(
        current_resume_text=current_resume,
        original_resume_text=original_resume,
        target_entry_text="- Delivered scalable backend services with stronger technical emphasis",
    )

    assert "- Built scalable backend services using Spring Boot" in reverted
    assert "- Delivered scalable backend services with stronger technical emphasis" not in reverted
