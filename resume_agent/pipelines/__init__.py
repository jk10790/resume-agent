"""Declarative pipelines for evaluating a role and tailoring a resume for it."""

from .approval import ApprovedDraft
from .context import FitContext, FitRequest, PipelineServices, TailorContext, TailorRequest
from .definitions import build_fit_pipeline, build_publish_pipeline, build_tailor_pipeline
from .serialization import serialize_evaluation
from .orchestrator import FitOutcome, ResumeOrchestrator, TailorOutcome

__all__ = [
    "serialize_evaluation",
    "ApprovedDraft",
    "FitContext",
    "FitRequest",
    "TailorContext",
    "TailorRequest",
    "PipelineServices",
    "build_fit_pipeline",
    "build_tailor_pipeline",
    "build_publish_pipeline",
    "ResumeOrchestrator",
    "FitOutcome",
    "TailorOutcome",
]
