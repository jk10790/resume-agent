"""
Skills loader: reads manifest and per-skill SKILL.md (or variant) files.
Returns system and human_template for use by Path A (templates wrapper) and Path B (tool handlers).
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import PROJECT_ROOT
from ..utils.logger import logger

_instruction_cache: Dict[tuple, Dict[str, str]] = {}
_manifest_cache: Optional[List["SkillDescriptor"]] = None
_manifest_path: Optional[Path] = None


def _resolve_instruction_path(instruction_path: str) -> Path:
    """Resolve instruction_path (relative to project root) to absolute Path."""
    p = Path(instruction_path)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return p


class SkillDescriptor:
    """One skill from the manifest."""

    __slots__ = ("id", "name", "description", "instruction_path", "model_hint", "input_schema")

    def __init__(
        self,
        id: str,
        name: str,
        description: str,
        instruction_path: str,
        model_hint: str = "sonnet",
        input_schema: Optional[Dict[str, Any]] = None,
    ):
        self.id = id
        self.name = name
        self.description = description
        self.instruction_path = instruction_path
        self.model_hint = model_hint
        self.input_schema = input_schema or {}

    def __repr__(self) -> str:
        return f"SkillDescriptor(id={self.id!r}, model_hint={self.model_hint!r})"


def get_manifest() -> List[SkillDescriptor]:
    """Load and return list of skill descriptors from skills/manifest.json."""
    global _manifest_cache, _manifest_path
    path = PROJECT_ROOT / "skills" / "manifest.json"
    if _manifest_cache is not None and _manifest_path == path:
        return _manifest_cache

    if not path.exists():
        logger.warning("Skills manifest not found", path=str(path))
        return []

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load skills manifest", path=str(path), error=str(e))
        return []

    skills = data.get("skills") or []
    result: List[SkillDescriptor] = []
    for s in skills:
        if not isinstance(s, dict) or "id" not in s:
            continue
        desc = SkillDescriptor(
            id=s["id"],
            name=s.get("name", s["id"]),
            description=s.get("description", ""),
            instruction_path=s.get("instruction_path", ""),
            model_hint=s.get("model_hint", "sonnet"),
            input_schema=s.get("input_schema"),
        )
        if desc.instruction_path and not _resolve_instruction_path(desc.instruction_path).exists():
            logger.warning("Skill instruction_path does not exist", skill_id=desc.id, path=desc.instruction_path)
        result.append(desc)

    _manifest_cache = result
    _manifest_path = path
    return result


def _parse_skill_md(content: str) -> Dict[str, str]:
    """Parse SKILL.md: YAML frontmatter (optional) and body. Body can have ## Human template section."""
    out: Dict[str, str] = {"system": "", "human_template": ""}
    frontmatter_match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", content, re.DOTALL)
    if frontmatter_match:
        body = frontmatter_match.group(2).strip()
    else:
        body = content.strip()

    human_marker = "## Human template"
    if human_marker in body:
        parts = body.split(human_marker, 1)
        out["system"] = parts[0].strip()
        out["human_template"] = parts[1].strip()
    else:
        out["system"] = body
        # Default human template for skills that need resume/jd/clarifications
        out["human_template"] = "Job Description:\n---\n{job_description}\n\nResume:\n---\n{resume}\n\nSupplemental Clarifications:\n{clarifications}"
    return out


def load_instruction(skill_id: str, variant: Optional[str] = None) -> Dict[str, str]:
    """
    Load instruction for a skill. Uses manifest to find instruction_path for skill_id.
    variant: optional e.g. 'light' for tailor_resume_light (we use full skill_id in manifest, so variant is only for future use).
    Returns {"system": "...", "human_template": "..."}.
    """
    manifest = get_manifest()
    descriptor = next((d for d in manifest if d.id == skill_id), None)
    if not descriptor or not descriptor.instruction_path:
        return {"system": "", "human_template": ""}

    path = _resolve_instruction_path(descriptor.instruction_path)
    cache_key = (str(path), variant or "")
    if cache_key in _instruction_cache:
        return _instruction_cache[cache_key].copy()

    if not path.exists():
        logger.warning("Skill instruction file not found", path=str(path))
        return {"system": "", "human_template": ""}

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("Failed to read skill instruction", path=str(path), error=str(e))
        return {"system": "", "human_template": ""}

    parsed = _parse_skill_md(text)
    _instruction_cache[cache_key] = parsed.copy()
    return parsed.copy()


def clear_caches() -> None:
    """Clear in-memory caches (for tests or reload)."""
    global _manifest_cache, _manifest_path, _instruction_cache
    _manifest_cache = None
    _manifest_path = None
    _instruction_cache.clear()
