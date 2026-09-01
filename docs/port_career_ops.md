Conclusion

  The right port is narrow: keep resume-agent’s existing Discover product shell and replace its expensive source-acquisition layer with a cheap ATS-first provider modeled on
  career-ops.

  That conclusion comes directly from the code. resume-agent already has the Discover tab, persistent inbox, feedback loop, saved searches, analytics, and Tailor handoff in /
  Users/tina/AI workspace/resume-agent/frontend/src/App.jsx:10, /Users/tina/AI workspace/resume-agent/frontend/src/components/DiscoverRoles.jsx:630, /Users/tina/AI workspace/
  resume-agent/resume_agent/storage/user_store.py:205, and /Users/tina/AI workspace/resume-agent/resume_agent/services/multi_agent_workflow.py:389. The weak point is only the
  provider layer: /Users/tina/AI workspace/resume-agent/resume_agent/services/discover_roles_service.py:147 only builds FirecrawlSearchProvider, and /Users/tina/AI workspace/
  resume-agent/resume_agent/services/discovery/firecrawl_provider.py:12 is a paid search API wrapper. Also, discovery is not using an LLM by default right now because /Users/
  tina/AI workspace/resume-agent/api/routers/discover.py:56 instantiates DiscoverRolesService() with no llm_service.

  career-ops is useful here for one thing: its zero-token ATS scan path in scan.mjs:38 and its source catalog / title guardrail pattern in portals.yml:22. It is not useful as
  a full architecture transplant, because its markdown pipeline and its documented Playwright/WebSearch layers are the wrong abstraction for resume-agent.

  One important correction for the handoff: a “live local LLM doing discovery without API calls” is not a real replacement for search. A local LLM can summarize, classify,
  and rewrite queries cheaply via Ollama, but it still needs inputs from somewhere. The cheapest practical design is: direct ATS HTTP + cached page fetch + optional local
  Ollama enrichment. Not “LLM alone”.

  What To Port

  - Port ATS detection and parsing logic from scan.mjs:38 for Greenhouse, Ashby, and Lever.
  - Port the idea of a checked-in tracked-company catalog plus coarse title guardrails from portals.yml:22.
  - Port cheap prefiltering before detail-page fetch so discovery stays fast and low cost.
  - Port the zero-token principle: discovery must work without paid LLM calls and without paid search APIs.

  What Not To Port

  - Do not port career-ops’s markdown tracker flow, pipeline.md, scan-history.tsv, or CLI scan script. resume-agent already has better persistence and UX in /Users/tina/AI
    workspace/resume-agent/resume_agent/storage/user_store.py:826.
  - Do not port search_queries / broad web search from portals.yml. That would just recreate the Firecrawl problem with a different source.
  - Do not port Playwright-first scanning as the default. resume-agent’s own discovery spec says discovery must stay manual, cheap, and avoid browser automation loops in /
    Users/tina/AI workspace/resume-agent/docs/discovery_feature.md:56 and /Users/tina/AI workspace/resume-agent/docs/discovery_feature.md:81.
  - Do not port user-specific filters from your current career-ops setup. Port the mechanism, not your personal title/location list.
  - Do not wire Codex/Claude “live tool use” into the app. The app must run on its own.

  Important Current Gaps In resume-agent

  - Provider selection is hard-coded to Firecrawl only in /Users/tina/AI workspace/resume-agent/resume_agent/services/discover_roles_service.py:147.
  - The query cache key does not include any source-catalog version today, which matters once ATS sources are configurable in /Users/tina/AI workspace/resume-agent/
    resume_agent/services/discover_roles_service.py:230.
  - seniority exists in the API and UI in /Users/tina/AI workspace/resume-agent/api/routers/discover.py:19 and /Users/tina/AI workspace/resume-agent/frontend/src/components/
    DiscoverRoles.jsx:630, but it is not used in ranking/filtering in /Users/tina/AI workspace/resume-agent/resume_agent/services/discover_roles_service.py:384.
  - Local-model support already exists via Ollama in /Users/tina/AI workspace/resume-agent/resume_agent/services/llm_service.py:19, but discovery does not opt into it.

  Implementation Plan

  1. Preserve the current product contract.
     Do not rewrite the Discover UI, storage model, or Tailor handoff. Keep the product rules in /Users/tina/AI workspace/resume-agent/docs/discovery_feature.md:56 intact:
     discovery remains user-triggered, no background crawling, no auto-fit-eval, no auto-tailoring, no application creation, no hidden personalization.
  2. Add a new ATS-first provider mode in config.
     Modify /Users/tina/AI workspace/resume-agent/resume_agent/config.py:137 to add:
     DISCOVER_PROVIDER=none|firecrawl|ats_api
     DISCOVER_SOURCE_CONFIG_PATH=config/discovery_sources.yml
     DISCOVER_ATS_FEED_TTL_MINUTES=30
     DISCOVER_LLM_ENRICHMENT_PROVIDER=none|ollama|groq|openai|anthropic
     DISCOVER_LLM_ENRICHMENT_MODEL=<optional>
     Keep DISCOVER_LLM_ENRICHMENT_PROVIDER=none as the default so discovery does not accidentally start billing remote model providers.
  3. Add a new checked-in source catalog file.
     Create config/discovery_sources.yml in resume-agent. Keep it much smaller and cleaner than career-ops/portals.yml. It should only hold source metadata and coarse title
     guardrails. Use this schema:

     version: 1

     title_guardrails:
       positive:
         - "software engineer"
         - "backend engineer"
         - "platform engineer"
         - "staff engineer"
         - "principal engineer"
         - "lead software engineer"
       negative:
         - "intern"
         - "recruiter"
         - "designer"
         - "sales"
         - "customer success"
         - "frontend"
       seniority_boost:
         - "senior"
         - "staff"
         - "lead"
         - "principal"

     tracked_companies:
       - name: "Anthropic"
         enabled: true
         provider: "greenhouse"
         careers_url: "https://job-boards.greenhouse.io/anthropic"
         api_url: "https://boards-api.greenhouse.io/v1/boards/anthropic/jobs"
         tags: ["ai", "product"]
         sponsorship_policy: "unknown"

       - name: "Vercel"
         enabled: true
         provider: "greenhouse"
         careers_url: "https://job-boards.greenhouse.io/vercel"
         api_url: "https://boards-api.greenhouse.io/v1/boards/vercel/jobs"
         tags: ["developer-tools", "product"]
         sponsorship_policy: "unknown"

     Do not include search_queries. Do not include markdown-pipeline concepts. Do not hardcode your personal Ashburn/visa filters here.
  4. Add a catalog loader.
     Create resume_agent/services/discovery/source_catalog.py.
     This module should:
      - load and validate DISCOVER_SOURCE_CONFIG_PATH
      - expose TitleGuardrails and TrackedCompany dataclasses or typed dicts
      - compute a stable catalog_hash from raw file contents
      - provide enabled_companies() and supported_companies() helpers
      - fail cleanly when the file is missing or has zero enabled supported companies

     The reason to do this separately is so discover_roles_service.py stays focused on search orchestration, not YAML parsing.
  5. Add an ATS provider module.
     Create resume_agent/services/discovery/ats_provider.py.
     Port and adapt the logic from scan.mjs:38 and scan.mjs:78 into Python:
      - detect_api(company) for Greenhouse, Ashby, Lever
      - fetch_greenhouse_jobs
      - fetch_ashby_jobs
      - fetch_lever_jobs
      - normalize_stub(job, company, provider)
      - prefilter_stub(stub, criteria, title_guardrails)

     The ATS provider should fetch feeds concurrently, cache raw feed responses by API URL, and return normalized stubs with at least:
     title, url, company, location, source_domain, source_quality="public_ats", posted_at?, employment_type?, apply_url?, description_html?, description_text?.

     Do not assume every ATS feed has description text. If the feed has a usable body, keep it. If not, leave it blank and let the service fetch the public posting page.
  6. Extend provider selection in the discovery service.
     Modify /Users/tina/AI workspace/resume-agent/resume_agent/services/discover_roles_service.py:147 so _build_provider() supports:
      - firecrawl
      - ats_api
      - none

     ats_api should be considered configured when:
      - discovery is enabled
      - source catalog loads successfully
      - at least one enabled tracked company has a supported ATS provider

     Update get_status() so the frontend can show provider: "ats_api" with correct configured state.
  7. Keep the current Firecrawl flow intact and add an ATS fast path.
     Do not delete the existing query-search path in /Users/tina/AI workspace/resume-agent/resume_agent/services/discover_roles_service.py:539. Instead branch:
      - if provider is FirecrawlSearchProvider, keep the current query_passes -> search hits -> page fetch -> extract -> rank -> enrich -> store path
      - if provider is ATSAPIProvider, use catalog fetch -> stub prefilter -> optional detail fetch -> rank -> enrich -> store

     This makes the port additive, not a rewrite.
  8. Add a cheap ATS prefilter before detail-page fetch.
     This is the core cost/performance win and should mirror the spirit of career-ops.
     The ATS prefilter should:
      - hard-drop titles that fail catalog title_guardrails
      - hard-drop obvious location conflicts using stub location
      - soft-score title matches against selected role families
      - soft-score seniority matches
      - soft-score remote-mode matches from stub location text
      - soft-score token overlap from search_intent against title + company + location
      - never call the LLM here
      - only fetch detail pages for the top DISCOVER_MAX_FETCHES_PER_SEARCH surviving stubs

     This avoids spending time on irrelevant detail pages while still preserving resume-agent’s richer per-role inbox.
  9. Fix the missing seniority behavior.
     Right now the UI and API expose seniority, but ranking ignores it. Add _infer_seniority(title, text) inside /Users/tina/AI workspace/resume-agent/resume_agent/services/
     discover_roles_service.py:384 and incorporate it into ranking:
      - if search seniority is any, do nothing
      - if inferred seniority matches requested seniority, add a score bonus and a matched filter
      - if inferred seniority is clearly below requested, add a blocker and a meaningful penalty
      - if inferred seniority is above requested by one band, allow it with a smaller bonus
      - if unknown, no bonus and no penalty

     Do not add a DB column for this in phase 1. Use it for ranking only.
  10. Reuse extractor logic, but permit direct structured ATS payloads.
     Keep /Users/tina/AI workspace/resume-agent/resume_agent/services/discovery/posting_extractor.py:1 as the shared page extraction path.
     Add a helper in discover_roles_service.py or ats_provider.py that converts a structured ATS stub directly into the same payload shape returned by _structured_from_hit()
     when the feed already contains enough description text. That payload should set:

  - canonical_url
  - source_urls
  - source_domain
  - company
  - job_title
  - location
  - remote_mode
  - employment_type
  - apply_url
  - posted_at
  - posted_label
  - date_confidence
  - extraction_confidence around 0.8-0.9
  - raw_text
  - raw_text_hash
  - source_quality="public_ats"

  If the ATS payload is missing real description text, fall back to fetching the public page and use the existing extractor.

  11. Update cache keys and namespaces.
     Keep the current cache namespaces in /Users/tina/AI workspace/resume-agent/resume_agent/storage/cache_store.py:1 and add one new namespace:
     discover_ats_feed

  Also change the query cache key in /Users/tina/AI workspace/resume-agent/resume_agent/services/discover_roles_service.py:230 to include the source-catalog hash when
  DISCOVER_PROVIDER=ats_api. Without that, editing the catalog file will not invalidate query caches.

  12. Keep the database model; do not import career-ops dedupe.
     Leave /Users/tina/AI workspace/resume-agent/resume_agent/storage/user_store.py:826 as the source of truth. It already preserves dismissed/shortlisted state on rediscove
     ry, merges source URLs, and supports inbox ordering. Do not add pipeline.md/applications.md dedupe logic from career-ops; the current DB model is better.
  13. Add optional local LLM enrichment, but keep it off by default.
     Modify /Users/tina/AI workspace/resume-agent/api/routers/discover.py:56 so _service() can optionally inject LLMService(...) when DISCOVER_LLM_ENRICHMENT_PROVIDER !=
     none.
     Use /Users/tina/AI workspace/resume-agent/resume_agent/services/llm_service.py:19 so ollama works locally.
     Rules:

  - default remains no LLM enrichment
  - if set to ollama, summaries stay local and cheap
  - if set to groq|openai|anthropic, that is an explicit opt-in cost
  - do not use the LLM to discover jobs
  - do not use the LLM in the ATS prefilter
  - only use the LLM in the existing top-survivor enrichment stage

  14. Make only minimal UI changes.
     Keep /Users/tina/AI workspace/resume-agent/frontend/src/components/DiscoverRoles.jsx:630 and /Users/tina/AI workspace/resume-agent/frontend/src/App.jsx:70 largely
     unchanged.
     Only change:

  - provider label text if you want to show ATS catalog
  - disabled-state messaging to mention missing source config when relevant
  - optionally add a tiny note near the hero text: “Searching curated public ATS boards” when provider=ats_api

  Do not add new filter controls in phase 1. The current form is already enough.

  15. Add docs, but in a new companion plan file.
     Do not rewrite /Users/tina/AI workspace/resume-agent/docs/discovery_feature.md:2, because it documents the current V1 public-web path.
     Add a new doc such as docs/discovery_ats_port_plan.md that explains:

  - why ATS-first is the default
  - how ats_api differs from firecrawl
  - why local LLM enrichment is optional, not required
  - why Playwright is deferred

  16. Add tests before any optional fallback work.
     Add or update:

  - tests/test_discover_ats_provider.py
  - tests/test_discover_roles_service.py
  - tests/test_discover_api.py
  - fixture files under tests/fixtures/discovery/

  Required test cases:

  - Greenhouse parser returns normalized stubs
  - Ashby parser returns normalized stubs
  - Lever parser returns normalized stubs
  - source catalog loader rejects bad config cleanly
  - ats_api status is configured when catalog is valid
  - ATS prefilter reduces candidates before detail fetch
  - query cache key changes when catalog hash changes
  - seniority now affects ranking
  - dismissed roles stay dismissed on rediscovery
  - open-in-tailor still links strategy briefs and still does not create application rows

  17. Explicitly defer Playwright.
     Do not implement Playwright in this port.
     If you later want it, make it a separate provider such as playwright_curated, with these rules:

  - off by default
  - only user-clicked searches
  - only an explicit allowlist of companies with no ATS feed
  - no search engine scraping
  - no CAPTCHA handling
  - sequential or very low concurrency
  - only as a fallback after ATS provider proves insufficient

  That keeps it aligned with /Users/tina/AI workspace/resume-agent/docs/discovery_feature.md:81 instead of turning discovery into a brittle browser bot.

  Cheapest End State

  - DISCOVER_PROVIDER=ats_api
  - DISCOVER_LLM_ENRICHMENT_PROVIDER=none for the absolute cheapest mode
  - or DISCOVER_LLM_ENRICHMENT_PROVIDER=ollama if you want local summaries/TLDRs
  - no Firecrawl
  - no remote LLM required
  - no Playwright required

  That gives you the main career-ops cost advantage without importing its weaker parts.

  One-sentence handoff instruction for another LLM

  Implement an ats_api discovery provider in resume-agent by porting only the ATS detection/parsing and catalog/title-guardrail concepts from career-ops, keep the existing
  Discover UI/storage/Tailor handoff intact, fix the missing seniority ranking, keep LLM enrichment optional and local-capable via Ollama, and do not add markdown tracker
  logic, broad web search, or Playwright as a default path.