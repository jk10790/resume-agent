"""A small pipeline engine: ordered steps over a typed context.

The workflow this replaces was a single method branching on a `WorkflowStep`
enum, with every branch mutating one thirty-field result object. Because no
branch could rely on an earlier one having run, each re-derived its own inputs
("if not parsed_resume: parse now (late)"), and that defensive re-derivation was
where duplicate LLM calls came from.

Here a pipeline declares its steps once, in order. A step names what it needs
and what it produces, and the engine refuses to run one whose inputs are not
present -- so a step never re-derives, and adding a step cannot silently
introduce a second parse of the same document.

Steps are plain callables over a context object, so they stay unit-testable
without a running workflow.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Generic, Iterable, List, Optional, Sequence, TypeVar

from ..utils.logger import logger

Context = TypeVar("Context")

# A step returns a mapping of context field -> value, or None when it only has
# side effects. Returning values (rather than mutating) is what lets the engine
# verify that a step produced what it declared.
StepResult = Optional[Dict[str, Any]]


class PipelineError(RuntimeError):
    """A pipeline could not be completed."""

    def __init__(self, message: str, *, step: str = "", code: str = "failed"):
        super().__init__(message)
        self.step = step
        self.code = code


@dataclass(frozen=True)
class Step(Generic[Context]):
    """One unit of work in a pipeline."""

    name: str
    run: Callable[[Context], StepResult]
    requires: Sequence[str] = ()
    produces: Sequence[str] = ()
    # A step that may fail without failing the run. Used for enrichment whose
    # absence degrades the result rather than invalidating it.
    optional: bool = False

    def missing_inputs(self, context: Context) -> List[str]:
        return [name for name in self.requires if getattr(context, name, None) in (None, "", [], {})]


@dataclass
class StepRecord:
    """What one step did, for cost reporting and debugging."""

    name: str
    status: str  # completed | skipped | failed
    duration_ms: int = 0
    error: Optional[str] = None
    usage: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineRun:
    """The outcome of running a pipeline."""

    steps: List[StepRecord] = field(default_factory=list)
    usage: Dict[str, Any] = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        return any(record.status == "failed" for record in self.steps)

    @property
    def cost_usd(self) -> float:
        return float(self.usage.get("cost_usd") or 0.0)

    def record_for(self, name: str) -> Optional[StepRecord]:
        return next((record for record in self.steps if record.name == name), None)


def _usage_delta(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    """Difference between two usage snapshots, ignoring non-numeric fields.

    A usage source that reports nothing usable costs the run nothing: cost
    reporting is an observation of the run, never a reason to fail it.
    """
    if not isinstance(after, dict):
        return {}
    if not isinstance(before, dict):
        before = {}
    delta: Dict[str, Any] = {}
    for key, value in (after or {}).items():
        if key == "by_model":
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        previous = (before or {}).get(key)
        previous = previous if isinstance(previous, (int, float)) and not isinstance(previous, bool) else 0
        if value - previous:
            delta[key] = value - previous

    before_models = (before or {}).get("by_model") or {}
    after_models = (after or {}).get("by_model") or {}
    if isinstance(after_models, dict):
        models = {}
        for model, count in after_models.items():
            previous = before_models.get(model, 0) if isinstance(before_models, dict) else 0
            if isinstance(count, (int, float)) and count - previous:
                models[model] = count - previous
        if models:
            delta["by_model"] = models
    return delta


def _merge_usage(total: Dict[str, Any], delta: Dict[str, Any]) -> None:
    for key, value in delta.items():
        if key == "by_model":
            models = dict(total.get("by_model") or {})
            for model, count in value.items():
                models[model] = models.get(model, 0) + count
            total["by_model"] = models
        else:
            total[key] = (total.get(key) or 0) + value


class Pipeline(Generic[Context]):
    """An ordered, named sequence of steps."""

    def __init__(self, name: str, steps: Sequence[Step[Context]]):
        self.name = name
        self.steps: List[Step[Context]] = list(steps)
        self._assert_inputs_are_producible()

    def _assert_inputs_are_producible(self) -> None:
        """Fail at construction if a step needs something no earlier step makes.

        This is the guard that keeps ordering honest: a mis-ordered pipeline is a
        startup error, not a confusing empty prompt at request time. Fields the
        caller seeds are declared by `seeds`.
        """
        available = set(getattr(self, "seeds", ()) or ())
        for step in self.steps:
            unmet = [name for name in step.requires if name not in available]
            if unmet and not available:
                # No seed declaration: cannot verify, and guessing would produce
                # false alarms. Ordering is still enforced at run time.
                return
            if unmet:
                raise PipelineError(
                    f"Pipeline '{self.name}': step '{step.name}' requires "
                    f"{', '.join(unmet)}, which no earlier step produces",
                    step=step.name,
                )
            available.update(step.produces)

    def run(
        self,
        context: Context,
        *,
        usage_source: Any = None,
        only: Optional[Iterable[str]] = None,
        progress: Optional[Callable[[str], None]] = None,
    ) -> PipelineRun:
        """Execute the steps in order against `context`.

        `usage_source` is anything exposing `usage_snapshot()` (an LLMService), so
        each step's token and cost usage is attributed to that step rather than
        summed anonymously for the whole run.
        """
        run = PipelineRun()
        selected = set(only) if only is not None else None

        for step in self.steps:
            if selected is not None and step.name not in selected:
                run.steps.append(StepRecord(name=step.name, status="skipped"))
                continue

            missing = step.missing_inputs(context)
            if missing:
                message = (
                    f"Step '{step.name}' cannot run: {', '.join(missing)} not available"
                )
                if step.optional:
                    logger.info("Pipeline step skipped", pipeline=self.name, step=step.name, missing=missing)
                    run.steps.append(StepRecord(name=step.name, status="skipped", error=message))
                    continue
                run.steps.append(StepRecord(name=step.name, status="failed", error=message))
                raise PipelineError(message, step=step.name, code="missing_input")

            if progress:
                progress(step.name)

            snapshot = getattr(usage_source, "usage_snapshot", None)
            before = snapshot() if callable(snapshot) else {}
            started = time.monotonic()
            try:
                produced = step.run(context) or {}
            except Exception as e:
                duration = int((time.monotonic() - started) * 1000)
                after = snapshot() if callable(snapshot) else {}
                delta = _usage_delta(before, after)
                _merge_usage(run.usage, delta)
                run.steps.append(
                    StepRecord(name=step.name, status="failed", duration_ms=duration, error=str(e), usage=delta)
                )
                logger.error(f"Pipeline step failed: {step.name}", pipeline=self.name, error=str(e), exc_info=True)
                if step.optional:
                    continue
                if isinstance(e, PipelineError):
                    raise
                # Carry a cause's own error code, so a caller can still map
                # "no Google session" onto a 401 rather than a blanket 500.
                raise PipelineError(
                    str(e), step=step.name, code=str(getattr(e, "code", "") or "failed")
                ) from e

            duration = int((time.monotonic() - started) * 1000)
            after = snapshot() if callable(snapshot) else {}
            delta = _usage_delta(before, after)
            _merge_usage(run.usage, delta)

            for key, value in produced.items():
                setattr(context, key, value)

            unproduced = [name for name in step.produces if getattr(context, name, None) is None]
            if unproduced and not step.optional:
                message = f"Step '{step.name}' declared {', '.join(unproduced)} but produced nothing"
                run.steps.append(StepRecord(name=step.name, status="failed", duration_ms=duration, error=message, usage=delta))
                raise PipelineError(message, step=step.name)

            run.steps.append(StepRecord(name=step.name, status="completed", duration_ms=duration, usage=delta))
            logger.info(
                "Pipeline step complete",
                pipeline=self.name,
                step=step.name,
                duration_ms=duration,
                cost_usd=delta.get("cost_usd"),
            )

        return run
