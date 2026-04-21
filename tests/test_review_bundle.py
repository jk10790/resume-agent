from resume_agent.models.agent_models import (
    ResumeValidation,
    ValidationIssue,
    Severity,
    JobStrategyBrief,
    StrategyDirective,
)
from resume_agent.models.resume import FitEvaluation
from resume_agent.review.ats_parse import review_ats_parse
from resume_agent.review.bundle_builder import build_review_bundle


class DummyATSScore:
    def __init__(self):
        self.score = 72
        self.keyword_density = 0.6
        self.keyword_matches = {"Python": 2, "AWS": 1}
        self.missing_keywords = ["Kubernetes"]
        self.recommendations = ["Add missing required technologies where truthful."]


class DummyAnalyzedJD:
    required_skills = ["Python", "AWS", "Kubernetes"]
    preferred_skills = ["Terraform"]


def test_review_ats_parse_penalizes_tables_and_missing_contact():
    resume = """NAME\nSummary\n| skill | level |\n| --- | --- |\nPython | strong"""
    section = review_ats_parse(resume)

    assert section.score < 90
    assert any(issue.category == "parse_hazard" for issue in section.issues)
    assert any(issue.category == "contact" for issue in section.issues)


def test_build_review_bundle_separates_scores():
    validation = ResumeValidation(
        quality_score=82,
        is_valid=False,
        issues=[
            ValidationIssue(
                severity=Severity.ERROR,
                category="consistency",
                message="Fabricated technology/skill: Kubernetes was added but not in original resume",
                suggestion="Remove this fabricated item.",
            ),
            ValidationIssue(
                severity=Severity.WARNING,
                category="format",
                message="Bullet formatting is inconsistent.",
                suggestion="Use one bullet style consistently.",
            ),
        ],
        jd_coverage={"Python": True, "AWS": True, "Kubernetes": False},
        keyword_density=0.5,
        length_check={"word_count": 500},
        recommendations=["Tighten formatting."],
        ats_score=78,
        metric_provenance={"flagged_details": [{"raw": "35%", "line": "Reduced costs by 35%"}]},
    )
    fit_evaluation = FitEvaluation(
        score=7,
        should_apply=True,
        matching_areas=["Backend APIs", "AWS"],
        missing_areas=["Missing direct Kubernetes evidence"],
        recommendations=["Support platform claims with concrete experience."],
        confidence=0.9,
        reasoning="Strong backend alignment with one notable infrastructure gap.",
    )

    bundle = build_review_bundle(
        tailored_resume="Jane Doe\njane@example.com\n(555) 123-4567\nExperience\n- Built Python APIs on AWS",
        validation=validation,
        ats_score=DummyATSScore(),
        fit_evaluation=fit_evaluation,
        analyzed_jd=DummyAnalyzedJD(),
    )

    assert bundle.authenticity.score < bundle.editorial.score
    assert bundle.ats_parse.score >= 0
    assert bundle.job_match.score == 72
    assert bundle.job_match.metrics["missing_keywords"] == ["Kubernetes"]
    assert bundle.overall.recommendation in {"Safe to submit", "Needs edits before submitting"}


def test_build_review_bundle_includes_strategy_alignment():
    strategy_brief = JobStrategyBrief(
        company="Acme",
        job_title="Platform Engineer",
        fit_score=7,
        should_apply=True,
        confidence=0.8,
        role_summary="Emphasize backend platform delivery.",
        tailoring_directives=[
            StrategyDirective(
                id="dir_1",
                section="summary",
                action="Highlight platform engineering and AWS delivery",
                rationale="Sets the positioning early",
                enabled=True,
            )
        ],
    )

    bundle = build_review_bundle(
        tailored_resume="Jane Doe\nSummary\nPlatform engineering leader with AWS delivery experience.",
        validation=None,
        ats_score=DummyATSScore(),
        fit_evaluation=FitEvaluation(
            score=7,
            should_apply=True,
            matching_areas=["AWS"],
            missing_areas=[],
            recommendations=[],
            confidence=0.8,
        ),
        analyzed_jd=DummyAnalyzedJD(),
        strategy_brief=strategy_brief,
    )

    assert bundle.strategy_alignment is not None
    assert bundle.strategy_alignment.metrics["directive_count"] == 1
    assert bundle.strategy_alignment.metrics["directive_trace"][0]["status"] == "applied"
    assert bundle.overall.top_wins
    assert bundle.overall.readiness_checks


def test_build_review_bundle_tracks_disabled_and_underrepresented_directives():
    strategy_brief = JobStrategyBrief(
        company="Acme",
        job_title="Platform Engineer",
        fit_score=6,
        should_apply=True,
        confidence=0.7,
        role_summary="Proceed with a credible platform angle.",
        tailoring_directives=[
            StrategyDirective(
                id="dir_1",
                section="summary",
                action="Highlight platform engineering",
                rationale="Sets role framing",
                enabled=True,
            ),
            StrategyDirective(
                id="dir_2",
                section="skills",
                action="Add unsupported Kubernetes claim",
                rationale="This was disabled by the user",
                enabled=False,
            ),
        ],
    )

    bundle = build_review_bundle(
        tailored_resume="Jane Doe\nSummary\nBackend engineer with AWS delivery experience.",
        validation=None,
        ats_score=DummyATSScore(),
        fit_evaluation=FitEvaluation(
            score=6,
            should_apply=True,
            matching_areas=["AWS"],
            missing_areas=["Kubernetes"],
            recommendations=[],
            confidence=0.8,
        ),
        analyzed_jd=DummyAnalyzedJD(),
        strategy_brief=strategy_brief,
    )

    trace = bundle.strategy_alignment.metrics["directive_trace"]
    assert any(item["status"] == "underrepresented" for item in trace)
    assert any(item["status"] == "skipped_disabled" for item in trace)
