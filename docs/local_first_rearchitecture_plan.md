# Local-First Rearchitecture Plan

## Goal

Rebuild the app as a local-first, SQLite-backed resume tailoring system with:

- Google-authenticated user identity
- user-scoped profile data and confirmed skills
- explicit, separate review systems for authenticity, ATS parseability, job match, and editorial quality
- versioned caching and durable artifact persistence
- onboarding that imports a resume, extracts skills, and asks the user to confirm them

The app is expected to run primarily on a single local machine. The design should therefore prefer operational simplicity and credibility over distributed-system complexity.

## Principles

1. SQLite is the primary persistence layer.
2. Google is the auth/import/export layer, not the source of truth for profile state.
3. Only confirmed user facts should be treated as validation truth.
4. Deterministic checks should not consume LLM tokens.
5. Expensive reasoning should run only where it materially improves correctness.
6. Every durable cache must be versioned and selectively invalidatable.

## Target Domains

### 1. Identity and Profile

- Google OAuth resolves an internal user record keyed by Google user id (`google_sub`)
- user profile stores:
  - confirmed skills
  - target roles
  - verified metrics
  - active resume profile
  - preferences

### 2. Ingestion and Artifacts

- import resume from Google Drive or configured default
- normalize text
- parse structure, skills, roles, metrics
- store parsed artifacts by content hash

### 3. Tailoring

- generate tailored draft from resume + JD + confirmed user context
- support iterative refinement
- save draft history per user

### 4. Reviews

Keep these outputs separate:

- `authenticity_review`
- `ats_parse_review`
- `job_match_review`
- `editorial_review`

### 5. Caching and Persistence

Persistent local store for:

- users
- skills
- quality reports
- tailored outputs
- review bundles
- cache metadata

## Workflow

1. User signs in with Google
2. System upserts local user record
3. If the user has no confirmed skills, run onboarding
4. User picks a resume source
5. System parses resume and extracts candidate skills
6. User confirms, edits, removes, or adds skills
7. User enters or imports a JD
8. System generates tailored draft
9. System runs separated reviews
10. User refines or approves
11. System saves outputs, reports, and profile changes locally

## Onboarding

### Resume Source

- use configured default resume
- choose a Google Drive doc
- future: upload file directly

### Skill Bootstrap

The system should derive:

- high-confidence detected skills
- low-confidence detected skills
- suggested related skills based on role and experience

Each skill should carry:

- source
- confidence
- category
- optional evidence snippet

### Skill States

- `detected`
- `suggested`
- `confirmed`
- `rejected`
- `archived`

Only `confirmed` skills should be allowed to justify new skill claims in tailored output.

## Persistence Plan

SQLite tables to add first:

- `users`
- `user_skill_inventory`
- `user_quality_reports`
- `user_improved_resumes`

Likely follow-on tables:

- `resume_profiles`
- `job_descriptions`
- `review_bundles`
- `cache_entries`

## Migration Plan

### Phase 1: Foundation

- add SQLite-backed user/profile store
- upsert user during Google auth callback
- introduce request-scoped current-user context
- route skill and quality/improved-resume storage through SQLite when authenticated
- retain file-based fallback for non-authenticated or legacy flows

### Phase 2: Onboarding

- add endpoints for resume profile import and parsed skill suggestions
- add confirm/edit/reject skill workflow
- persist confirmed skills in SQLite

### Phase 3: Review Separation

- split current mixed validation into separate review outputs
- stop overloading ATS score semantics

### Phase 4: Versioned Cache Layer

- persist artifacts and review outputs with versions and source hashes
- add targeted invalidation

### Phase 5: UI Rewrite

- onboarding wizard
- separate review cards
- explicit score semantics

## Immediate Execution Slice

This first implementation slice should deliver:

1. local user records in SQLite
2. user-scoped skill storage
3. user-scoped quality report storage
4. user-scoped improved resume storage
5. request-scoped current user resolution
6. Google-auth upsert on login
7. compatibility fallback to existing local memory file

That gives the app a durable local identity foundation without requiring the full review-engine rewrite in the same change.
