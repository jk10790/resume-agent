"""The task registry: one declared home for every LLM call this service makes.

Each task is one Markdown file in `resume_agent/prompts/library/`, whose
frontmatter declares the things that used to be scattered:

- `tier` -- which model band runs it. This used to be nothing at all: the
  provider guessed from prompt length, which put whole-resume rewrites on the
  cheap tier, and the later fix spread model choice across twenty call sites.
- `cache` -- whether a response may be reused for an identical prompt.
- `output` -- `json` when the caller parses structured output.

The prompt text lives in the same file. Prompts previously lived in four places
(`skills/*.md`, `prompts/templates.py`, `prompts/tailoring_intensity.py`, and
inline strings in fourteen agent modules), which is how one prompt came to
demand a quantified metric while its twin forbade inventing one. A single home
makes that class of contradiction unreachable.

Adding a task means adding a file. Nothing here needs editing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from ..utils.logger import logger

# Cost/capability bands. These are the keys the provider maps onto real models,
# so they are deliberately about the work, not about any vendor's model names.
TIERS = ("simple", "standard", "complex")

# Older skill files declared a model family instead of a band.
_MODEL_HINT_TO_TIER = {
    "haiku": "simple",
    "sonnet": "standard",
    "opus": "complex",
}

_LIBRARY_DIR = Path(__file__).resolve().parent.parent / "prompts" / "library"

_HUMAN_MARKER = "## Human template"
_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)", re.DOTALL)


class TaskError(RuntimeError):
    """A task is missing, malformed, or misdeclared."""


@dataclass(frozen=True)
class LLMTask:
    """One declared unit of LLM work."""

    id: str
    tier: str
    system: str
    human_template: str
    description: str = ""
    output: str = "text"
    cache: bool = True
    mcp_exposed: bool = False
    input_schema: Dict[str, Any] = field(default_factory=dict)

    @property
    def expects_json(self) -> bool:
        return self.output == "json"

    def render(self, **variables: Any) -> List[Any]:
        """Build the message list for this task.

        Missing variables are an error rather than a silently empty section: a
        prompt that quietly loses its resume still returns plausible text, which
        is the worst possible failure mode here.
        """
        from langchain_core.messages import HumanMessage, SystemMessage

        try:
            # Both halves are formatted, so a literal brace in either -- the JSON
            # shape these prompts ask for is full of them -- is escaped the same
            # way, as `{{`.
            system = self.system.format(**variables)
            human = self.human_template.format(**variables)
        except KeyError as e:
            raise TaskError(
                f"Task '{self.id}' needs template variable {e} and it was not supplied"
            ) from e

        messages: List[Any] = [SystemMessage(content=system)]
        if human.strip():
            messages.append(HumanMessage(content=human))
        return messages


def _parse_frontmatter(raw: str) -> Dict[str, Any]:
    """Minimal YAML-subset parse: `key: value` pairs and `key: [a, b]` lists.

    Deliberately not a YAML dependency -- these files hold flat scalars, and a
    parser that accepts more than the format allows invites the frontmatter to
    grow into configuration that belongs in code.
    """
    data: Dict[str, Any] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if value.startswith("[") and value.endswith("]"):
            data[key] = [v.strip().strip('"').strip("'") for v in value[1:-1].split(",") if v.strip()]
        elif value.lower() in ("true", "false"):
            data[key] = value.lower() == "true"
        else:
            data[key] = value
    return data


def _parse_task_file(path: Path) -> LLMTask:
    text = path.read_text(encoding="utf-8")
    match = _FRONTMATTER.match(text)
    if not match:
        raise TaskError(f"{path.name} has no frontmatter; every task must declare its tier")

    meta = _parse_frontmatter(match.group(1))
    body = match.group(2).strip()

    if _HUMAN_MARKER in body:
        system, _, human = body.partition(_HUMAN_MARKER)
    else:
        system, human = body, ""

    task_id = meta.get("id") or path.stem
    tier = meta.get("tier") or _MODEL_HINT_TO_TIER.get(str(meta.get("model_hint", "")).lower(), "")
    if not tier:
        raise TaskError(f"Task '{task_id}' does not declare a tier (one of {', '.join(TIERS)})")
    if tier not in TIERS:
        raise TaskError(f"Task '{task_id}' declares unknown tier '{tier}'; expected one of {', '.join(TIERS)}")

    return LLMTask(
        id=task_id,
        tier=tier,
        system=system.strip(),
        human_template=human.strip(),
        description=str(meta.get("description", "")),
        output=str(meta.get("output", "text")),
        cache=bool(meta.get("cache", True)),
        mcp_exposed=bool(meta.get("mcp_exposed", False)),
    )


class TaskRegistry:
    """Every task the service can run, loaded once from the prompt library."""

    def __init__(self, library_dir: Optional[Path] = None):
        self.library_dir = Path(library_dir) if library_dir else _LIBRARY_DIR
        self._tasks: Optional[Dict[str, LLMTask]] = None

    def _load(self) -> Dict[str, LLMTask]:
        if self._tasks is not None:
            return self._tasks
        tasks: Dict[str, LLMTask] = {}
        if not self.library_dir.is_dir():
            logger.warning("Prompt library directory missing", path=str(self.library_dir))
            self._tasks = tasks
            return tasks
        for path in sorted(self.library_dir.glob("*.md")):
            task = _parse_task_file(path)
            if task.id in tasks:
                raise TaskError(f"Duplicate task id '{task.id}' in {path.name}")
            tasks[task.id] = task
        self._tasks = tasks
        logger.info("Loaded prompt library", task_count=len(tasks))
        return tasks

    def get(self, task_id: str) -> LLMTask:
        tasks = self._load()
        if task_id not in tasks:
            raise TaskError(
                f"Unknown task '{task_id}'. Known tasks: {', '.join(sorted(tasks)) or '(none)'}"
            )
        return tasks[task_id]

    def __iter__(self) -> Iterator[LLMTask]:
        return iter(self._load().values())

    def __len__(self) -> int:
        return len(self._load())

    def __contains__(self, task_id: object) -> bool:
        return task_id in self._load()

    def by_tier(self, tier: str) -> List[LLMTask]:
        return [task for task in self if task.tier == tier]

    def reload(self) -> None:
        self._tasks = None


_registry = TaskRegistry()


def get_registry() -> TaskRegistry:
    return _registry


def get_task(task_id: str) -> LLMTask:
    return _registry.get(task_id)
