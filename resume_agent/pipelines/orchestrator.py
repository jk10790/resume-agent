"""The orchestrator: the one entry point for evaluating a role and tailoring for it.

This replaces two forked workflow services (`ResumeWorkflowService` and
`MultiAgentWorkflowService`, ~1750 lines between them) that implemented the same
stages with different fallbacks and different fit prompts, so the same resume and
role could score differently depending on which one a caller reached for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..llm.pipeline import PipelineError, PipelineRun
from ..services.llm_service import get_llm_service
from ..utils.logger import logger
from .context import FitContext, FitRequest, PipelineServices, TailorContext, TailorRequest
from .definitions import (
    TAILOR_ONLY_STEPS,
    build_fit_pipeline,
    build_publish_pipeline,
    build_tailor_pipeline,
)


@dataclass
class FitOutcome:
    """A completed fit evaluation and what it cost."""

    evaluation: Any
    parsed_resume: Any = None
    analyzed_jd: Any = None
    resume_text: Optional[str] = None
    profile_context: Any = None
    usage: Dict[str, Any] = field(default_factory=dict)
    steps: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class TailorOutcome:
    """A completed tailoring run and what it cost."""

    tailored_resume: Optional[str] = None
    evaluation: Any = None
    strategy_brief: Any = None
    strategy_brief_id: Optional[int] = None
    validation: Any = None
    review_bundle: Any = None
    ats_score: Optional[int] = None
    ats_score_object: Any = None
    original_resume_text: Optional[str] = None
    tailored_doc_id: Optional[str] = None
    doc_url: Optional[str] = None
    diff_path: Optional[str] = None
    application_id: Optional[int] = None
    usage: Dict[str, Any] = field(default_factory=dict)
    steps: List[Dict[str, Any]] = field(default_factory=list)


def _step_records(run: PipelineRun) -> List[Dict[str, Any]]:
    return [
        {
            "name": record.name,
            "status": record.status,
            "duration_ms": record.duration_ms,
            "error": record.error,
            "usage": record.usage,
        }
        for record in run.steps
    ]


class ResumeOrchestrator:
    """Runs the fit and tailoring pipelines."""

    def __init__(self, llm_service: Optional[Any] = None, google_services: Optional[Any] = None):
        self.services = PipelineServices(
            llm_service=llm_service or get_llm_service(),
            google_services=google_services,
        )

    # -- evaluate ---------------------------------------------------------

    def evaluate_fit(
        self,
        request: FitRequest,
        *,
        progress: Optional[Callable[[str], None]] = None,
    ) -> FitOutcome:
        """Judge one role. Raises `PipelineError` rather than inventing a score."""
        context = FitContext(request=request, services=self.services)
        run = build_fit_pipeline().run(
            context, usage_source=self.services.llm_service, progress=progress
        )
        logger.info(
            "Fit evaluation complete",
            score=getattr(context.evaluation, "score", None),
            cost_usd=run.usage.get("cost_usd"),
        )
        return FitOutcome(
            evaluation=context.evaluation,
            parsed_resume=context.parsed_resume,
            analyzed_jd=context.analyzed_jd,
            resume_text=context.resume_text,
            profile_context=context.profile_context,
            usage=run.usage,
            steps=_step_records(run),
        )

    # -- tailor -----------------------------------------------------------

    def tailor(
        self,
        request: TailorRequest,
        *,
        prior: Optional[FitOutcome] = None,
        strategy_brief: Optional[Any] = None,
        current_draft_text: Optional[str] = None,
        progress: Optional[Callable[[str], None]] = None,
    ) -> TailorOutcome:
        """Tailor the resume for one role.

        `prior` carries a fit evaluation the caller already has, so approving a
        strategy brief and then tailoring does not re-parse and re-judge what was
        established a moment ago -- the duplicate work the old per-step workflow
        could not avoid.

        `strategy_brief` is an already-approved brief. Supplying it skips the
        step that would build one, which is what tailoring after approval wants:
        the brief the user approved is the brief that must be used.
        """
        context = self._seeded_context(request, prior, current_draft_text=current_draft_text)
        if strategy_brief is not None:
            context.strategy_brief = strategy_brief
            context.strategy_brief_id = getattr(strategy_brief, "id", None)
            context.approval_stage = "final_resume"
            context.approval_status = "approved"

        # A brief is built only when the caller will actually use one: either it
        # supplied an approved brief already, or it asked for one to be made.
        wants_strategy = strategy_brief is None and request.include_strategy_brief

        if prior is not None:
            only = tuple(
                step for step in TAILOR_ONLY_STEPS
                if step != "build_strategy" or wants_strategy
            )
        elif not wants_strategy:
            only = tuple(
                step.name for step in build_tailor_pipeline().steps
                if step.name != "build_strategy"
            )
        else:
            only = None

        run = build_tailor_pipeline().run(
            context, usage_source=self.services.llm_service, only=only, progress=progress
        )
        logger.info(
            "Tailoring complete",
            length=len(context.tailored_resume or ""),
            cost_usd=run.usage.get("cost_usd"),
        )
        return self._outcome(context, run)

    def build_strategy(
        self,
        request: TailorRequest,
        *,
        prior: FitOutcome,
        progress: Optional[Callable[[str], None]] = None,
    ) -> TailorOutcome:
        """Produce the strategy brief for an already-evaluated role.

        Its own entry point because the brief is a human approval gate: the UI
        asks for it, waits, and only then tailors. Passing `prior` is what keeps
        that second call from re-parsing and re-judging.
        """
        context = self._seeded_context(request, prior)
        run = build_tailor_pipeline().run(
            context,
            usage_source=self.services.llm_service,
            only=("build_strategy",),
            progress=progress,
        )
        return self._outcome(context, run)

    def publish(
        self,
        request: TailorRequest,
        tailored_resume: str,
        *,
        evaluation: Any = None,
        analyzed_jd: Any = None,
        strategy_brief_id: Optional[int] = None,
        resume_text: Optional[str] = None,
        progress: Optional[Callable[[str], None]] = None,
    ) -> TailorOutcome:
        """Save an approved draft to Drive, write a diff, and record the application."""
        context = TailorContext(
            request=request,
            services=self.services,
            tailored_resume=tailored_resume,
            evaluation=evaluation,
            analyzed_jd=analyzed_jd,
            strategy_brief_id=strategy_brief_id,
            resume_text=resume_text,
            original_resume_text=resume_text,
        )
        run = build_publish_pipeline().run(
            context, usage_source=self.services.llm_service, progress=progress
        )
        return self._outcome(context, run)

    def _seeded_context(
        self,
        request: TailorRequest,
        prior: Optional[FitOutcome],
        *,
        current_draft_text: Optional[str] = None,
    ) -> TailorContext:
        """Build a tailoring context, carrying over an evaluation already made."""
        context = TailorContext(
            request=request,
            services=self.services,
            current_draft_text=current_draft_text,
        )
        if prior is not None:
            context.parsed_resume = prior.parsed_resume
            context.analyzed_jd = prior.analyzed_jd
            context.evaluation = prior.evaluation
            context.resume_text = prior.resume_text
            context.original_resume_text = prior.resume_text
            context.profile_context = prior.profile_context
        return context

    @staticmethod
    def _outcome(context: TailorContext, run: PipelineRun) -> TailorOutcome:
        return TailorOutcome(
            tailored_resume=context.tailored_resume,
            evaluation=context.evaluation,
            strategy_brief=context.strategy_brief,
            strategy_brief_id=context.strategy_brief_id,
            validation=context.validation,
            review_bundle=context.review_bundle,
            ats_score=context.ats_score,
            ats_score_object=context.ats_score_object,
            original_resume_text=context.original_resume_text,
            tailored_doc_id=context.tailored_doc_id,
            doc_url=context.doc_url,
            diff_path=context.diff_path,
            application_id=context.application_id,
            usage=run.usage,
            steps=_step_records(run),
        )


__all__ = [
    "ResumeOrchestrator",
    "FitOutcome",
    "TailorOutcome",
    "FitRequest",
    "TailorRequest",
    "PipelineError",
]
