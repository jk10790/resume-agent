# Discovery → Tailoring Integration Review

Scope: how the `Discover` module can carry a user from "found a role" to "tailored a
resume for it" — specifically (1) reading the full JD in-app, (2) evaluating fit on a
chosen discovery result, and (3) moving that same result into tailoring.

This is a design review of the code as it stands on `main`, plus a recommended
implementation shape. No behaviour is changed by this document.

---

## 1. What already exists

The handoff is not greenfield. A one-way Discovery → Tailor path is already wired:

| Layer | Artifact | Behaviour |
|---|---|---|
| Backend | `api/routers/discover.py:206` | `POST /api/discover/roles/{id}/open-in-tailor` |
| Service | `resume_agent/services/discover_roles_service.py:1012` | `open_in_tailor()` — marks `opened_in_tailor_at`, returns a `discover_seed` |
| App state | `frontend/src/App.jsx:14,110` | `tailorDiscoverSeed` + tab switch |
| Tailor | `frontend/src/components/TailorResume.jsx:364-376` | seeds `company`, `job_title`, `job_url`, `jd_text`, keeps `discoveryContext` |
| Tailor | `frontend/src/components/TailorResume.jsx:2295` | "Discovery result loaded" banner |
| Tailor | `frontend/src/components/TailorResume.jsx:1287` | posts `discovered_role_id` to `/api/tailor-resume` |
| Workflow | `resume_agent/services/multi_agent_workflow.py:389-394` | links the generated strategy brief back to the discovered role |

Two facts that make the rest of this design cheap:

- **The stored JD is the real JD.** `discovered_roles.raw_text`
  (`resume_agent/storage/user_store.py:223`) holds the hydrated posting body, and the
  tailoring workflow consumes `request.jd_text` directly
  (`multi_agent_workflow.py:215`) — it never re-scrapes when text is supplied. So a
  discovery result can drive tailoring end-to-end without touching the network again.
- **A read-only detail endpoint already exists and is unused by the UI.**
  `GET /api/discover/roles/{id}` (`api/routers/discover.py:170`) returns the full row —
  including `raw_text` — plus the last 5 feedback events, and it does **not** mutate
  inbox state.

## 2. What is missing

1. **No way to read the job in-app.** Cards render `short_tldr` only
   (`DiscoverRoles.jsx:799`). The only route to the full posting is "Open posting",
   which leaves the app. `GET /api/discover/roles/{id}` is never called from the frontend.
2. **No fit evaluation from Discovery.** Fit is reachable only from the Tailor tab
   ("Only evaluate fit", `TailorResume.jsx:3367`) or from the Chrome extension endpoint
   `POST /api/evaluate-fit` (`api/main.py:517`). A user must therefore commit a role to
   the Tailor tab before learning it is a 3/10.
3. **Fit results are not persisted per role.** Nothing on `discovered_roles` records a
   score, so the inbox cannot be sorted or filtered by fit, and re-checking a role costs
   another LLM run.
4. **The seed is thin.** `open_in_tailor` sends 8 fields; `location`, `remote_mode`,
   `compensation`, `extraction_confidence` and any fit result are dropped, so the Tailor
   banner cannot show why this role was worth opening.

## 3. Recommended shape — a role detail drawer with a three-step ladder

Keep everything up to the tailoring decision inside the Discover tab; hand off to the
existing Tailor workspace for the expensive part.

```
Discover card ──▶ Role detail modal ──▶ Evaluate fit (inline) ──▶ Tailor resume ─▶ Tailor tab
   (list)          GET /roles/{id}       POST /roles/{id}/          POST /roles/{id}/
                   read-only             evaluate-fit               open-in-tailor
```

**Why this split.** `TailorResume.jsx` is ~4.7k lines and owns the strategy-first
workflow with its two approval gates. Duplicating any of it inside Discovery would fork
the most delicate flow in the app. Discovery should stay a cheap triage surface — which
is also the product rule the module was built to (`docs/discovery_feature.md`:
"Discovery is manual and cheap. It must never auto-run fit evaluation, strategy brief
generation, resume tailoring...").

### 3.1 Frontend — `RoleDetailModal`

Trigger: a new **View job** action on each card (and clicking the card body).

Fetch `GET /api/discover/roles/{id}` on open. Render:

- Header: company · title · location · remote mode · posted label · compensation ·
  source domain, with the existing "Low confidence" badge when
  `extraction_confidence < 0.6`.
- Matched-filter and blocker pills (already on the row).
- **Full JD** — `raw_text` in a scrollable pane with `white-space: pre-wrap`. Pair the
  low-confidence badge with a line like *"Extracted text may be partial — open the
  original posting to confirm"*, because tailoring on a truncated JD produces a weak
  strategy brief and that failure is otherwise invisible.
- Feedback history (`feedback_events`, already returned).
- Footer ladder: `Evaluate fit` · `Shortlist` · `Not relevant` · `Tailor resume →`.

Modal mechanics can follow the existing pattern in `TailorResume.css:1466`
(`.quality-report-modal` / `.modal-content` / `.close-button`); add the equivalent
classes to `DiscoverRoles.css` rather than importing across components.

**State rules**

- Opening the modal must **not** call `open-in-tailor`. That endpoint stamps
  `opened_in_tailor_at`, which feeds the `opened_in_tailor_roles` funnel metric
  (`discover_roles_service.py:855`). Using it as a viewer would silently corrupt the funnel.
- For `inbox_state === 'dismissed'`, disable **Tailor resume** and surface **Restore** —
  `open_in_tailor` raises `PermissionError` → HTTP 409 for dismissed roles
  (`discover_roles_service.py:1016-1017`).
- Unauthenticated or no resume configured → show the sign-in / pick-a-resume CTA in place
  of the fit button instead of letting the call fail with a 401 body.

### 3.2 Backend — fit evaluation on a discovered role

Add `POST /api/discover/roles/{role_id}/evaluate-fit` to `api/routers/discover.py`.

```
1. get_discovered_role_for_user(user_id, role_id)          -> 404 if missing
2. jd_text = role["raw_text"]                              -> never re-scrape
3. resume_text  <- resume_doc_id | preferred doc | settings -> 400 if none
4. evaluate                                                 -> FitEvaluation
5. persist on the role + record a feedback event
6. return the same payload shape as /api/evaluate-fit
```

Three points worth getting right:

**Use the stored text, not the URL.** `POST /api/evaluate-fit` prefers `job_url` over
`jd_text` (`api/main.py:570-573`) and re-runs `extract_clean_jd`. For a discovery role
that URL is usually a Greenhouse/Lever/Ashby page that the ATS provider already hydrated
and that often blocks a second direct fetch — so calling the existing endpoint with both
fields set would be slower, more fragile, and would discard the good text discovery
already has. The new endpoint should pass `jd_text` only.

**Factor the shared body rather than copying it.** `api/main.py:517-605` is ~90 lines of
resume-resolution + workflow-step plumbing. Lift it into something like
`resume_agent/services/fit_evaluation_service.py::evaluate_fit_for_jd(jd_text,
resume_doc_id, google_services, local_user_id)` and have both `/api/evaluate-fit` and the
new discovery endpoint call it. Otherwise the Chrome extension and Discovery drift apart.

**Persist the result.** Add four additive columns to `discovered_roles`, following the
existing migration pattern at `user_store.py:245-249`:

```sql
fit_score REAL
fit_should_apply INTEGER
fit_evaluation_json TEXT
fit_evaluated_at TEXT
```

That unlocks a fit badge on the card, "sort by fit" in the inbox, and re-opening the
modal without paying for a second LLM run (offer an explicit **Re-evaluate**).

Also record `record_discovered_role_feedback_for_user(..., decision="fit_evaluated", ...)`
so the analytics funnel gains a stage between `shortlisted_roles` and
`opened_in_tailor_roles` — that is the number that will tell you whether the drawer
actually reduces wasted tailoring runs.

### 3.3 Carrying the fit result into tailoring

Extend the `discover_seed` in `open_in_tailor()` with `fit_evaluation`, `location`,
`remote_mode`, `compensation` and `extraction_confidence`. Then in `TailorResume.jsx`:

- Show the score in the existing discovery banner (`TailorResume.jsx:2295`) — "Fit 8/10,
  evaluated in Discover · 2 min ago".
- Leave `evaluateFirst` **off** when a fresh evaluation came with the seed, so the same
  evaluation is not paid for twice; keep it on when there is none.

No change to the workflow engine, the approval gates, or the strategy-brief linkage is
needed — `discovered_role_id` already rides through to
`link_discovered_role_strategy_brief_for_user`.

## 4. Alternatives considered

| Option | Cost | Verdict |
|---|---|---|
| **B — frontend-only.** Modal renders `raw_text` already present in the list payload; fit button calls existing `POST /api/evaluate-fit` with `jd_text` only. | One component, zero backend | Good as PR 1 if you want it shipping this week. No persistence, no fit badge, no analytics stage, re-evaluates every time. |
| **C — Discovery drives the full strategy brief** via `POST /api/job-strategy/evaluate` with the role's JD. | Medium | Not for v1. That is the expensive path (fit + brief), it needs the brief review/approval UI, and it would duplicate the Tailor workspace inside Discovery. |
| **D — merge Discovery into the Tailor tab as a job picker.** | High | Rejected. Loses the standalone triage inbox and forces the 4.7k-line component to grow again. |

Recommended: **A**, delivered as B→A in sequence.

## 5. Suggested sequencing

- **PR 1 — read.** `RoleDetailModal` + "View job" action + CSS; wire `GET /roles/{id}`;
  trim the list payload (§6.3). Ships the "view the job thoroughly" ask on its own.
- **PR 2 — evaluate.** Extract `fit_evaluation_service`; add
  `POST /roles/{id}/evaluate-fit`; add the four columns + migration; fit badge on cards;
  `fit_evaluated` feedback event and the new funnel stage.
- **PR 3 — tailor.** Enrich the seed; banner shows fit; skip the duplicate evaluation;
  sort/filter inbox by fit.

Tests to add alongside:

- `tests/test_discover_api.py` — new endpoint 200 / 404 / 409-on-dismissed / 400-no-resume.
- `tests/test_discover_roles_service.py` — fit persisted on the role; seed carries it.
- `tests/test_user_store.py` — migration adds columns on an existing DB; list payload
  excludes `raw_text`.
- `tests/test_frontend_playwright.py` — open modal, JD visible, evaluate, handoff.

## 6. Findings from the read-through

Independent of which option you pick.

1. **`api/main.py:570` prefers `job_url` over `jd_text` in `/api/evaluate-fit`.** Any
   caller that has good text *and* a URL gets a redundant scrape. Discovery is exactly
   that caller. Prefer `jd_text` when present, or pass URL only.
2. **`resume_agent/agents/fit_evaluator.py:59` references an undefined `known_skills`.**
   `_evaluate_fit_structured_fallback` does not take that parameter, so the fallback
   raises `NameError` — and because it runs inside the `except` handler of
   `evaluate_resume_fit`, it masks the original error. Reachable from `main.py:79`,
   `main.py:182` and `resume_workflow.py:189`. One-line fix: thread `known_skills`
   through.
3. **The inbox list ships the entire JD for every role.**
   `list_discovered_roles_for_user` (`user_store.py:970-1023`) does `SELECT *`, and
   `_normalize_discovered_role_row` keeps `raw_text`. At the 100-role cap that is a
   multi-megabyte response for a list view that renders only `short_tldr`. Select explicit
   columns and let the detail endpoint serve the body — which is what the modal wants
   anyway.
4. **`api/main.py:1498` — `track_application=(request.track_application and not
   request.discovered_role_id)`** silently disables tracking for discovery-origin runs in
   the streaming call. In practice the strategy-first flow always stops at an approval
   gate and `/api/approve-resume` (`api/main.py:1823`) tracks with a hardcoded
   `track_application=True`, so the funnel's `application_linked_roles` still fills via
   the approval path. The condition looks vestigial; either drop it or comment why it is
   there, because it makes the discovery path behave differently from every other path
   for a non-obvious reason.
5. **`open_in_tailor` conflates "opened" with "intent to tailor".** Once a viewer exists,
   consider whether `opened_in_tailor_at` should be stamped on the modal's Tailor button
   only (recommended, and what §3.1 assumes) so the funnel keeps measuring intent.
