"""Typed contexts for the fit and tailor pipelines.

The workflow this replaces passed one thirty-field result object through every
stage, so no stage could tell which fields were its inputs and which merely
happened to be set. Here each pipeline has a context holding only what its own
steps read and write, and the request that produced it.

`FitContext` is deliberately the smaller of the two and is not a base class of
the tailoring one: evaluating a role is the cheap, high-volume path, and it
should not carry -- or be tempted to populate -- the fields tailoring needs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class FitRequest:
    """What the caller asks for when evaluating one role."""

    jd_text: str
    company: str = ""
    job_title: str = ""
    job_url: Optional[str] = None
    resume_doc_id: Optional[str] = None
    resume_text: Optional[str] = None
    local_user_id: Optional[int] = None
    discovered_role_id: Optional[int] = None


@dataclass
class TailorRequest(FitRequest):
    """Everything the tailoring pipeline accepts beyond a fit evaluation."""

    intensity: Optional[str] = None
    sections_to_tailor: Optional[List[str]] = None
    preserve_sections: Optional[List[str]] = None
    protected_entry_texts: Optional[List[str]] = None
    target_entry_text: Optional[str] = None
    refinement_feedback: Optional[str] = None
    revert_target_entry: bool = False
    save_folder_id: Optional[str] = None
    track_application: bool = True
    strategy_brief_id: Optional[int] = None
    strategy_approved: bool = False
    # Off by default. The brief is a user-facing artifact -- an approval gate,
    # gap assessment and interview prep -- reviewed through the job-strategy
    # endpoints. Building one on a path that never shows it measured at 45% of
    # the run for a draft that was no better without it.
    include_strategy_brief: bool = False


@dataclass
class FitContext:
    """State carried through the evaluate-fit pipeline."""

    request: FitRequest
    services: "PipelineServices"

    resume_text: Optional[str] = None
    resume_source_cache_key: Optional[str] = None
    profile_context: Optional[Any] = None
    parsed_resume: Optional[Any] = None
    analyzed_jd: Optional[Any] = None
    evaluation: Optional[Any] = None

    def usage_source(self):
        return self.services.llm_service


@dataclass
class TailorContext:
    """State carried through the tailoring pipeline."""

    request: TailorRequest
    services: "PipelineServices"

    resume_text: Optional[str] = None
    original_resume_text: Optional[str] = None
    resume_source_cache_key: Optional[str] = None
    profile_context: Optional[Any] = None
    parsed_resume: Optional[Any] = None
    analyzed_jd: Optional[Any] = None
    evaluation: Optional[Any] = None
    strategy_brief: Optional[Any] = None
    strategy_brief_id: Optional[int] = None

    tailored_resume: Optional[str] = None
    current_draft_text: Optional[str] = None
    ats_score: Optional[int] = None
    ats_score_object: Optional[Any] = None
    validation: Optional[Any] = None
    review_bundle: Optional[Any] = None

    tailored_doc_id: Optional[str] = None
    doc_url: Optional[str] = None
    diff_path: Optional[str] = None
    application_id: Optional[int] = None

    approval_required: bool = False
    approval_status: Optional[str] = None
    approval_stage: Optional[str] = None

    def usage_source(self):
        return self.services.llm_service


@dataclass
class PipelineServices:
    """Collaborators the steps need, constructed once per run.

    Bundling them keeps step functions to a single argument and keeps the
    agents out of module-level state, so a test can substitute any one of them.
    """

    llm_service: Any
    google_services: Optional[Any] = None
    confirmed_skills: List[str] = field(default_factory=list)

    _agents: Dict[str, Any] = field(default_factory=dict, repr=False)

    def agent(self, name: str) -> Any:
        """Lazily build and memoise one agent by name.

        Lazy because the evaluate-fit path should not pay to construct the
        tailoring, humanising and scoring agents it never calls.
        """
        if name in self._agents:
            return self._agents[name]

        if name == "resume_parser":
            from ..agents.resume_parser_agent import ResumeParserAgent

            agent = ResumeParserAgent(self.llm_service, confirmed_skills=self.confirmed_skills)
        elif name == "jd_analyzer":
            from ..agents.jd_analyzer_agent import JDAnalyzerAgent

            agent = JDAnalyzerAgent(self.llm_service)
        elif name == "fit_evaluator":
            from ..agents.fit_evaluator_agent import FitEvaluatorAgent

            agent = FitEvaluatorAgent(self.llm_service, confirmed_skills=self.confirmed_skills)
        elif name == "resume_tailor":
            from ..agents.resume_tailor_agent import ResumeTailorAgent

            agent = ResumeTailorAgent(self.llm_service, confirmed_skills=self.confirmed_skills)
        elif name == "humanizer":
            from ..agents.resume_humanizer_agent import ResumeHumanizerAgent

            agent = ResumeHumanizerAgent(self.llm_service)
        elif name == "ats_scorer":
            from ..agents.ats_scorer_agent import ATSScorerAgent

            agent = ATSScorerAgent(self.llm_service)
        elif name == "strategy_brief":
            from ..services.strategy_brief_service import StrategyBriefService

            agent = StrategyBriefService(self.llm_service)
        else:
            raise KeyError(f"Unknown pipeline collaborator '{name}'")

        self._agents[name] = agent
        return agent

    def set_confirmed_skills(self, skills: List[str]) -> None:
        """Update confirmed skills and drop agents built with the old set."""
        if list(skills or []) == self.confirmed_skills:
            return
        self.confirmed_skills = list(skills or [])
        self._agents.clear()
