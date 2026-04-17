# Path B: Conversational agent (optional)

When the **Claude Agent SDK** is installed, you can run the resume agent in conversational mode: one parent Claude (Sonnet/Opus) that decides which skills to call (tailor, evaluate fit, extract JD, etc.).

## Requirements

- `pip install claude-agent-sdk`
- In `.env`: **`LLM_PROVIDER=anthropic`** and **`ANTHROPIC_API_KEY=<your_key>`**

## Run

```bash
python scripts/run_resume_agent.py "Tailor my resume for this job: [paste job description]"
# or
python scripts/run_resume_agent.py
# then type your request
```

Skills are exposed as tools; the parent chooses which to invoke. Each skill runs with the model from `model_hint` in the manifest (e.g. Haiku for extract_jd, Sonnet for tailor).

## Bootstrap scripts

- **Suggest skills:** `python scripts/suggest_skills.py` – writes a report to `scripts/out/skill_suggestions_*.md` (no file changes).
- **Scaffold a skill:** `python scripts/scaffold_skill.py --id my_skill --name "My Skill" --description "When to use."` – creates `skills/my_skill/SKILL.md` stub; you add the manifest entry.
