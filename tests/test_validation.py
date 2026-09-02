"""Rules-based validation of a tailored resume.

These checks are the safety net under every prompt: whatever wording produced the
draft, a number that is not in the source resume gets flagged here, for free.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from resume_agent.validation import rules, validate_tailored_resume

ORIGINAL = """Jane Doe
jane@example.com

# Experience
**Senior Engineer** | Acme | 2019 - 2024
- Built payment services in Python on AWS
- Led the migration to Kubernetes across 12 services
- Mentored two junior engineers

# Education
BS Computer Science, State University
"""


def _draft(body: str) -> str:
    return ORIGINAL.replace("- Built payment services in Python on AWS", body)


def test_a_number_absent_from_the_source_is_flagged():
    """The exact defect the prompt work was chasing: an invented measurement."""
    issues, provenance = rules.check_metric_provenance(
        ORIGINAL, _draft("- Reduced latency by 35% across the payment services")
    )

    assert issues, "an invented metric must be reported"
    assert any("35%" in issue.message for issue in issues)
    assert "35%" in " ".join(provenance["flagged"])


def test_a_number_already_in_the_source_passes():
    issues, provenance = rules.check_metric_provenance(
        ORIGINAL, _draft("- Migrated 12 services to Kubernetes")
    )

    assert issues == []
    assert provenance["flagged"] == []


def test_a_confirmed_metric_is_allowed():
    """The user can vouch for a figure that is not written on the resume."""
    issues, _provenance = rules.check_metric_provenance(
        ORIGINAL,
        _draft("- Cut checkout latency by 35%"),
        # Stored records carry the same normalized form extract_metrics produces.
        verified_metric_records=[
            {"raw": "35%", "normalized": "percent:35", "line": "", "category": "percent"}
        ],
    )

    assert issues == []


def test_an_added_years_of_experience_claim_is_flagged():
    issues = rules.check_experience_years(ORIGINAL, ORIGINAL + "\n10 years of experience in fintech")

    assert issues
    assert "10 years of experience" in issues[0].message


def test_a_skill_the_candidate_never_claimed_is_flagged():
    """Tailoring has a motive to add exactly the skills the JD asks for."""
    issues = rules.check_skill_authenticity(
        ORIGINAL,
        _draft("- Built payment services in Python, Rust and Go on AWS"),
        jd_skills=["Rust", "Python"],
    )

    messages = " ".join(issue.message for issue in issues)
    assert "Rust" in messages
    assert "Python" not in messages


def test_a_confirmed_skill_is_not_flagged():
    issues = rules.check_skill_authenticity(
        ORIGINAL,
        _draft("- Built payment services in Python and Rust on AWS"),
        jd_skills=["Rust"],
        confirmed_skills=["Rust"],
    )

    assert issues == []


def test_very_short_skill_tokens_are_skipped():
    """'Go' matches inside 'Google'; the false positives outweigh the catches."""
    issues = rules.check_skill_authenticity(
        ORIGINAL, _draft("- Worked with Google Cloud"), jd_skills=["Go", "R"]
    )

    assert issues == []


def test_job_description_text_copied_into_the_resume_is_flagged():
    jd = "We are looking for a senior engineer to own our payments platform end to end. " * 4
    issues = rules.check_jd_leakage(ORIGINAL + jd[:200], jd)

    assert issues
    assert issues[0].category == "content"


def test_an_empty_draft_is_an_error():
    issues = rules.check_structure("")

    assert issues
    assert str(issues[0].severity) in ("error", "Severity.ERROR")


def test_validation_runs_without_an_llm_by_default(monkeypatch):
    """Rules mode is the default precisely so validation always actually runs."""
    monkeypatch.setattr("resume_agent.storage.user_memory.get_verified_metrics", lambda: [])

    llm = Mock()
    result = validate_tailored_resume(
        original_resume=ORIGINAL,
        tailored_resume=_draft("- Reduced latency by 35%"),
        jd_text="Python role",
        llm_service=llm,
    )

    assert result.is_valid is False
    assert any("35%" in issue.message for issue in result.issues)
    llm.run_task.assert_not_called()


def test_off_mode_skips_validation():
    result = validate_tailored_resume(
        original_resume=ORIGINAL,
        tailored_resume=_draft("- Reduced latency by 35%"),
        mode="off",
    )

    assert result.is_valid is True
    assert result.issues == []


def test_full_mode_adds_one_llm_pass_on_top_of_the_rules(monkeypatch):
    """One escalation call, where the old path cost four."""
    monkeypatch.setattr("resume_agent.storage.user_memory.get_verified_metrics", lambda: [])

    llm = Mock()
    llm.run_task.return_value = {
        "issues": [
            {"severity": "warning", "category": "content", "message": "Summary buries the platform work"}
        ]
    }

    result = validate_tailored_resume(
        original_resume=ORIGINAL,
        tailored_resume=_draft("- Built payment services"),
        jd_text="Python role",
        llm_service=llm,
        mode="full",
    )

    assert llm.run_task.call_count == 1
    assert any("buries the platform work" in issue.message for issue in result.issues)


def test_a_failed_llm_pass_keeps_the_rule_findings(monkeypatch):
    monkeypatch.setattr("resume_agent.storage.user_memory.get_verified_metrics", lambda: [])

    llm = Mock()
    llm.run_task.side_effect = RuntimeError("provider down")

    result = validate_tailored_resume(
        original_resume=ORIGINAL,
        tailored_resume=_draft("- Reduced latency by 35%"),
        llm_service=llm,
        mode="full",
    )

    assert any("35%" in issue.message for issue in result.issues)


def test_capability_phrases_are_not_flagged_as_fabricated_skills():
    """A job description asks for capabilities in prose; those are not technologies.

    Flagging 'Service architecture' and 'Cross-functional collaboration' as
    fabricated skills buried the real findings in noise on a live run.
    """
    draft = _draft("- Applied service architecture and cross-functional collaboration")
    issues = rules.check_skill_authenticity(
        ORIGINAL,
        draft,
        jd_skills=["Service architecture", "Cross-functional collaboration", "Technical communication"],
    )

    assert issues == []


def test_a_multi_word_technology_is_still_checked():
    """The capability filter must not swallow real tool names."""
    issues = rules.check_skill_authenticity(
        ORIGINAL,
        _draft("- Built pipelines with Apache Kafka"),
        jd_skills=["Apache Kafka"],
    )

    assert issues, "a named technology absent from the source must still be flagged"
    assert "Apache Kafka" in issues[0].message


def test_the_header_check_accepts_the_markdown_the_tailorer_produces():
    """The tailoring prompt mandates '# Name'; the check must not warn on it.

    A bare-name-only pattern fired this warning on every single draft, which is
    the fastest way to teach someone to ignore validation output.
    """
    body = "\n" + ("filler words here " * 60)
    for header in ("# Jane Doe", "**Jane Doe**", "Jane Doe"):
        warnings = [i for i in rules.check_structure(header + body) if "header" in i.message]
        assert warnings == [], f"{header!r} should not warn"

    missing = [i for i in rules.check_structure("lorem ipsum dolor" + body) if "header" in i.message]
    assert missing, "a draft with no name at the top should still warn"


def test_a_worded_scale_claim_is_flagged_and_queued_for_removal():
    """"millions of orders" is the same fabrication as "40% faster", without digits.

    The metric extractor only matches numerals, so a draft carrying one of these
    was reported clean on a live run.
    """
    draft = _draft("- Built payment services supporting millions of orders daily")

    issues, phrases = rules.check_scale_claims(ORIGINAL, draft)

    assert phrases == ["millions of orders"]
    assert "millions of orders" in issues[0].message


def test_a_scale_claim_the_source_makes_is_allowed():
    original = ORIGINAL + "\n- Served millions of requests per day"
    issues, phrases = rules.check_scale_claims(original, original)

    assert issues == []
    assert phrases == []


def test_scale_claims_join_the_flagged_list_the_grounding_pass_strips(monkeypatch):
    monkeypatch.setattr("resume_agent.storage.user_memory.get_verified_metrics", lambda: [])

    result = validate_tailored_resume(
        original_resume=ORIGINAL,
        tailored_resume=_draft("- Built services handling millions of orders"),
    )

    assert "millions of orders" in result.metric_provenance["flagged"]
    assert result.is_valid is False


def test_first_person_is_flagged_when_the_source_has_none():
    """A resume is terse fragments, not prose about the candidate.

    Told to "sound human", the tailorer drifted into narration -- "I maintain
    full ownership", "Take my turn in the on-call rotation" -- against a source
    resume using no first person at all.
    """
    draft = _draft("- I've focused on building payment services, and I maintain full ownership")

    issues = rules.check_voice_drift(ORIGINAL, draft)

    assert any("first-person" in i.message for i in issues)


def test_a_candidate_who_writes_in_first_person_keeps_their_voice():
    """The check is relative to the source, not a fixed house style."""
    source = ORIGINAL.replace("- Built payment services", "- I built payment services")
    draft = source.replace("Led the migration", "I led the migration")

    issues = rules.check_voice_drift(source, draft)

    assert not any("first-person" in i.message for i in issues)


def test_bullets_that_grow_to_explain_themselves_are_flagged():
    long_bullet = (
        "- Built payment services in Python on AWS, which meant the team could ship "
        "features faster and with more confidence than they previously could manage"
    )
    issues = rules.check_voice_drift(ORIGINAL, _draft(long_bullet))

    assert any("explaining rather than stating" in i.message for i in issues)


def test_accomplishment_lines_are_found_without_bullet_markers():
    """A resume exported from Google Docs carries no '-' markers.

    Matching only markdown bullets found nothing in the source and silently
    skipped the length comparison.
    """
    unmarked = "Jane Doe\nBuilt payment services in Python on AWS for the checkout team\n"

    assert rules._bullets(unmarked) == [
        "Built payment services in Python on AWS for the checkout team"
    ]


def test_a_draft_matching_the_source_register_is_clean():
    issues = rules.check_voice_drift(ORIGINAL, _draft("- Built payment APIs in Python on AWS"))

    assert issues == []


def test_a_capability_phrase_containing_a_real_term_is_still_skipped():
    """"API development" is a capability, even though "API" is a real term.

    Requiring every word to be generic still flagged it on a live run, because
    "API" is not a generic word. One generic word makes the phrase a capability.
    """
    draft = _draft("- Focused on API development and data engineering")

    issues = rules.check_skill_authenticity(
        ORIGINAL, draft, jd_skills=["API development", "Data engineering"]
    )

    assert issues == []


def test_multi_word_technologies_survive_the_capability_filter():
    draft = _draft("- Built pipelines with Apache Kafka and shipped a Node.js service")

    flagged = {
        issue.message.split("'")[1]
        for issue in rules.check_skill_authenticity(
            ORIGINAL, draft, jd_skills=["Apache Kafka", "Node.js", "API development"]
        )
    }

    assert flagged == {"Apache Kafka", "Node.js"}
