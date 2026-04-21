
# Discover Roles — Implementation Spec And Rollout Notes

## Implementation Status

- Implemented in this repo:
  - top-level `Discover` tab and Tailor handoff
  - public-web discovery API family under `/api/discover`
  - persistent discovered-role inbox and feedback storage
  - shortlist, dismiss, restore, and open-in-tailor flows
  - strategy-brief linkage for discovery-origin tailoring
  - stale-cache fallback and structured posting cache reuse
  - saved searches
  - explicit discovery default preferences
  - discovery analytics endpoints and UI
  - rule-based suggestion scaffolding that updates defaults only after explicit acceptance
- Explicitly not implemented yet:
  - learned ranking
  - automatic query rewriting from inferred preferences
  - free-text comment analysis affecting results
  - hidden personalization

## Rollout Guidance

- Run V1 and the “useful now” layer first:
  - collect structured feedback
  - inspect analytics and funnel data
  - tune reason taxonomy and UI friction
- Do not enable learned personalization until:
  - feedback volume is real, not synthetic
  - repeated dismiss reasons are stable over time
  - shortlist and downstream application data are correlated enough to trust
  - users explicitly opt in to personalization

## What Was Added Beyond The Original V1 Spec

- Saved searches:
  - users can save and reapply search criteria
  - one saved search can be marked as default
- Explicit default preferences:
  - current discovery criteria can be stored as reusable defaults
  - suggestion acceptance writes into defaults rather than hidden ranking logic
- Analytics:
  - discover funnel from discovered -> shortlisted -> opened in Tailor -> strategy linked -> application linked
  - feedback totals, top dismiss reasons, repeated reasons in the last 90 days, and restore rate
- Suggestion scaffolding:
  - suggestion banners are advisory only
  - suggestions are shown only after repeated structured signal
  - accepting a suggestion updates saved defaults, not ranking

  ## Summary

  - Document target: docs/discover_roles_feature_plan.md
  - Add a new top-level Discover tab that searches the public web for job postings, extracts structured role data, shows a compact JD TLDR with filter-match
    badges, and lets the user open one result into the existing Tailor flow.
  - Discovery is manual and cheap. It must never auto-run fit evaluation, strategy brief generation, resume tailoring, application tracking, or browser
    automation loops.
  - The user still initiates the expensive workflow explicitly from Tailor. Discovery only feeds that workflow.

  ## Exact implementation changes

  - Add frontend component pair:
      - frontend/src/components/DiscoverRoles.jsx
      - frontend/src/components/DiscoverRoles.css
  - Wire a new Discover tab in frontend/src/App.jsx and add state for a Tailor seed object:
      - tailorDiscoverSeed
      - onOpenInTailorFromDiscover(seed)
  - Add backend router:
      - api/routers/discover.py
  - Add backend services:
      - resume_agent/services/discovery/search_provider.py
      - resume_agent/services/discovery/firecrawl_provider.py
      - resume_agent/services/discovery/posting_extractor.py
      - resume_agent/services/discover_roles_service.py
  - Extend storage in resume_agent/storage/user_store.py
  - Extend POST /api/tailor-resume request with optional discovered_role_id
  - When Tailor creates a strategy brief from a discovery seed, link that brief back to the discovered role

  ## Product rules that must not be violated

  - Discover requires an authenticated local user. If unauthenticated, show sign-in CTA only.
  - V1 uses public web only. No login-gated scraping, no browser-use loops, no CAPTCHA workarounds.
  - If no discovery provider is configured, the tab renders a disabled informational state. It does not attempt a partial fallback.
  - Search runs only when the user clicks Search. No background crawling, scheduled refresh, or auto-refresh on tab load.
  - Search results are written to a persistent per-user inbox immediately.
  - Repeated discovery of the same role updates the existing role row. It never creates duplicates for the same canonical role.
  - Discovery feedback is collected in V1, but feedback does not affect ranking in V1.
  - Dismissed roles stay dismissed if found again later. They update last_seen_at, but their inbox state is preserved.

  ## Frontend spec

  ### Tab placement

  - Add 🔎 Discover as the first tab, before Tailor Resume.

  ### Discover search form

  - Render a hero section with:
      - title: Discover Roles
      - subtitle: Search broadly, review quickly, and only run strategy evaluation on roles you choose.
  - Render a form with these exact fields:
      - Search intent — free text input, placeholder e.g. applied AI backend roles at product startups
      - Role families — multiselect using the existing 6 archetypes
      - Seniority — single select with values:
          - any
          - junior
          - mid
          - senior
          - staff
          - principal
          - manager
          - director
      - Remote modes — multi-select chips:
          - remote
          - hybrid
          - onsite
      - Include locations — comma-separated text input
      - Exclude locations — comma-separated text input
      - Must-have keywords — comma-separated text input
      - Avoid keywords — comma-separated text input
  - Buttons:
      - primary: Search
      - secondary: Clear
  - Validation:
      - at least one of search_intent or role_families must be present
      - otherwise show inline error: Add search intent or at least one role family.

  ### Discover results UI

  - After search, render:
      - a result meta bar with:
          - result count
          - source label: Cached | Fresh search | Cached fallback
          - warnings if any
      - a filter banner if the inbox list is filtered
  - Each result card shows these exact fields:
      - company
      - title
      - source domain
      - location
      - remote mode
      - Posted with exact date if available, otherwise relative label, otherwise Date unavailable
      - archetype label
      - TLDR
      - matched filter pills
      - blocker pills
      - extraction confidence badge only if confidence < 0.60
  - Each result card actions:
      - Open posting
      - Open in Tailor
      - Shortlist
      - Not relevant
  - Clicking Not relevant opens a small inline form with reason chips:
      - wrong role family
      - wrong seniority
      - wrong location or remote
      - too frontend-heavy
      - too managerial
      - too customer-facing
      - wrong domain
      - tech mismatch
      - company type not right
      - duplicate or stale
      - optional comment textarea
      - submit button Save feedback
  - Clicking Shortlist records shortlist feedback immediately. Optional comment is not required.
  - Add inbox filter controls above the persistent list:
      - Active
      - Shortlisted
      - Dismissed
      - All
      - text search input
  - Default inbox filter is Active
  - Active means roles whose inbox_state is discovered or shortlisted

  ### Tailor handoff

  - Open in Tailor must:
      - call POST /api/discover/roles/{id}/open-in-tailor
      - switch to Tailor tab
      - prefill:
          - company
          - job title
          - job URL
          - JD text
          - discovered role id
      - show a banner in Tailor:
          - title: Discovery result loaded
          - body: This role came from Discover. Review it, then click Tailor Resume to start the strategy-first workflow.
  - Tailor must not auto-run any workflow step on handoff.

  ## Exact search and ranking behavior

  ### Role-family expansion

  - Use this exact v1 synonym map. Include DISCOVER_ROLE_EXPANSION_VERSION=v1 in all query cache keys.

  | Canonical family | Query title variants |
  |---|---|
  | software_engineering | software engineer, software developer, backend engineer, backend developer, application engineer, product engineer, full stack
  engineer |
  | platform_infrastructure | platform engineer, infrastructure engineer, site reliability engineer, sre, devops engineer, production engineer |
  | data_ml_ai | data engineer, machine learning engineer, ml engineer, data platform engineer, ai engineer |
  | applied_ai_llmops | applied ai engineer, llm engineer, llmops engineer, agent engineer, ai systems engineer, ai platform engineer |
  | product_technical_product | product manager, technical product manager, platform product manager, ai product manager |
  | solutions_customer_engineering | solutions engineer, customer engineer, sales engineer, forward deployed engineer, implementation engineer |

  ### Query construction

  - Normalize criteria before use:
      - trim strings
      - lowercase for matching
      - dedupe tokens
      - sort arrays for cache keys
  - Construct query passes in this exact order:
      1. pass 1 = user search_intent + highest-priority role-family variant
      2. pass 2 = next-highest role-family variant
      3. pass 3 = next-highest role-family variant
  - Max query variants per search: 3
  - Max raw provider hits per query variant: 15
  - Max raw unique URLs after URL dedupe: 20
  - If the user selected multiple role families, order pass variants by the family order in the UI selection.
  - Must-have keywords are appended to pass queries.
  - Avoid keywords are not appended to pass queries; they only affect ranking and blocker tags.

  ### Deterministic extraction first

  - For each candidate URL:
      - fetch page text
      - strip HTML, nav, footer, scripts
      - attempt deterministic extraction of:
          - job title
          - company
          - apply URL
          - location
          - remote mode
          - posted date
          - employment type
          - raw text
  - Deterministic extraction sources in this exact order:
      1. JSON-LD job posting schema
      2. meta tags
      3. known ATS page patterns
      4. visible page text
  - Drop a result entirely if extracted readable text length is < 350 characters
  - If extraction confidence is < 0.35, drop the result entirely
  - If extracted apply URL exists, it becomes the candidate canonical URL base; otherwise use the fetched page URL

  ### LLM enrichment

  - LLM enrichment is allowed only for the top 12 survivors after cheap ranking.
  - Batch size: 4 postings per LLM call
  - Max LLM enrichment calls per search: 3
  - Each posting sent to the LLM is truncated to 1800 characters
  - Use a separate discovery prompt, not the strategy or fit-eval prompts
  - LLM prompt contract:

  [
    {
      "id": "candidate-local-id",
      "tldr": "max 260 chars, 2 sentences max",
      "archetype": "software_engineering|platform_infrastructure|data_ml_ai|applied_ai_llmops|product_technical_product|solutions_customer_engineering|unknown",
      "seniority": "junior|mid|senior|staff|principal|manager|director|unknown",
      "remote_mode": "remote|hybrid|onsite|unknown",
      "possible_blockers": ["max 4 short strings"]
    }
  ]

  - Prompt rules:
      - describe the role only
      - do not evaluate candidate fit
      - do not recommend whether to apply
      - do not invent company facts
      - do not add blockers not grounded in the page text

  ### Final ranking formula

  - Compute rank_score with this exact formula:
      - +60 if final archetype is one of the user-selected role families
      - +20 if matched title variant came from the selected family expansion map
      - +15 for remote mode match
      - -40 for explicit remote mode conflict
      - +10 if include location matched or location filter was empty
      - -50 if exclude location matched
      - +10 per must-have keyword hit, max +30
      - -20 per avoid keyword hit, max -60
      - +15 if posted within last 7 days
      - +8 if posted within last 30 days
      - -10 if posted date missing
      - +10 if source quality is company_public
      - +8 if source quality is public_ats
      - -15 if source quality is aggregator
      - +10 if extraction confidence >= 0.85
      - -25 if extraction confidence < 0.50
  - Source quality buckets:
      - company_public
      - public_ats
      - general_web
      - aggregator
  - Final sort order:
      1. rank_score descending
      2. posted_at descending with nulls last
      3. company ascending
      4. job_title ascending

  ## Storage schema

  ### Add to resume_agent/storage/user_store.py

  - Create these exact tables:

  CREATE TABLE IF NOT EXISTS discovered_roles (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL,
      canonical_url TEXT NOT NULL,
      source_urls_json TEXT NOT NULL DEFAULT '[]',
      source_domain TEXT NOT NULL,
      company TEXT NOT NULL,
      job_title TEXT NOT NULL,
      matched_title_variant TEXT,
      location TEXT,
      remote_mode TEXT NOT NULL DEFAULT 'unknown',
      employment_type TEXT NOT NULL DEFAULT 'unknown',
      apply_url TEXT,
      posted_at TEXT,
      posted_label TEXT,
      date_confidence TEXT NOT NULL DEFAULT 'unknown',
      archetype TEXT NOT NULL DEFAULT 'unknown',
      extraction_confidence REAL NOT NULL DEFAULT 0.0,
      raw_text TEXT NOT NULL DEFAULT '',
      raw_text_hash TEXT,
      short_tldr TEXT NOT NULL DEFAULT '',
      matched_filters_json TEXT NOT NULL DEFAULT '[]',
      possible_blockers_json TEXT NOT NULL DEFAULT '[]',
      rank_score REAL NOT NULL DEFAULT 0.0,
      inbox_state TEXT NOT NULL DEFAULT 'discovered',
      opened_in_tailor_at TEXT,
      opened_strategy_brief_id INTEGER,
      first_seen_at TEXT NOT NULL,
      last_seen_at TEXT NOT NULL,
      last_scraped_at TEXT,
      last_ranked_at TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      UNIQUE(user_id, canonical_url),
      FOREIGN KEY(user_id) REFERENCES users(id),
      FOREIGN KEY(opened_strategy_brief_id) REFERENCES job_strategy_briefs(id)
  );

  CREATE TABLE IF NOT EXISTS discovered_role_feedback (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL,
      discovered_role_id INTEGER NOT NULL,
      decision TEXT NOT NULL,
      reasons_json TEXT NOT NULL DEFAULT '[]',
      comment TEXT,
      created_at TEXT NOT NULL,
      FOREIGN KEY(user_id) REFERENCES users(id),
      FOREIGN KEY(discovered_role_id) REFERENCES discovered_roles(id)
  );

  - Create these exact indexes:

  CREATE INDEX IF NOT EXISTS idx_discovered_roles_user_state ON discovered_roles(user_id, inbox_state);
  CREATE INDEX IF NOT EXISTS idx_discovered_roles_user_rank ON discovered_roles(user_id, rank_score DESC);
  CREATE INDEX IF NOT EXISTS idx_discovered_roles_user_seen ON discovered_roles(user_id, last_seen_at DESC);
  CREATE INDEX IF NOT EXISTS idx_discovered_roles_user_posted ON discovered_roles(user_id, posted_at DESC);
  CREATE INDEX IF NOT EXISTS idx_discovered_roles_hash ON discovered_roles(raw_text_hash);
  CREATE INDEX IF NOT EXISTS idx_discovered_feedback_user_role ON discovered_role_feedback(user_id, discovered_role_id, created_at DESC);

  ### Add exact storage helpers

  - save_or_merge_discovered_role_for_user(user_id, role_payload) -> dict
  - get_discovered_role_for_user(user_id, role_id) -> dict | None
  - list_discovered_roles_for_user(user_id, inbox_state='active', search=None, limit=50) -> list[dict]
  - record_discovered_role_feedback_for_user(user_id, role_id, decision, reasons, comment) -> dict
  - update_discovered_role_inbox_state_for_user(user_id, role_id, inbox_state) -> dict
  - mark_discovered_role_opened_in_tailor_for_user(user_id, role_id) -> dict
  - link_discovered_role_strategy_brief_for_user(user_id, role_id, strategy_brief_id) -> None

  ### Inbox state rules

  - Allowed inbox_state values:
      - discovered
      - shortlisted
      - dismissed
  - Search upsert behavior:
      - new role => discovered
      - existing role => keep existing inbox_state unchanged
      - always update last_seen_at
  - Shortlist action:
      - set inbox_state='shortlisted'
      - record feedback row with decision='shortlisted'
  - Dismiss action:
      - set inbox_state='dismissed'
      - record feedback row with decision='not_relevant'
  - Restore action:
      - set inbox_state='discovered'
      - record feedback row with decision='restored'
  - Open in Tailor:
      - do not modify inbox_state
      - set opened_in_tailor_at=now
      - if inbox_state='dismissed', return HTTP 409 with detail Restore this role before opening it in Tailor.

  ## Cache and cost policy

  ### Config additions

  - Add these settings with these defaults:
      - DISCOVER_ENABLED=false
      - DISCOVER_PROVIDER=none
      - DISCOVER_FIRECRAWL_API_KEY=null
      - DISCOVER_QUERY_CACHE_HOURS=6
      - DISCOVER_POSTING_RECENT_TTL_HOURS=12
      - DISCOVER_POSTING_STALE_TTL_HOURS=72
      - DISCOVER_MAX_QUERY_VARIANTS=3
      - DISCOVER_MAX_RESULTS_PER_VARIANT=15
      - DISCOVER_MAX_FETCHES_PER_SEARCH=20
      - DISCOVER_MAX_SURVIVORS=12
      - DISCOVER_MAX_DISPLAY_RESULTS=20
      - DISCOVER_MIN_EXTRACTED_TEXT_CHARS=350
      - DISCOVER_ROLE_EXPANSION_VERSION=v1

  ### Cache namespaces using existing cache_entries

  - discover_query
  - discover_page_fetch
  - discover_structured_posting
  - discover_llm_enrichment

  ### Exact cache keys

  - Query cache key:
      - sha256(user_id + normalized_criteria_json + DISCOVER_ROLE_EXPANSION_VERSION + provider_name)
  - Page fetch cache key:
      - sha256(normalized_url)
  - Structured posting cache key:
      - sha256(canonical_url + extractor_version)
  - LLM enrichment cache key:
      - sha256(raw_text_hash + enrichment_prompt_version + provider_name + model_name)

  ### Cache behavior

  - Fresh query cache hit:
      - return cached ordered role ids immediately
      - do not call provider
  - Expired query cache:
      - run a fresh provider search synchronously
      - reuse page-fetch, structured-posting, and LLM-enrichment caches aggressively
  - Provider failure with previous cached query available:
      - return cached results with result_source='stale_cache_fallback'
      - return warning banner Showing previous results because live search failed.
  - Structured posting TTL:
      - if posted_at is within 14 days or missing => 12 hours
      - otherwise => 72 hours
  - LLM enrichment reuse:
      - if raw_text_hash unchanged, reuse TLDR/archetype/blockers even when structured posting cache is refreshed

  ## API contracts

  ### POST /api/discover/search

  Request:

  {
    "search_intent": "applied ai backend roles",
    "role_families": ["applied_ai_llmops", "software_engineering"],
    "seniority": "senior",
    "remote_modes": ["remote"],
    "include_locations": ["new york", "boston"],
    "exclude_locations": ["san francisco"],
    "must_have_keywords": ["python", "llm", "backend"],
    "avoid_keywords": ["frontend", "onsite"],
    "page_size": 20,
    "refresh": false
  }

  Response:

  {
    "search_key": "opaque-hash",
    "result_source": "cache|fresh_search|stale_cache_fallback",
    "warnings": ["optional warning strings"],
    "roles": [
      {
        "id": 123,
        "company": "Acme",
        "job_title": "Applied AI Engineer",
        "canonical_url": "https://...",
        "apply_url": "https://...",
        "source_domain": "boards.greenhouse.io",
        "location": "Remote - US",
        "remote_mode": "remote",
        "employment_type": "full_time",
        "posted_at": "2026-04-20T00:00:00+00:00",
        "posted_label": "2 days ago",
        "date_confidence": "high",
        "archetype": "applied_ai_llmops",
        "short_tldr": "Two-sentence summary...",
        "matched_filters": ["remote", "python", "llm", "applied_ai_llmops"],
        "possible_blockers": ["US-only remote"],
        "rank_score": 96.0,
        "inbox_state": "discovered",
        "extraction_confidence": 0.91,
        "opened_strategy_brief_id": null,
        "last_seen_at": "2026-04-20T12:00:00+00:00"
      }
    ]
  }

  ### GET /api/discover/roles

  - Query params:
      - inbox_state=active|shortlisted|dismissed|all
      - search=<text>
      - limit=<int default 50 max 100>
  - active means discovered OR shortlisted
  - Order:
      - if active|all: shortlisted first, then rank_score desc, then posted_at desc, then last_seen_at desc
      - if dismissed: created_at desc on latest feedback event

  ### GET /api/discover/roles/{id}

  - Return the role card fields plus:
      - raw_text
      - source_urls
      - latest 5 feedback events

  ### POST /api/discover/roles/{id}/shortlist

  Request:

  {"comment": "Looks strong for platform + AI"}

  - Set inbox_state='shortlisted'
  - Record feedback event with decision='shortlisted'

  ### POST /api/discover/roles/{id}/dismiss

  Request:

  {
    "reasons": ["too frontend-heavy", "wrong location or remote"],
    "comment": "UI-heavy and hybrid"
  }

  - Set inbox_state='dismissed'
  - Record feedback event with decision='not_relevant'

  ### POST /api/discover/roles/{id}/restore

  - Set inbox_state='discovered'
  - Record feedback event with decision='restored'

  ### POST /api/discover/roles/{id}/open-in-tailor

  Response:

  {
    "discover_seed": {
      "discovered_role_id": 123,
      "company": "Acme",
      "job_title": "Applied AI Engineer",
      "job_url": "https://canonical-or-apply-url",
      "jd_text": "clean extracted JD text",
      "posted_at": "2026-04-20T00:00:00+00:00",
      "posted_label": "2 days ago",
      "source_domain": "boards.greenhouse.io",
      "apply_url": "https://..."
    }
  }

  ### Tailor request extension

  - Extend existing Tailor request with:

  {
    "discovered_role_id": 123
  }

  - When a strategy brief is persisted, if discovered_role_id is present:
      - set opened_strategy_brief_id on the discovered role
      - do not create an application row

  ## Provider abstraction

  - Define this exact interface:

  class SearchHit(TypedDict):
      url: str
      title: str
      snippet: str
      source_domain: str

  class SearchProvider(Protocol):
      name: str
      def search(self, query: str, max_results: int) -> list[SearchHit]: ...

  - V1 concrete provider:
      - FirecrawlSearchProvider
      - enabled only when DISCOVER_PROVIDER=firecrawl and API key is present
  - If provider unavailable:
      - /api/discover/search returns HTTP 503 with detail Discover search is not configured on this instance.

  ## Future evolution and activation thresholds

  - V1.0
      - persistent inbox
      - search + scrape + TLDR
      - shortlist/dismiss feedback collection
      - no learned ranking
      - implemented
  - V1.1 activation threshold
      - only after a user has at least 15 feedback events total
      - and at least 3 repeated events for the same structured reason in the last 90 days
      - then show a non-blocking suggestion banner:
          - You often dismiss frontend-heavy roles. Add "frontend" to Avoid keywords?
      - suggestions update defaults only after explicit user confirmation
      - partially implemented now:
          - analytics can detect repeated reasons
          - advisory suggestions can be accepted or dismissed
          - accepted suggestions update explicit defaults only
      - still deferred:
          - broader suggestion catalog
          - comment-derived suggestions
  - V2 activation threshold
      - only after a user has at least 30 feedback events
      - and at least 10 shortlisted roles
      - and explicit opt-in to Personalize ranking
      - ranking may then use approved learned preferences only
      - should not be implemented before validating:
          - suggestion acceptance rate
          - restore rate on dismissed roles
          - discovery-to-application funnel quality
          - stability of repeated dismiss reasons
  - Free-text comments:
      - comments become analysis input no earlier than V2

## Proceeding To Next Phases

- Proceed from V1.0 to V1.1 broadly when:
  - users are consistently producing structured feedback
  - restore rate is low enough that dismiss reasons look trustworthy
  - suggestion acceptance is positive enough to justify broader suggestion coverage
- Proceed from V1.1 to V2 only when:
  - users have at least 30 feedback events and at least 10 shortlisted roles
  - a meaningful share of discovery-origin roles reach strategy briefs and applications
  - personalization is explicit and reviewable
  - ranking changes can be explained in product copy and debug output

## Current Verification Notes

- Completed:
  - backend modules compile
  - frontend production build passes
  - storage smoke checks for discovered roles and feedback helpers
- Still required in a fully provisioned local environment:
  - backend pytest execution for discovery API, storage, and workflow linkage
  - live Firecrawl validation against a real API key
  - browser-level interaction pass for saved searches, analytics, and suggestion acceptance
  ## Regression and bottleneck guards

  - Discover must not write to applications table.
  - Discover must not create strategy briefs or review bundles.
  - Search must not overwrite dismissed or shortlisted roles back to discovered.
  - Slow network must not erase previous inbox state.
  - Query cache and structured-posting cache must remain user-scoped where applicable.
  - Provider caps and survivor caps must be enforced server-side, not only in config comments.
  - Dismissed roles must stay hidden from Active even when rediscovered.

  ## Test plan

  - Storage:
      - upsert discovered role merges same canonical role
      - dismissed/shortlisted state survives rediscovery
      - feedback rows are user-scoped and ordered correctly
  - Cache:
      - fresh query cache prevents provider call
      - expired query cache triggers provider call but reuses posting/TLDR caches
      - stale cache fallback returns warning and cached results on provider failure
  - Extraction:
      - JSON-LD date wins over text heuristics
      - low-confidence extraction is dropped
      - canonical URL prefers extracted apply URL when present
  - Ranking:
      - remote mismatch is penalized
      - avoid keyword hits are penalized but not hard-dropped
      - fresh roles rank above stale or undated roles
  - API:
      - search endpoint returns cards only and does not call fit evaluation
      - dismiss/restore/shortlist update state and feedback correctly
      - open-in-tailor returns seed and marks opened_in_tailor_at
      - tailor request with discovered_role_id links resulting strategy brief back to the discovered role
  - Frontend:
      - unauthenticated discover state shows sign-in CTA
      - provider-missing state shows disabled informational card
      - result cards render required fields and actions
      - dismiss form saves reasons and comment
      - open-in-tailor switches tabs and prefills Tailor without auto-running
      - Tailor banner appears for discovery seeds

  ## Assumptions and defaults

  - The implementation may reuse parts of the current JD extraction utilities for per-page cleanup, but discovery is a separate service and separate API
    family.
  - The existing visual language from Strategies, Applications, and Tailor should be reused for Discover cards, pills, forms, and empty states.
  - SQLite JSON fields are stored as TEXT containing JSON arrays/objects.
  - All timestamps are ISO 8601 UTC strings, matching current local store patterns.
