"""The two pipelines this service runs.

Reading these two lists tells you what work happens, in what order, and what each
stage needs -- which is what the previous nine-branch enum did not.
"""

from __future__ import annotations

from ..llm.pipeline import Pipeline, Step
from . import steps

# Fields the caller seeds before the pipeline starts. Declared so the engine can
# reject a mis-ordered pipeline at construction rather than at request time.
_SEEDS = ("request", "services")


def build_fit_pipeline() -> Pipeline:
    """Evaluate one role against the candidate's resume.

    The high-volume path: a discovery sweep runs this once per role. Both
    understanding steps are cached on their own inputs, so across a sweep the
    resume parse is paid once and only the JD analysis and the judgement recur.
    """
    pipeline = Pipeline(
        "evaluate_fit",
        [
            Step("load_profile", steps.load_profile, produces=("profile_context",), optional=True),
            Step("load_resume", steps.load_resume, produces=("resume_text",)),
            Step(
                "understand_inputs",
                steps.understand_inputs,
                requires=("resume_text",),
                produces=("parsed_resume", "analyzed_jd"),
            ),
            Step(
                "evaluate_fit",
                steps.evaluate_fit,
                requires=("parsed_resume", "analyzed_jd"),
                produces=("evaluation",),
            ),
        ],
    )
    pipeline.seeds = _SEEDS
    return pipeline


def build_tailor_pipeline() -> Pipeline:
    """Turn an evaluated role into a tailored resume.

    Everything up to `evaluate_fit` mirrors the fit pipeline and is skipped when
    the caller already has those results -- see `only=` on `Pipeline.run`.
    """
    pipeline = Pipeline(
        "tailor_resume",
        [
            Step("load_profile", steps.load_profile, produces=("profile_context",), optional=True),
            Step("load_resume", steps.load_resume, produces=("resume_text", "original_resume_text")),
            Step(
                "understand_inputs",
                steps.understand_inputs,
                requires=("resume_text",),
                produces=("parsed_resume", "analyzed_jd"),
            ),
            Step(
                "evaluate_fit",
                steps.evaluate_fit,
                requires=("parsed_resume", "analyzed_jd"),
                produces=("evaluation",),
            ),
            Step(
                "build_strategy",
                steps.build_strategy,
                requires=("parsed_resume", "analyzed_jd", "evaluation"),
                produces=("strategy_brief",),
            ),
            Step(
                "tailor_resume",
                steps.tailor_resume,
                requires=("parsed_resume", "analyzed_jd", "evaluation"),
                produces=("tailored_resume",),
            ),
            # Optional: a failure here leaves a usable draft rather than losing it.
            Step("humanize", steps.humanize, requires=("tailored_resume",), optional=True),
            Step("score_ats", steps.score_ats, requires=("tailored_resume",), optional=True),
            Step("validate", steps.validate, requires=("tailored_resume",), produces=("validation",)),
            # Acts on what validate found. Free when the draft is clean.
            Step(
                "enforce_grounding",
                steps.enforce_grounding,
                requires=("tailored_resume", "validation"),
            ),
            # Derived from results already in hand. If it cannot be assembled
            # the draft and its validation still stand, so this never fails a run.
            Step(
                "build_review",
                steps.build_review,
                requires=("tailored_resume", "validation"),
                optional=True,
            ),
        ],
    )
    pipeline.seeds = _SEEDS
    return pipeline


def build_publish_pipeline() -> Pipeline:
    """Persist an approved draft: Drive, diff, tracker.

    Separate from tailoring because it runs after human approval and touches no
    model at all.
    """
    pipeline = Pipeline(
        "publish_resume",
        [
            Step(
                "save_to_google",
                steps.save_to_google,
                requires=("tailored_resume",),
                produces=("tailored_doc_id", "doc_url"),
            ),
            Step("generate_diff", steps.generate_diff, requires=("tailored_resume",), optional=True),
            Step("track_application", steps.track_application, optional=True),
        ],
    )
    pipeline.seeds = _SEEDS + ("tailored_resume",)
    return pipeline


# Step names, so callers can resume a pipeline without repeating string literals.
FIT_STEPS = ("load_profile", "load_resume", "understand_inputs", "evaluate_fit")
TAILOR_ONLY_STEPS = (
    "build_strategy",
    "tailor_resume",
    "humanize",
    "score_ats",
    "validate",
    "enforce_grounding",
    "build_review",
)
