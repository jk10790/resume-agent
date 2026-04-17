#!/usr/bin/env python3
"""Ask Claude for skill suggestions and improvements. Writes report to scripts/out/; does not modify skills/."""

import json
import sys
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    use_case_path = ROOT / "docs" / "use_case.md"
    if not use_case_path.exists():
        use_case_path = ROOT / "skills" / "README.md"
    if not use_case_path.exists():
        print("No use_case.md or skills/README.md found.", file=sys.stderr)
        return 1

    use_case_text = use_case_path.read_text(encoding="utf-8")

    manifest_path = ROOT / "skills" / "manifest.json"
    existing_skills = []
    if manifest_path.exists():
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            existing_skills = [s.get("id") for s in data.get("skills", []) if s.get("id")]
        except Exception:
            pass

    system = """You are an expert at designing AI agent skills. Given a product use case and optionally a list of existing skill IDs, propose (a) new or refined skills with id, name, short description, and a 1–2 paragraph instruction outline each, and (b) 3–5 concrete improvements (new skills, workflow changes, or prompt tweaks). Output valid JSON only, with keys "proposed_skills" (list of {id, name, description, outline}) and "improvements" (list of strings). Do not modify any files."""

    user = f"""Use case document:\n\n{use_case_text}\n\nExisting skill IDs: {existing_skills}\n\nPropose new or refined skills and 3–5 improvements. Output JSON only."""

    try:
        from resume_agent.services.llm_service import LLMService
        from resume_agent.config import settings
    except ImportError as e:
        print(f"Import error: {e}", file=sys.stderr)
        return 1

    if getattr(settings, "anthropic_api_key", None):
        llm = LLMService(provider_type="anthropic")
    else:
        llm = LLMService()

    from langchain_core.messages import SystemMessage, HumanMessage
    messages = [SystemMessage(content=system), HumanMessage(content=user)]
    try:
        raw = llm.invoke_with_retry(messages)
    except Exception as e:
        print(f"LLM call failed: {e}", file=sys.stderr)
        return 1

    out_dir = ROOT / "scripts" / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"skill_suggestions_{datetime.now().strftime('%Y%m%d_%H%M')}.md"
    report = f"# Skill suggestions\n\nGenerated: {datetime.now().isoformat()}\n\n## Raw response\n\n```\n{raw}\n```\n"
    out_file.write_text(report, encoding="utf-8")
    print(f"Report written to {out_file}")
    print(raw)
    return 0


if __name__ == "__main__":
    sys.exit(main())
