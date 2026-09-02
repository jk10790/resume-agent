"""The task registry: one declared home for every prompt, tier and output shape.

These tests are the guard that the mis-tiering and prompt-drift defects cannot
come back. They assert on the whole library rather than on individual tasks, so
a new prompt file is covered the moment it is added.
"""

from __future__ import annotations

import pytest

from resume_agent.llm.tasks import TIERS, LLMTask, TaskError, TaskRegistry, get_registry


def test_the_library_loads():
    registry = get_registry()
    assert len(registry) > 0
    assert "fit.evaluate" in registry
    assert "tailor.medium" in registry


def test_every_task_declares_a_known_tier():
    """Tier is the cost decision. A task without one would fall back to a
    length heuristic, which is what put whole-resume rewrites on the cheap tier."""
    for task in get_registry():
        assert task.tier in TIERS, f"{task.id} declares tier {task.tier!r}"


def test_work_that_writes_the_resume_runs_on_the_expensive_tier():
    """The output the user actually receives must not be produced cheaply."""
    registry = get_registry()
    for task_id in (
        "tailor.light",
        "tailor.medium",
        "tailor.heavy",
        "tailor.refine_entry",
        "resume.humanize",
        "quality.improve",
        "fit.evaluate",
    ):
        assert registry.get(task_id).tier == "complex", f"{task_id} must run on the complex tier"


def test_mechanical_extraction_runs_on_the_cheap_tier():
    """Extraction is transcription, not judgement; paying model rates for it is waste."""
    registry = get_registry()
    for task_id in ("resume.parse", "jd.analyze", "ats.score", "jd.extract", "skills.extract"):
        assert registry.get(task_id).tier == "simple", f"{task_id} should run on the simple tier"


def test_generative_tasks_do_not_reuse_a_cached_response():
    """Otherwise 'regenerate' returns the identical draft forever."""
    registry = get_registry()
    for task_id in ("tailor.light", "tailor.medium", "tailor.heavy", "resume.humanize", "quality.improve"):
        assert registry.get(task_id).cache is False, f"{task_id} must not serve a cached draft"


def test_no_prompt_asks_for_a_metric_it_cannot_verify():
    """The defect this whole library exists to make unreachable.

    A prompt that shows a worked example containing an invented figure teaches
    the model to invent one, whatever the surrounding prohibition says.
    """
    banned = ("by 40%", "by 35%", "from 3s to 1.8s", "15 microservices", "by X%", "Xms to Yms")
    for task in get_registry():
        body = f"{task.system}\n{task.human_template}"
        for phrase in banned:
            assert phrase not in body, f"{task.id} contains the fabricated-metric template {phrase!r}"


def test_json_tasks_escape_their_literal_braces():
    """A task declaring JSON output renders its schema, not a KeyError."""
    for task in get_registry():
        if not task.expects_json:
            continue
        placeholders = _placeholders(task)
        rendered = task.render(**{name: "x" for name in placeholders})
        assert rendered[0].content, f"{task.id} rendered an empty system prompt"


def test_a_missing_template_variable_is_an_error_not_a_silent_gap(tmp_path):
    """A prompt that quietly loses its resume still returns plausible text."""
    task = LLMTask(
        id="demo",
        tier="simple",
        system="System",
        human_template="Resume: {resume_text}",
    )

    with pytest.raises(TaskError) as excinfo:
        task.render()

    assert "resume_text" in str(excinfo.value)


def test_a_task_without_a_tier_is_rejected(tmp_path):
    (tmp_path / "bad.md").write_text("---\nid: bad\ndescription: no tier\n---\nBody")
    registry = TaskRegistry(library_dir=tmp_path)

    with pytest.raises(TaskError) as excinfo:
        registry.get("bad")

    assert "tier" in str(excinfo.value)


def test_a_task_with_an_unknown_tier_is_rejected(tmp_path):
    (tmp_path / "bad.md").write_text("---\nid: bad\ntier: cheapest\n---\nBody")
    registry = TaskRegistry(library_dir=tmp_path)

    with pytest.raises(TaskError) as excinfo:
        registry.get("bad")

    assert "cheapest" in str(excinfo.value)


def test_a_model_hint_still_maps_onto_a_tier(tmp_path):
    """Older skill files declared a model family instead of a band."""
    (tmp_path / "legacy.md").write_text("---\nid: legacy\nmodel_hint: haiku\n---\nBody")
    registry = TaskRegistry(library_dir=tmp_path)

    assert registry.get("legacy").tier == "simple"


def _placeholders(task: LLMTask) -> set:
    import re

    pattern = re.compile(r"(?<!\{)\{([a-zA-Z_][a-zA-Z0-9_]*)\}(?!\})")
    return set(pattern.findall(task.system)) | set(pattern.findall(task.human_template))
