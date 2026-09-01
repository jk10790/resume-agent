# Discovery ATS Port Plan

The ATS-first discovery path keeps the existing Discover inbox, ranking, feedback loop, and Tailor handoff intact while replacing the expensive source-acquisition layer with curated public ATS feeds.

## Why ATS-first

- Public ATS feeds are cheaper and more stable than paid search APIs.
- The app already has the product shell and persistence; the missing piece is a cheaper provider layer.
- Structured ATS payloads let discovery prefilter aggressively before fetching detail pages.

## How `ats_api` Differs From `firecrawl`

- `firecrawl` starts with query search results and extracts structure from fetched pages.
- `ats_api` starts with a checked-in source catalog and direct Greenhouse, Ashby, or Lever feeds.
- `ats_api` keeps detail-page fetches only for the top surviving stubs after rule-based prefiltering.

## LLM Enrichment

- LLM enrichment is optional and off by default.
- `ollama` is supported as the local, low-cost enrichment path.
- Remote providers remain explicit opt-ins and are not used for discovery itself.

## Playwright

Playwright is intentionally deferred. If added later, it should be a separate provider with an explicit allowlist and manual, low-concurrency execution rather than a default discovery path.

## Feed Field Handling

Field choices that are not obvious from the provider docs, each one a bug that
was live before:

- **`first_published`, not `updated_at`.** Greenhouse bulk-refreshes
  `updated_at`, so on a typical board nearly every posting reports the same
  recent day — including ones first published two years earlier. Ranking on it
  scored stale postings as fresh.
- **Lever `createdAt` is epoch milliseconds.** Read as a string it never parses,
  so every Lever role took the "no date" ranking penalty.
- **Locations are a list.** Providers report one primary location plus a
  separate list (`offices`, `secondaryLocations`, `categories.allLocations`).
  Filtering on the primary alone dropped multi-site roles, and include/exclude
  rules are applied per location so a role open in both London and Remote (US)
  survives.
- **`workplaceType`, not `isRemote`.** Ashby reports `isRemote: true` on plainly
  hybrid postings; only `workplaceType` is trustworthy.
- **Greenhouse needs `?content=true`.** Without it the feed carries no job body
  at all, which forces a page fetch per posting and makes body filtering
  impossible.

## Two-Phase Providers

Greenhouse, Ashby and Lever resolve everything from one list call. Workday and
SmartRecruiters do not — their list endpoints return a title and little else, so
they are fetched in two phases:

1. The title gate runs against every stub. It is cheap and discards most of the
   feed.
2. Detail pages are fetched only for title-gate survivors, under a shared budget
   (`DISCOVER_MAX_HYDRATION_FETCHES`). The budget is spent in round-robin order
   across companies, because one large Workday board can otherwise produce more
   survivors than the rest of the catalog combined and crowd it out.

Stubs past the budget are flagged `hydration_skipped` rather than dropped.

## Hard Exclusions

`body_guardrails.exclude` in the source catalog holds regexes for requirements
no amount of tailoring can satisfy — an active security clearance, citizenship-
only eligibility, or an explicit refusal to sponsor. These are hard drops rather
than score penalties. This matters most around Northern Virginia, which is dense
with cleared government work that is permanently inaccessible on an H-1B.

## Catalog Breadth

Discovery can only surface roles at companies the catalog lists, so breadth —
not extraction quality — is the ceiling. `scripts/harvest_ats_companies.py`
reads board tokens out of the Common Crawl URL index (no scraping) and emits a
catalog fragment to merge by hand. `scripts/build_sponsors.py` fills
`sponsorship_policy` from DOL PERM disclosure data; PERM rather than LCA,
because LCA volume shows an employer files H-1Bs while PERM volume shows it
moves people toward a green card.

## Not Ported

Recruitee has an adapter shape in the public docs but no board in the catalog
resolved during testing, so it was left out rather than shipped unverified.
