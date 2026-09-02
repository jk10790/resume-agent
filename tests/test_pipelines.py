"""The pipeline engine and the two pipelines built on it.

These pin the properties the old enum-branching workflow could not guarantee:
ordering, no duplicate derivation, per-step cost attribution, and that an
optional step failing degrades the run instead of losing the work.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, List, Optional
from unittest.mock import Mock

import pytest

from resume_agent.llm.pipeline import Pipeline, PipelineError, Step
from resume_agent.models.agent_models import ResumeValidation
from resume_agent.pipelines.context import (
    FitContext,
    FitRequest,
    PipelineServices,
    TailorContext,
    TailorRequest,
)
from resume_agent.pipelines.definitions import build_fit_pipeline, build_tailor_pipeline


# ---------------------------------------------------------------- engine


@dataclass
class _Ctx:
    a: Optional[str] = None
    b: Optional[str] = None
    c: Optional[str] = None


def test_steps_run_in_order_and_pass_values_forward():
    pipeline = Pipeline(
        "demo",
        [
            Step("first", lambda c: {"a": "A"}, produces=("a",)),
            Step("second", lambda c: {"b": c.a + "B"}, requires=("a",), produces=("b",)),
        ],
    )
    context = _Ctx()
    run = pipeline.run(context)

    assert context.b == "AB"
    assert [(r.name, r.status) for r in run.steps] == [("first", "completed"), ("second", "completed")]


def test_a_step_missing_its_input_fails_the_run():
    """The guarantee that lets steps stop defensively re-deriving their inputs."""
    pipeline = Pipeline("demo", [Step("needs_a", lambda c: {"b": "B"}, requires=("a",), produces=("b",))])

    with pytest.raises(PipelineError) as excinfo:
        pipeline.run(_Ctx())

    assert "not available" in str(excinfo.value)
    assert excinfo.value.step == "needs_a"


def test_an_optional_step_that_fails_does_not_lose_the_run():
    pipeline = Pipeline(
        "demo",
        [
            Step("first", lambda c: {"a": "A"}, produces=("a",)),
            Step("flaky", _boom, requires=("a",), optional=True),
            Step("last", lambda c: {"c": "C"}, produces=("c",)),
        ],
    )
    context = _Ctx()
    run = pipeline.run(context)

    assert context.c == "C"
    assert run.record_for("flaky").status == "failed"
    assert run.record_for("last").status == "completed"


def test_a_step_that_does_not_produce_what_it_declared_fails():
    pipeline = Pipeline("demo", [Step("liar", lambda c: {}, produces=("a",))])

    with pytest.raises(PipelineError) as excinfo:
        pipeline.run(_Ctx())

    assert "produced nothing" in str(excinfo.value)


def test_only_runs_the_named_steps():
    pipeline = Pipeline(
        "demo",
        [
            Step("first", lambda c: {"a": "A"}, produces=("a",)),
            Step("second", lambda c: {"b": "B"}, produces=("b",)),
        ],
    )
    context = _Ctx(a="seeded")
    run = pipeline.run(context, only=("second",))

    assert context.b == "B"
    assert run.record_for("first").status == "skipped"


def test_usage_is_attributed_per_step():
    """Cost has to land on the step that spent it, or it cannot guide anything."""
    source = _UsageSource([
        {"cost_usd": 0.0, "calls": 0},   # before first
        {"cost_usd": 0.10, "calls": 1},  # after first
        {"cost_usd": 0.10, "calls": 1},  # before second
        {"cost_usd": 0.70, "calls": 2},  # after second
    ])
    pipeline = Pipeline(
        "demo",
        [
            Step("cheap", lambda c: {"a": "A"}, produces=("a",)),
            Step("dear", lambda c: {"b": "B"}, produces=("b",)),
        ],
    )
    run = pipeline.run(_Ctx(), usage_source=source)

    assert run.record_for("cheap").usage["cost_usd"] == pytest.approx(0.10)
    assert run.record_for("dear").usage["cost_usd"] == pytest.approx(0.60)
    assert run.cost_usd == pytest.approx(0.70)


def _boom(_context):
    raise RuntimeError("nope")


class _UsageSource:
    def __init__(self, snapshots: List[dict]):
        self._snapshots = list(snapshots)

    def usage_snapshot(self) -> dict:
        return self._snapshots.pop(0) if self._snapshots else {}


# ---------------------------------------------------------------- pipelines


def _services(**overrides) -> PipelineServices:
    services = PipelineServices(llm_service=Mock(), google_services=(Mock(), Mock()))
    services._agents.update(overrides)
    return services


def _parsed_resume(**kwargs) -> Mock:
    resume = Mock()
    resume.all_skills = kwargs.get("skills", ["Python", "AWS"])
    resume.raw_text = "Jane Doe\nSenior Engineer\nBuilt services in Python on AWS."
    resume.job_titles = ["Senior Engineer"]
    resume.total_years_experience = 7
    resume.education = []
    return resume


def _analyzed_jd() -> SimpleNamespace:
    return SimpleNamespace(
        required_skills=["Python"],
        preferred_skills=["Kubernetes"],
        technologies_needed=["Python"],
        required_experience_years=5,
        raw_text="We need a Python engineer.",
        company="Acme",
        job_title="Backend Engineer",
        key_responsibilities=[],
    )


def _brief(id: int = 1, gating_decision: str = "proceed") -> SimpleNamespace:
    return SimpleNamespace(
        id=id,
        gating_decision=gating_decision,
        tailoring_directives=[],
        requirement_evidence=[],
        gap_assessments=[],
        positioning_strategy=[],
        risk_notes=[],
        archetype="software_engineering",
        role_summary="",
    )


def _evaluation(score: int = 8) -> SimpleNamespace:
    return SimpleNamespace(
        score=score,
        should_apply=score >= 6,
        confidence=0.9,
        matching_areas=["Python"],
        missing_areas=[],
        recommendations=[],
        reasoning="",
    )


def test_fit_pipeline_parses_and_judges_once(monkeypatch):
    parser, analyzer, evaluator = Mock(), Mock(), Mock()
    parser.parse.return_value = _parsed_resume()
    analyzer.analyze.return_value = _analyzed_jd()
    evaluator.evaluate_fit.return_value = _evaluation()

    services = _services(resume_parser=parser, jd_analyzer=analyzer, fit_evaluator=evaluator)
    context = FitContext(
        request=FitRequest(jd_text="We need a Python engineer.", resume_text="Jane Doe\nEngineer"),
        services=services,
    )
    monkeypatch.setattr(
        "resume_agent.services.profile_context_service.load_profile_context", lambda _id: None
    )

    run = build_fit_pipeline().run(context)

    assert context.evaluation.score == 8
    # Exactly one of each: the pipeline guarantees ordering, so nothing re-derives.
    assert parser.parse.call_count == 1
    assert analyzer.analyze.call_count == 1
    assert evaluator.evaluate_fit.call_count == 1
    assert not run.failed


def test_fit_pipeline_uses_the_supplied_resume_without_touching_drive():
    """The extension posts resume text directly; that must not hit Drive."""
    parser, analyzer, evaluator = Mock(), Mock(), Mock()
    parser.parse.return_value = _parsed_resume()
    analyzer.analyze.return_value = _analyzed_jd()
    evaluator.evaluate_fit.return_value = _evaluation()

    services = _services(resume_parser=parser, jd_analyzer=analyzer, fit_evaluator=evaluator)
    services.google_services = None
    context = FitContext(
        request=FitRequest(jd_text="JD text here", resume_text="Jane Doe\nEngineer"),
        services=services,
    )

    build_fit_pipeline().run(context)

    assert context.resume_text == "Jane Doe\nEngineer"


def test_tailor_pipeline_resumes_from_an_existing_evaluation(monkeypatch):
    """Approving a brief then tailoring must not re-parse or re-judge."""
    parser, analyzer, evaluator = Mock(), Mock(), Mock()
    tailor, humanizer, scorer, strategy = Mock(), Mock(), Mock(), Mock()
    tailor.tailor.return_value = "TAILORED RESUME BODY"
    humanizer.humanize.return_value = "HUMANISED RESUME BODY"
    scorer.score.return_value = SimpleNamespace(score=82, missing_keywords=[], recommendations=[])
    strategy.find_existing_brief.return_value = _brief(id=5)
    strategy.persist_brief.side_effect = lambda _user, brief: brief

    services = _services(
        resume_parser=parser,
        jd_analyzer=analyzer,
        fit_evaluator=evaluator,
        resume_tailor=tailor,
        humanizer=humanizer,
        ats_scorer=scorer,
        strategy_brief=strategy,
    )
    context = TailorContext(
        request=TailorRequest(jd_text="JD", resume_text="ORIGINAL", company="Acme"),
        services=services,
        parsed_resume=_parsed_resume(),
        analyzed_jd=_analyzed_jd(),
        evaluation=_evaluation(),
        resume_text="ORIGINAL RESUME TEXT",
        original_resume_text="ORIGINAL RESUME TEXT",
    )

    monkeypatch.setattr(
        "resume_agent.utils.agent_cache.AgentCache.get_tailored_result", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "resume_agent.utils.agent_cache.AgentCache.set_tailored_result", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "resume_agent.validation.validate_tailored_resume",
        lambda **k: ResumeValidation(quality_score=90, is_valid=True, issues=[]),
    )

    from resume_agent.pipelines.definitions import TAILOR_ONLY_STEPS

    run = build_tailor_pipeline().run(context, only=TAILOR_ONLY_STEPS)

    assert parser.parse.call_count == 0
    assert analyzer.analyze.call_count == 0
    assert evaluator.evaluate_fit.call_count == 0
    assert context.tailored_resume == "HUMANISED RESUME BODY"
    assert not run.failed
    # Derived review is assembled from results already in hand -- no extra call.
    assert context.review_bundle is not None


def test_low_fit_skips_ats_scoring(monkeypatch):
    """Scoring a draft for a role the candidate should not apply to is wasted spend."""
    tailor, humanizer, scorer, strategy = Mock(), Mock(), Mock(), Mock()
    tailor.tailor.return_value = "DRAFT"
    humanizer.humanize.return_value = "DRAFT"
    strategy.find_existing_brief.return_value = _brief(id=1, gating_decision="stop_and_ask")
    strategy.persist_brief.side_effect = lambda _user, brief: brief

    services = _services(
        resume_tailor=tailor, humanizer=humanizer, ats_scorer=scorer, strategy_brief=strategy
    )
    context = TailorContext(
        request=TailorRequest(jd_text="JD", resume_text="ORIGINAL"),
        services=services,
        parsed_resume=_parsed_resume(),
        analyzed_jd=_analyzed_jd(),
        evaluation=_evaluation(score=3),
        resume_text="ORIGINAL",
        original_resume_text="ORIGINAL",
    )

    from resume_agent.pipelines.definitions import TAILOR_ONLY_STEPS

    monkeypatch.setattr(
        "resume_agent.utils.agent_cache.AgentCache.get_tailored_result", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "resume_agent.utils.agent_cache.AgentCache.set_tailored_result", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "resume_agent.validation.validate_tailored_resume",
        lambda **k: ResumeValidation(quality_score=90, is_valid=True, issues=[]),
    )

    build_tailor_pipeline().run(context, only=TAILOR_ONLY_STEPS)

    assert scorer.score.call_count == 0
    assert context.ats_score is None


def test_a_step_failure_carries_the_cause_s_error_code():
    """Callers map codes onto HTTP status; a blanket 'failed' loses a 401."""
    from resume_agent.services.resume_source import ResumeUnavailable

    def _no_session(_context):
        raise ResumeUnavailable("sign in first", code="no_google_session")

    pipeline = Pipeline("demo", [Step("load_resume", _no_session, produces=("a",))])

    with pytest.raises(PipelineError) as excinfo:
        pipeline.run(_Ctx())

    assert excinfo.value.code == "no_google_session"
    assert excinfo.value.step == "load_resume"


def test_unverified_metrics_are_removed_not_merely_reported():
    """Detection without removal ships the fabrication.

    An earlier version flagged invented figures and handed the user the draft
    with them still in it; on a real resume that produced "99.9% uptime" and
    "sub-200ms p95 latency" against a resume containing neither.
    """
    from resume_agent.models.agent_models import ResumeValidation
    from resume_agent.pipelines import steps

    tailor = Mock()
    tailor.strip_unverified_metrics.return_value = "CLEANED DRAFT"
    services = _services(resume_tailor=tailor)

    dirty = ResumeValidation(
        quality_score=55,
        is_valid=False,
        issues=[],
        metric_provenance={"flagged": ["99.9%", "200ms"]},
    )
    context = TailorContext(
        request=TailorRequest(jd_text="JD", resume_text="ORIGINAL"),
        services=services,
        tailored_resume="DRAFT claiming 99.9% uptime and 200ms latency",
        original_resume_text="ORIGINAL",
        validation=dirty,
    )

    produced = steps.enforce_grounding(context)

    tailor.strip_unverified_metrics.assert_called_once()
    assert tailor.strip_unverified_metrics.call_args[0][1] == ["99.9%", "200ms"]
    assert produced["tailored_resume"] == "CLEANED DRAFT"


def test_a_clean_draft_costs_nothing_to_ground():
    from resume_agent.models.agent_models import ResumeValidation
    from resume_agent.pipelines import steps

    tailor = Mock()
    services = _services(resume_tailor=tailor)
    context = TailorContext(
        request=TailorRequest(jd_text="JD", resume_text="ORIGINAL"),
        services=services,
        tailored_resume="CLEAN DRAFT",
        original_resume_text="ORIGINAL",
        validation=ResumeValidation(
            quality_score=100, is_valid=True, issues=[], metric_provenance={"flagged": []}
        ),
    )

    assert steps.enforce_grounding(context) == {}
    tailor.strip_unverified_metrics.assert_not_called()


def test_every_step_s_deferred_imports_resolve():
    """Steps import lazily, so a wrong module path only surfaces when it runs.

    `save_to_google` imported GOOGLE_DOC_MIME from `config`, where it does not
    live. Nothing caught it until a real Drive save returned a 502, because no
    test ever reached that step.
    """
    import ast
    import importlib
    import inspect

    from resume_agent.pipelines import steps as steps_module

    source = inspect.getsource(steps_module)
    tree = ast.parse(source)
    failures = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.level == 0:
            continue
        # level 1 is the package holding steps.py, level 2 its parent.
        package = {1: "resume_agent.pipelines", 2: "resume_agent"}.get(node.level)
        if package is None:
            continue
        module = f"{package}.{node.module}" if node.module else package
        try:
            imported = importlib.import_module(module)
        except Exception as e:
            failures.append(f"{module}: {e}")
            continue
        for alias in node.names:
            if not hasattr(imported, alias.name):
                failures.append(f"{module} has no {alias.name!r}")

    assert not failures, "unresolvable step imports: " + "; ".join(failures)


def _tailor_services():
    tailor, humanizer, scorer, strategy = Mock(), Mock(), Mock(), Mock()
    tailor.tailor.return_value = "DRAFT"
    humanizer.humanize.return_value = "DRAFT"
    scorer.score.return_value = SimpleNamespace(score=80, missing_keywords=[], recommendations=[])
    strategy.find_existing_brief.return_value = _brief()
    strategy.persist_brief.side_effect = lambda _u, b: b
    return _services(
        resume_tailor=tailor, humanizer=humanizer, ats_scorer=scorer, strategy_brief=strategy
    ), strategy


def _prior():
    return SimpleNamespace(
        parsed_resume=_parsed_resume(), analyzed_jd=_analyzed_jd(), evaluation=_evaluation(),
        resume_text="ORIGINAL", profile_context=None,
    )


def _orchestrator(services):
    from resume_agent.pipelines import ResumeOrchestrator

    orch = ResumeOrchestrator(llm_service=services.llm_service)
    orch.services = services
    return orch


def test_the_strategy_brief_is_not_built_unless_someone_will_read_it(monkeypatch):
    """It measured at 45% of a tailoring run on a path that never showed it."""
    _patch_tailoring(monkeypatch)
    services, strategy = _tailor_services()

    _orchestrator(services).tailor(
        TailorRequest(jd_text="JD", resume_text="ORIGINAL"), prior=_prior()
    )

    strategy.find_existing_brief.assert_not_called()
    strategy.build_brief.assert_not_called()


def test_a_caller_can_ask_for_the_strategy_brief(monkeypatch):
    _patch_tailoring(monkeypatch)
    services, strategy = _tailor_services()

    _orchestrator(services).tailor(
        TailorRequest(jd_text="JD", resume_text="ORIGINAL", include_strategy_brief=True),
        prior=_prior(),
    )

    strategy.find_existing_brief.assert_called_once()


def test_an_approved_brief_is_used_rather_than_rebuilt(monkeypatch):
    """Tailoring after approval must use the brief the user approved."""
    _patch_tailoring(monkeypatch)
    services, strategy = _tailor_services()
    approved = _brief(id=99)

    outcome = _orchestrator(services).tailor(
        TailorRequest(jd_text="JD", resume_text="ORIGINAL"),
        prior=_prior(),
        strategy_brief=approved,
    )

    strategy.find_existing_brief.assert_not_called()
    assert outcome.strategy_brief_id == 99


def test_the_dedicated_entry_point_still_builds_a_brief(monkeypatch):
    """The job-strategy flow is where the brief is reviewed, so it always builds."""
    _patch_tailoring(monkeypatch)
    services, strategy = _tailor_services()

    _orchestrator(services).build_strategy(
        TailorRequest(jd_text="JD", resume_text="ORIGINAL"), prior=_prior()
    )

    strategy.find_existing_brief.assert_called_once()


def _patch_tailoring(monkeypatch):
    from resume_agent.models.agent_models import ResumeValidation

    monkeypatch.setattr(
        "resume_agent.utils.agent_cache.AgentCache.get_tailored_result", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "resume_agent.utils.agent_cache.AgentCache.set_tailored_result", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "resume_agent.validation.validate_tailored_resume",
        lambda **k: ResumeValidation(
            quality_score=100, is_valid=True, issues=[], metric_provenance={"flagged": []}
        ),
    )
