"""LLM task registry and pipeline engine.

`tasks` holds the single declared home for every prompt, tier and output shape.
`pipeline` runs ordered steps over a typed context and attributes cost per step.
"""

from .pipeline import Pipeline, PipelineError, PipelineRun, Step, StepRecord
from .tasks import LLMTask, TaskError, TaskRegistry, get_registry, get_task

__all__ = [
    "LLMTask",
    "TaskError",
    "TaskRegistry",
    "get_registry",
    "get_task",
    "Pipeline",
    "PipelineError",
    "PipelineRun",
    "Step",
    "StepRecord",
]
