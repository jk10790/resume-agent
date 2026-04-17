# Skills (file-backed prompts)

Prompts live under `skills/`: one **manifest** and one **SKILL.md** (or variant) per skill. The loader is the single source of truth; Path A (API/CLI) and Path B (agent) both use it.

## Layout

- `skills/manifest.json` – List of skills: `id`, `name`, `description`, `instruction_path`, `model_hint` (`haiku` | `sonnet` | `opus`).
- `skills/<id>/SKILL.md` – Per-skill instruction: optional YAML frontmatter (`name`, `description`, `model_hint`) and body. Body can include a `## Human template` section with placeholders like `{resume}`, `{job_description}`, `{clarifications}`.

## Adding a skill

1. Create `skills/<skill_id>/SKILL.md` (e.g. `skills/my_skill/SKILL.md`).
2. Add frontmatter and system instruction; add `## Human template` with `{placeholders}` if the skill needs inputs.
3. Append an entry to `skills/manifest.json` with `instruction_path`: `skills/<skill_id>/SKILL.md`.

Or run: `python scripts/scaffold_skill.py --id my_skill --name "My Skill" --description "When to use it."` and add the printed JSON to the manifest.

## API

- `resume_agent.skills.loader.get_manifest()` → list of `SkillDescriptor`.
- `resume_agent.skills.loader.load_instruction(skill_id)` → `{"system": "...", "human_template": "..."}`.
