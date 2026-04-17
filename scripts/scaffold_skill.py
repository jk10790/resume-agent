#!/usr/bin/env python3
"""Create a new skill dir and stub SKILL.md. Usage: --id <id> --name \"Name\" --description \"When to use.\" [or --from-suggestion <file> --index N]"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT / "skills"


def main() -> int:
    ap = argparse.ArgumentParser(description="Scaffold a new resume-agent skill")
    ap.add_argument("--id", help="Skill id (snake_case, e.g. my_skill)")
    ap.add_argument("--name", help="Display name")
    ap.add_argument("--description", help="Short description (when to use)")
    ap.add_argument("--from-suggestion", dest="suggestion_file", help="Path to suggestion report MD file")
    ap.add_argument("--index", type=int, default=0, help="Index of proposed skill in suggestion file (default 0)")
    args = ap.parse_args()

    if args.suggestion_file:
        path = Path(args.suggestion_file)
        if not path.exists():
            print(f"File not found: {path}", file=sys.stderr)
            return 1
        text = path.read_text(encoding="utf-8")
        # Heuristic: find JSON block or "id": "..." in the raw response
        skills_match = re.search(r'"proposed_skills"\s*:\s*\[(.*?)\]', text, re.DOTALL)
        if not skills_match:
            print("No proposed_skills array found in suggestion file.", file=sys.stderr)
            return 1
        # Parse first few entries; get index-th
        try:
            # Reconstruct minimal JSON
            raw = "{" + skills_match.group(0)
            if not raw.endswith("}"):
                raw += "}"
            data = json.loads(raw)
            skills = data.get("proposed_skills", [])
            if args.index >= len(skills):
                print(f"Index {args.index} out of range (found {len(skills)} skills).", file=sys.stderr)
                return 1
            s = skills[args.index]
            skill_id = s.get("id", "new_skill").replace(" ", "_").lower()
            name = s.get("name", skill_id)
            description = s.get("description", "TODO: describe when to use this skill.")
        except Exception as e:
            print(f"Could not parse suggestion file: {e}", file=sys.stderr)
            return 1
    else:
        skill_id = (args.id or "").strip().replace(" ", "_").lower()
        name = (args.name or skill_id or "New Skill").strip()
        description = (args.description or "TODO: describe when to use this skill.").strip()
        if not skill_id:
            print("Provide --id or --from-suggestion.", file=sys.stderr)
            return 1

    skill_dir = SKILLS_DIR / skill_id
    if skill_dir.exists():
        print(f"Skill directory already exists: {skill_dir}", file=sys.stderr)
        return 1

    skill_dir.mkdir(parents=True)
    skill_md = skill_dir / "SKILL.md"
    content = f"""---
name: {skill_id}
description: {description}
model_hint: sonnet
---

# {name}

TODO: add instruction body (system prompt and optional ## Human template section with placeholders like {{resume}}, {{job_description}}).
"""
    skill_md.write_text(content, encoding="utf-8")
    print(f"Created {skill_dir}")
    print(f"  {skill_md}")
    print("Add this to skills/manifest.json:")
    print(json.dumps({
        "id": skill_id,
        "name": name,
        "description": description,
        "instruction_path": f"skills/{skill_id}/SKILL.md",
        "model_hint": "sonnet",
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
