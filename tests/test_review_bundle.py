from resume_agent.models.agent_models import ResumeValidation, ValidationIssue, Severity
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
