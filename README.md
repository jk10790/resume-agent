# Resume Agent

An AI-powered resume tailoring and job application assistant that helps you:

- **Tailor your resume** to match specific job descriptions
- **Evaluate job fit** to determine if you're a good match
- **Extract job descriptions** from job listing URLs
- **Track applications** and manage your job search

## Features

- **Resume tailoring** – AI customizes your resume from job descriptions while keeping content honest
- **Job fit evaluation** – Fit score (1–10) and recommendations
- **Job description extraction** – Scrape and clean JDs from URLs
- **Google Docs integration** – Read/write resumes via Google Docs (OAuth or file-based auth)
- **Application tracking** – SQLite-backed application tracker
- **Change logging** – Markdown diffs of resume changes
- **Multiple LLM providers** – Ollama, Groq, OpenAI, Anthropic (Claude)
- **Skills** – File-backed prompts in `skills/` ([docs/SKILLS.md](docs/SKILLS.md))
- **Path B (optional)** – Conversational agent ([docs/AGENT.md](docs/AGENT.md))
- **Prompt learning** – Improve prompts from user feedback (see [Prompt learning](#prompt-learning) below)

## Quick Start

**First time?** See [Bootstrap](docs/BOOTSTRAP.md) for step-by-step setup.

### Prerequisites

- Python 3.8+
- An LLM provider: **Ollama**, **Groq**, **OpenAI**, or **Anthropic**
- Google account (Drive + Docs) for resume storage
- Node.js for the React frontend (if using Web UI)

### Installation

```bash
git clone <repo-url>
cd resume-agent
python3 -m venv venv   # or: python3 -m venv .venv
source venv/bin/activate   # or: source .venv/bin/activate   (Windows: venv\Scripts\activate)
pip install -r requirements.txt
pip install -e .
```

On macOS with an externally-managed Python:

```bash
pip install --break-system-packages -r requirements.txt
```

### LLM configuration

Pick one provider and add to `.env`:

**Groq (recommended)**  
`LLM_PROVIDER=groq`  
`GROQ_API_KEY=<your_key>`  
`GROQ_MODEL=llama-3.3-70b-versatile`

**Ollama (local)**  
`LLM_PROVIDER=ollama`  
`OLLAMA_MODEL=llama2`  
Run: `ollama serve` and `ollama pull llama2`

**OpenAI**  
`LLM_PROVIDER=openai`  
`OPENAI_API_KEY=<your_key>`  
`OPENAI_MODEL=gpt-4o-mini`

**Anthropic (Claude)**  
`LLM_PROVIDER=anthropic`  
`ANTHROPIC_API_KEY=<your_key>`  
`ANTHROPIC_MODEL=claude-sonnet-4-20250514`

**Model choice**  
Yes – a more capable model generally follows instructions more reliably (e.g. “replace this word”, “break these sentences”) and can improve resume quality and tailoring. You’re already on Groq’s strongest option (`llama-3.3-70b-versatile`). For even more consistent instruction-following you can switch to OpenAI:

- **OpenAI `gpt-4o`** – Best instruction-following and nuance; higher cost.  
  In `.env`: `LLM_PROVIDER=openai`, `OPENAI_API_KEY=<key>`, `OPENAI_MODEL=gpt-4o`
- **OpenAI `gpt-4o-mini`** – Good balance of cost and quality (default if you only set `LLM_PROVIDER=openai`).

So: **better LLM → better understanding and more reliable edits**; the app supports Groq (current) and OpenAI so you can switch via `.env` without code changes.

### Google API (Web UI – recommended)

1. In [Google Cloud Console](https://console.cloud.google.com/), create a project and enable **Drive API** and **Docs API**.
2. Create **OAuth 2.0** credentials (Web application).
3. Add redirect URI: `http://localhost:8000/api/auth/google/callback`.
4. In **OAuth consent screen**, add scopes: `drive`, `documents`, `userinfo.email`, `userinfo.profile`. Add test users if the app is in Testing mode.
5. In `.env`:
   ```env
   GOOGLE_OAUTH_CLIENT_ID=your_client_id.apps.googleusercontent.com
   GOOGLE_OAUTH_CLIENT_SECRET=your_client_secret
   SESSION_SECRET_KEY=your-random-secret-key
   ```

No `credentials.json` or manual doc IDs needed for the Web UI.

**Legacy (CLI / file-based):** Use a Desktop OAuth client, download `credentials.json` to the project root, set `GOOGLE_FOLDER_ID` and `RESUME_DOC_ID` in `.env`, and run `python -m resume_agent.storage.google_auth` to authenticate.

### Validate setup

```bash
python validate_app.py
```

## Usage

### Web UI (recommended)

Start backend and React frontend together:

```bash
make ui
```

Then open **http://localhost:3000**.  
To run them separately: `make api` (port 8000) and `make frontend` (port 3000).

**Alternative UIs:**  
- Streamlit: `make ui-streamlit` (legacy).  
- CLI only: see below.

### Command line

```bash
# Evaluate fit for a job
python main.py evaluate --url <job_listing_url>

# Tailor resume and optionally track
python main.py tailor --url <job_listing_url> --company "Company" --title "Job Title" --track

# Full flow: evaluate + tailor + track
python main.py apply --url <job_listing_url> --company "Company" --title "Job Title"

# List / search / stats
python main.py list
python main.py stats
python main.py search "company name"
```

## Configuration

### Environment variables

**LLM**  
- `LLM_PROVIDER` – `ollama` | `groq` | `openai` | `anthropic`  
- Groq: `GROQ_API_KEY`, `GROQ_MODEL`, `GROQ_TEMPERATURE`, `GROQ_TOP_P`, `GROQ_MAX_TOKENS`  
- Ollama: `OLLAMA_MODEL`  
- OpenAI: `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_TEMPERATURE`, `OPENAI_TOP_P`, `OPENAI_MAX_TOKENS`  
- Anthropic: `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `ANTHROPIC_TEMPERATURE`, `ANTHROPIC_MAX_TOKENS`

**Google (optional for Web UI)**  
- `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`  
- `GOOGLE_OAUTH_REDIRECT_URI` (default: `http://localhost:8000/api/auth/google/callback`)  
- `SESSION_SECRET_KEY`  
- Legacy: `GOOGLE_FOLDER_ID`, `RESUME_DOC_ID`

**Chrome extension**  
- To use the [Chrome extension](chrome_extension/README.md) (evaluate fit from any job page), add your extension origin to CORS:  
  `API_CORS_ORIGINS=http://localhost:3000,chrome-extension://YOUR_EXTENSION_ID`  
  (Get the ID from `chrome://extensions` after loading the unpacked extension.)

**Other**  
- `APPLICATION_DB_PATH`, `MEMORY_FILE`, `LOG_FILE`  
- `LLM_CACHE_SIZE`, `LLM_MAX_RETRIES`, `LLM_RETRY_DELAY`  
- `JD_EXTRACTION_TIMEOUT`, `JD_EXTRACTION_MAX_RETRIES`, `JD_TEXT_LIMIT`  
- `GOOGLE_API_TIMEOUT`

## Testing

```bash
pytest tests/ -v
```

**Test layout**

- **Unit / API:** `test_backend_api_integration.py`, `test_workflow_integration.py`, `test_cache_integration.py`, `test_llm_service.py`, `test_memory.py`, etc.
- **E2E (real APIs):** `test_integration_e2e.py` – needs `GROQ_API_KEY` and optionally Google credentials. Run with:  
  `INTEGRATION_TESTS=true pytest tests/test_integration_e2e.py -v -s`
- **Streamlit UI:** `test_ui_integration.py` – run with `UI_TESTS=true pytest tests/test_ui_integration.py -v` (requires Playwright: `pip install playwright && playwright install chromium`).
- **React UI (Playwright):** `test_frontend_playwright.py` – start backend and frontend (`make api`, `make frontend`), then:  
  `UI_TESTS=true pytest tests/test_frontend_playwright.py -v`

**Make targets**

- `make test` – all tests  
- `make test-integration` – E2E with real APIs  
- `make test-ui` – Streamlit UI tests  
- `make test-frontend-playwright` – React Playwright (checks that servers are up)  
- `make test-backend-api`, `make test-cache`, `make test-workflow` – specific suites  

## Prompt learning

The app can learn from feedback to improve prompts. After tailoring, users can submit feedback (e.g. formatting, content, style). If approved for learning, the system can suggest prompt updates.  
API: `POST /api/feedback`, `POST /api/feedback/approve`, `GET /api/feedback/opportunities`, `GET /api/feedback/suggestions`, `POST /api/prompts/update`. Feedback is stored under `data/`; prompt backups are kept before updates.

## Project structure

```
resume-agent/
├── api/                    # FastAPI app
│   ├── main.py
│   └── routers/            # health, auth, applications, google_drive
├── resume_agent/
│   ├── agents/             # Parser, JD analyzer, fit evaluator, tailor, review, etc.
│   ├── agent/              # Path B: MCP skills server (optional)
│   ├── skills/             # Loader for file-backed prompts
│   ├── services/            # LLM, workflow
│   ├── prompts/            # Templates (thin wrapper over skills), intensity
│   ├── storage/, tracking/, utils/, config.py
├── skills/                 # Manifest + SKILL.md per skill
├── docs/                   # BOOTSTRAP.md, use_case.md, SKILLS.md, AGENT.md
├── frontend/                # React (Vite) UI
├── tests/
├── main.py                 # CLI entry
├── app.py                  # Streamlit (legacy)
├── validate_app.py
├── requirements.txt
└── Makefile
```

## Troubleshooting

**Google OAuth**  
- *redirect_uri_mismatch* – Use exactly `http://localhost:8000/api/auth/google/callback` in Cloud Console and `.env`.  
- *Access blocked / invalid request* – Add test users in OAuth consent screen; add required scopes.  
- *deleted_client* – Remove `token.json` and re-run file-based auth if using legacy flow.

**LLM**  
- *Missing API key* – Set the key for your provider (`GROQ_API_KEY`, `OPENAI_API_KEY`, or `ANTHROPIC_API_KEY`).  
- *Provider not found* – Set `LLM_PROVIDER` to `groq`, `ollama`, `openai`, or `anthropic`.

**General**  
- `.env` must be in project root. Paths in config resolve relative to project root.

## License

[Add your license here]
