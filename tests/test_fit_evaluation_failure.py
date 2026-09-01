"""A failed fit evaluation must fail, not invent a score.

When the provider is unreachable the pipeline used to return 5/10 with
confidence 0.5, which the API happily returned as 200 and Discovery persisted on
the role. Nothing downstream could tell that apart from a real 5/10, so these
tests pin the failure to an error at every layer that used to substitute one.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from resume_agent.agents.fit_evaluator_agent import FitEvaluatorAgent
from resume_agent.services.fit_evaluation_service import FitEvaluationError, evaluate_fit_for_jd
from resume_agent.utils.exceptions import FitEvaluationUnavailable, LLMError


def _resume(skills=("Python", "AWS")):
    resume = Mock()
    resume.all_skills = list(skills)
    resume.total_experience_years = 7
    resume.job_titles = ["Senior Software Engineer"]
    resume.education = []
    return resume


def _jd(required=("Python",), preferred=("Kubernetes",)):
    jd = Mock()
    jd.required_skills = list(required)
    jd.preferred_skills = list(preferred)
    jd.min_experience_years = 5
    jd.job_title = "Staff Software Engineer"
    jd.company = "Example"
    jd.responsibilities = []
    jd.technologies = []
    jd.education_requirements = []
    return jd


def test_unreachable_provider_raises_instead_of_scoring_five():
    llm = Mock()
    llm.invoke_with_retry = Mock(side_effect=LLMError("Anthropic API failed: 404 not_found_error"))
    agent = FitEvaluatorAgent(llm)

    with pytest.raises(FitEvaluationUnavailable) as excinfo:
        agent.evaluate_fit(_resume(), _jd())

    assert "did not return a usable fit analysis" in str(excinfo.value)


def test_response_without_json_raises_rather_than_defaulting():
    llm = Mock()
    llm.invoke_with_retry = Mock(return_value="I cannot evaluate this posting.")
    agent = FitEvaluatorAgent(llm)

    with pytest.raises(FitEvaluationUnavailable):
        agent.evaluate_fit(_resume(), _jd())


def test_a_scored_response_still_comes_through():
    llm = Mock()
    llm.invoke_with_retry = Mock(
        return_value=(
            '{"fit_score": 8, "should_apply": true, "confidence": 0.9, '
            '"experience_match": "strong", "experience_gap_years": 0, '
            '"education_match": true, "missing_education": [], '
            '"strengths": ["Python"], "weaknesses": [], "recommendations": [], '
            '"matching_areas": ["Python"], "missing_areas": []}'
        )
    )
    agent = FitEvaluatorAgent(llm)

    evaluation = agent.evaluate_fit(_resume(), _jd())

    assert evaluation.score == 8
    assert evaluation.should_apply is True
    assert evaluation.confidence == 0.9


def test_evaluate_fit_for_jd_surfaces_the_failure(monkeypatch):
    """The shared service both fit endpoints call must raise, so neither returns 200."""

    class _FailingWorkflow:
        def __init__(self, *_args, **_kwargs):
            pass

        def execute_workflow_step(self, _request, step, result):
            from resume_agent.services.resume_workflow import WorkflowStep

            result.current_step = WorkflowStep.ERROR
            result.error = "Fit evaluation failed: provider unreachable"
            return result

    monkeypatch.setattr(
        "resume_agent.services.multi_agent_workflow.MultiAgentWorkflowService",
        _FailingWorkflow,
    )

    with pytest.raises(FitEvaluationError) as excinfo:
        evaluate_fit_for_jd(
            jd_text="Job description body",
            resume_text="Resume body",
            llm_service=Mock(),
        )

    assert excinfo.value.code == "failed"
    assert "provider unreachable" in str(excinfo.value)


def test_discovery_does_not_persist_a_fit_when_evaluation_fails(monkeypatch):
    """A failed evaluation must leave the discovered_roles row untouched."""
    from resume_agent.services import discover_roles_service as module

    writes = []
    monkeypatch.setattr(
        module,
        "get_discovered_role_for_user",
        lambda *_args, **_kwargs: {"id": 1, "raw_text": "Job description body", "canonical_url": "u"},
    )
    monkeypatch.setattr(
        "resume_agent.services.fit_evaluation_service.load_resume_text",
        lambda *_args, **_kwargs: "Resume body",
    )
    monkeypatch.setattr(
        "resume_agent.services.fit_evaluation_service.evaluate_fit_for_jd",
        Mock(side_effect=FitEvaluationError("provider unreachable", code="failed")),
    )
    for writer in ("set_discovered_role_fit_for_user", "record_discovered_role_feedback_for_user"):
        if hasattr(module, writer):
            monkeypatch.setattr(module, writer, lambda *a, **k: writes.append(a))

    service = module.DiscoverRolesService.__new__(module.DiscoverRolesService)
    service.llm_service = Mock()

    with pytest.raises(FitEvaluationError):
        service.evaluate_role_fit(1, 1, google_services=("drive", "docs"))

    assert writes == []
