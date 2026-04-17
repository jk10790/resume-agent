# Bootstrap: first-time setup

Get the project running from a clean clone.

## 1. Clone and install

```bash
git clone <repo-url>
cd resume-agent
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
pip install -e .
```

## 2. Environment

Create a `.env` file in the project root. Minimum for CLI/API – **pick one provider**:

```env
# Groq
LLM_PROVIDER=groq
GROQ_API_KEY=<your_key>
```

```env
# Anthropic (Claude)
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=<your_key>
```

```env
# OpenAI
LLM_PROVIDER=openai
OPENAI_API_KEY=<your_key>
```

```env
# Ollama (local)
LLM_PROVIDER=ollama
OLLAMA_MODEL=llama2
```

Valid `LLM_PROVIDER` values: `groq`, `anthropic`, `openai`, `ollama`. More options in [README § LLM configuration](../README.md#llm-configuration).

**Web UI (optional):** Add Google OAuth and session secret – see [README § Google API](../README.md#google-api-web-ui--recommended).

## 3. Validate

```bash
python validate_app.py
```

Fixes any reported issues (e.g. missing API key, wrong provider name) then re-run until checks pass.

## 4. Skills (already in repo)

The repo ships with `skills/manifest.json` and SKILL.md files for extract_jd, tailor_resume, evaluate_fit, improve_quality. No bootstrap needed to use them.

**To add or refine skills later:**

- `python scripts/suggest_skills.py` – writes suggestions to `scripts/out/` (does not change files).
- `python scripts/scaffold_skill.py --id <id> --name "Name" --description "When to use."` – creates a new skill stub; you add its entry to `skills/manifest.json`.

See [docs/SKILLS.md](SKILLS.md).

## 5. Path B – conversational agent (optional)

If you want the conversational agent (Claude decides which skills to call):

```bash
pip install claude-agent-sdk
```

Set `ANTHROPIC_API_KEY` in `.env`, then:

```bash
python scripts/run_resume_agent.py "Tailor my resume for this job: ..."
```

See [docs/AGENT.md](AGENT.md).

## 6. Run the app

- **Web UI:** `make ui` → open http://localhost:3000  
- **CLI:** `python main.py evaluate --url <job_url>` or `python main.py tailor --url <job_url> --company "Co" --title "Role"`

**Next:** [README](../README.md) for full usage and configuration.
