from resume_agent.agents.resume_quality_agent import (
    ImprovedResume,
    IssueSeverity,
    QualityCategory,
    QualityIssue,
    QualityReport,
    ResumeQualityAgent,
)


class DummyLLMService:
    def invoke_with_retry(self, *args, **kwargs):
        return "stub"


def test_improve_resume_returns_candidate_when_score_regresses(monkeypatch):
    agent = ResumeQualityAgent(DummyLLMService())

    original_text = "Jane Doe\nExperience\n- Built APIs\nSkills\nPython\nEducation\nBS"
    quality_report = QualityReport(
        overall_score=63,
        ats_score=100,
        metrics_count=0,
        issues=[
            QualityIssue(
                category=QualityCategory.CONTENT,
                severity=IssueSeverity.MEDIUM,
                section="Experience",
                issue="Passive voice detected",
                suggestion="Use active voice",
            )
        ],
    )

    def fake_apply(_resume_text, _issues_to_fix, _user_answers, _resolution_guidance):
        return "Worse rewritten version", ["changed wording"], 0, {"llm_used": False}

    reports = [
        QualityReport(overall_score=55, ats_score=100, metrics_count=0),
    ]

    monkeypatch.setattr(agent, "_apply_improvements_with_context", fake_apply)
    monkeypatch.setattr(agent, "analyze_quality", lambda text: reports.pop(0))

    improved = agent.improve_resume(original_text, quality_report=quality_report)

    assert improved.improved_text == "Worse rewritten version"
    assert improved.before_score == 63
    assert improved.after_score == 55
    assert improved.accepted is False
    assert improved.score_regressed is True
    assert improved.changes_made == ["changed wording"]
    assert improved.after_report is not None
    assert improved.after_report.overall_score == 55


def test_find_verbatim_user_answer_snippets_flags_direct_paste():
    agent = ResumeQualityAgent(DummyLLMService())

    original = "Jane Doe\nExperience\n- Built backend services"
    candidate = "Jane Doe\nExperience\n- Led a platform rewrite. Reduced costs by 35% across 12 services during migration."
    user_answers = {
        "metrics_details": "Reduced costs by 35% across 12 services during migration."
    }

    snippets = agent._find_verbatim_user_answer_snippets(original, candidate, user_answers)

    assert snippets == ["Reduced costs by 35% across 12 services during migration."]


def test_quality_analysis_enriches_issue_with_target_text():
    agent = ResumeQualityAgent(DummyLLMService())
    resume_text = """Jane Doe
Experience
- Worked on various projects etc across the platform team
Skills
Python
Education
BS
"""

    report = agent.analyze_quality(resume_text)
    vague_issue = next(issue for issue in report.issues if "Vague term found" in issue.issue)

    assert vague_issue.id
    assert vague_issue.target_text == "- Worked on various projects etc across the platform team"
    assert vague_issue.target_entry_id


def test_apply_issue_resolutions_skips_and_customizes():
    agent = ResumeQualityAgent(DummyLLMService())
    issues = [
        QualityIssue(
            category=QualityCategory.CONTENT,
            severity=IssueSeverity.MEDIUM,
            section="Experience",
            issue="Passive voice detected",
            suggestion="Use active voice",
            id="issue-1",
        ),
        QualityIssue(
            category=QualityCategory.METRICS,
            severity=IssueSeverity.MEDIUM,
            section="Experience",
            issue="Only 0 quantified achievements found",
            suggestion="Add numbers",
            id="issue-2",
        ),
    ]

    filtered, guidance = agent._apply_issue_resolutions(
        issues,
        {
            "issue-1": {"action": "custom", "custom_text": "Rewrite this bullet to emphasize ownership"},
            "issue-2": {"action": "skip"},
        },
    )

    assert [issue.id for issue in filtered] == ["issue-1"]
    assert guidance == [
        "For issue 'Passive voice detected', use this user-approved language direction instead of the default fix: Rewrite this bullet to emphasize ownership"
    ]


def test_normalize_resume_layout_splits_glued_headers_and_bullets():
    agent = ResumeQualityAgent(DummyLLMService())
    raw = (
        "**EDUCATION** Master of Science in Data Science, Maryville University, 2021"
        "**WORK EXPERIENCE****Senior Software Engineer, Mastercard, 2023-2025** "
        "• Led the development of scalable data processing pipelines and backend services using Spring Boot, Apache NiFi, Kafka, Oracle DB"
    )

    normalized = agent._normalize_resume_layout(raw)

    assert "**EDUCATION**" in normalized
    assert "\n**WORK EXPERIENCE**" in normalized
    assert "\n**Senior Software Engineer, Mastercard, 2023-2025**" in normalized
    assert "\n• Led the development of scalable data processing pipelines" in normalized


def test_split_long_entry_text_uses_structured_entry_boundaries():
    agent = ResumeQualityAgent(DummyLLMService())
    long_entry = (
        "Results-driven software engineer with 6 years of experience in software development and automation. "
        "Leverages expertise in SDLC and STLC to drive process optimization and automation, resulting in improved SDLC efficiency. "
        "Develops scalable data processing pipelines and backend services using Spring Boot, Apache NiFi, and Kafka."
    )

    parts = agent._split_entry_text(long_entry)

    assert len(parts) >= 2
    assert all(part.strip() for part in parts)


def test_quality_agent_uses_fixed_rubric_reviewer():
    agent = ResumeQualityAgent(DummyLLMService())
    resume_text = """Jane Doe
SUMMARY
Results-driven software engineer with 6 years of experience in software development and automation. Leverages expertise in SDLC and STLC to drive process optimization and automation, resulting in improved SDLC efficiency.
EXPERIENCE
- Led development of backend services
SKILLS
Python
EDUCATION
BS
"""

    report = agent.analyze_quality(resume_text)

    assert agent.reviewer.REVIEW_STANDARD == "industry_resume_quality_v1"
    assert report.issues


def test_quality_report_exposes_subscores_and_best_next_fix():
    agent = ResumeQualityAgent(DummyLLMService())
    resume_text = """Jane Doe
SUMMARY
Results-driven software engineer with 6 years of experience in software development and automation. Leverages expertise in SDLC and STLC to drive process optimization and automation.
EXPERIENCE
- Worked on various projects etc across the platform team
SKILLS
Python
EDUCATION
BS
"""

    report = agent.analyze_quality(resume_text)

    assert report.subscores
    assert report.top_driver is not None
    assert report.best_next_fix is not None
    assert report.best_next_fix["issue_id"] in {issue.id for issue in report.issues}
