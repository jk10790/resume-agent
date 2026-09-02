#!/usr/bin/env python3
"""Create a stub task file in the prompt library.

Usage: scaffold_skill.py --id <id> --tier simple|standard|complex --description "When to use."

A task is one Markdown file; there is no manifest to update afterwards. See
docs/SKILLS.md.
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIBRARY = ROOT / "resume_agent" / "prompts" / "library"

TEMPLATE = """---
id: {task_id}
tier: {tier}
description: {description}
---
You are a ...

State the task in the imperative. Where the prompt shows a JSON shape, double
every literal brace ({{{{ and }}}}) -- the body is formatted with the caller's
variables.

Never illustrate a suggestion with an invented number: a worked example
containing a figure teaches the model to produce one.

## Human template
{{input_text}}
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--id", required=True, help="Task id, e.g. resume.summarize")
    parser.add_argument(
        "--tier",
        required=True,
        choices=("simple", "standard", "complex"),
        help="simple = mechanical extraction; complex = writing or judgement",
    )
    parser.add_argument("--description", required=True, help="One line: when to use it")
    args = parser.parse_args()

    if not re.fullmatch(r"[a-z0-9_]+(\.[a-z0-9_]+)*", args.id):
        print(f"Invalid task id {args.id!r}: use lowercase words separated by dots", file=sys.stderr)
        return 1

    path = LIBRARY / f"{args.id}.md"
    if path.exists():
        print(f"{path} already exists", file=sys.stderr)
        return 1

    LIBRARY.mkdir(parents=True, exist_ok=True)
    path.write_text(
        TEMPLATE.format(task_id=args.id, tier=args.tier, description=args.description)
    )
    print(f"Created {path.relative_to(ROOT)}")
    print(f'Call it with: llm_service.run_task("{args.id}", input_text=...)')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
