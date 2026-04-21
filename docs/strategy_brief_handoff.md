# Strategy Brief Workflow Rework Handoff

## Purpose

This document is the authoritative handoff for the `feat/strategy-brief-workflow` branch in `resume-agent`.

It exists so a new session rooted in `resume-agent` can continue implementation without losing product intent, design decisions, or partially completed work.

This is not a lightweight summary. It intentionally contains:
- the full implementation direction
- the detailed product rationale
- the exact workflow changes that should be made
- what has already been implemented
- what remains to be implemented
- constraints and non-goals
- explicit instructions for future implementation behavior

## Critical Instructions For The Next Implementing LLM

These are hard constraints, not suggestions:

1. There are **no older clients** for this project.
   - Do **not** keep branching code for backward compatibility.
   - Do **not** preserve legacy workflow branches just because they existed before.
   - If a new workflow is better and is now the product direction, make it the workflow.

2. Do **not** leave tech debt in the name of compatibility.
   - Prefer replacing obsolete paths over layering fallback branches.
   - Remove or simplify logic that only exists to preserve prior behavior when that prior behavior is no longer the target product.

3. Always think beyond the original plan while implementing.
   - The plan is the starting point, not a cage.
   - If implementation reveals a cleaner seam, a missing abstraction, a workflow risk, or a simpler architecture, take the better path.
   - However, preserve the product intent described here.

4. Always think about regressions.
   - Every meaningful workflow or model change should be evaluated for how it affects: approval flow, saved docs, tracking, quality checks, refinement, and truthfulness constraints.
   - Review UI regressions matter as much as backend regressions.

5. Keep the product human-in-the-loop.
   - Do not let this become an auto-apply or auto-pilot system.
   - Hard user review gates are intentional and should remain.

6. Build the new system as the default system.
   - The strategy brief is not an optional sidecar.
   - It is intended to become the primary workflow entrypoint before tailoring.

## Product Direction

`resume-agent` should evolve from a strong tailoring tool into a **strategy-first tailoring system**.

The core idea is to create a **Job Strategy Brief** for each role before any resume tailoring happens. That brief must be reviewed and approved by the user before draft generation. Then the final tailored resume must also be reviewed and approved before saving/tracking.

The workflow should remain incremental in the existing app shell. This is **not** a full UI rewrite and not a separate product.

The strongest ideas being imported come from `career-ops`, specifically:
- deeper job evaluation
- more explicit tailoring strategy
- truthful gap analysis
- reusable per-job strategy context
- structured positioning rather than generic keyword stuffing

What should **not** be copied from `career-ops` in this phase:
- search features
- ATS portal crawling
- external compensation/demand research
- broad “career OS” features unrelated to tailoring and job strategy

## Full Implementation Plan

### 1. Add a canonical per-job Job Strategy Brief

Create a new persisted, user-scoped object that becomes the source of truth for each job.

It should contain:
- Role snapshot: company, title, URL, location, seniority, remote mode, detected archetype
- Evaluation summary: fit score, confidence, apply recommendation, weak-fit reason codes
- Evidence map: JD requirement -> exact resume/profile evidence or explicit gap
- Gap matrix: `hard_blocker | stretch | nice_to_have`, plus truthful mitigation options
- Positioning strategy: top narrative themes, seniority framing, summary direction, section emphasis
- Tailoring directives: what to change in summary, skills, projects, bullets, ordering, keywords
- Risk notes: authenticity risks, source-resume quality limits, missing metrics, unclear JD
- Approval state: draft, approved, rejected, override-approved
- Links to derived artifacts: tailored drafts, saved docs, tracker entries, later interview/apply outputs

Implementation shape:
- New SQLite table(s) in the existing local DB for `job_strategy_briefs`
- Optionally add strategy event/history table if needed
- Add optional foreign key linkage from `applications` to `job_strategy_brief_id`
- Add typed models for strategy briefs, directives, evidence links, gap records, and gate decisions

### 2. Replace “fit check then tailor” with a gated strategy workflow

The main product flow should become:

1. Load resume + JD
2. Analyze JD and classify role archetype
3. Build Job Strategy Brief
4. Stop for user review
5. If approved, tailor from the approved brief
6. Run review/validation
7. Stop for final approval
8. Save/track

Required behavior:
- Weak-fit roles stop after evaluation and require explicit override to continue
- Tailoring must consume the **approved** strategy brief, not raw JD text alone
- Every tailoring directive should be reviewable and individually disable-able before generation
- Unsupported JD keywords must be marked as “do not add” instead of being silently forced into the draft
- Re-tailoring the same job should reuse the same strategy brief unless the user regenerates it

### 3. Import the strongest evaluation ideas into structured form

The strategy brief should include these sections:
- Role Summary: archetype, domain, function, seniority, scope, quick recommendation
- Resume Match: requirement-by-requirement mapping to exact resume/profile evidence
- Gap Assessment: blocker vs stretch vs adjacent experience, with mitigation strategy
- Positioning Plan: how to frame the candidate truthfully for this role
- Tailoring Plan: concrete resume changes by section
- Interview Seeds: short list of likely stories/case-study angles to preserve for later use
- Job Viability Notes: only internal quality/risk signals from the JD itself, not web research

Do not pull in external comp or demand research in this phase.

### 4. Add a reusable candidate evidence layer

Extend the current user profile model beyond skills and metrics into a reusable evidence system.

Add a new user-scoped inventory for:
- Verified achievements and metrics
- Approved proof points and case studies
- Role-specific narratives
- Founder-to-employee or domain translation notes
- Interview story seeds

This layer should be human-curated and approval-based:
- Auto-suggestions are allowed
- Nothing enters the verified inventory without user confirmation
- Tailoring and future interview prep can only use verified or source-grounded evidence

### 5. Upgrade tailoring to be strategy-driven, not keyword-driven

Refactor tailoring prompts and pipeline logic so generation follows the approved brief.

New tailoring rules:
- Prioritize role-specific positioning themes over generic keyword insertion
- Rewrite only the sections/directives approved by the user
- Use gap mitigation policy explicitly: adjacent evidence allowed, fabricated equivalence forbidden
- Let the user lock bullets/sections before generation
- Record which strategy directives were applied to each draft

Add a new review surface:
- `strategy_alignment`: does the output actually reflect the approved strategy brief?

The review bundle should become:
- authenticity
- ats_parse
- job_match
- strategy_alignment
- editorial
- overall

### 6. Incrementally expand the UI without replacing the current app shape

Keep the current Tailor Resume experience, but add a new strategy layer around it.

UI changes:
- Add a Job Strategy stage before draft generation
- Show the brief as editable, sectioned cards rather than one opaque block
- Add clear hard gates:
  - weak fit stop screen
  - strategy approval screen
  - final resume approval screen
- Show evidence provenance inline for every important recommendation
- Let users disable or lock directives before tailoring
- Let users regenerate just one strategy section without restarting the whole workflow
- Preserve current refine-single-entry and protected-section behavior after draft generation

### 7. Phase broader features later

After the strategy brief exists and tailoring depends on it, add downstream features in this order:

Phase 2:
- Verified evidence/story inventory
- Resume variant defaults by archetype
- Better low-quality-source handling inside strategy generation
- Re-tailor from saved strategy brief

Phase 3:
- Interview prep pack generated from the saved strategy brief + verified evidence
- Application answer drafts tied to the same job brief
- Case-study recommendation and red-flag question prep

Phase 4:
- Job comparison across saved strategy briefs
- Tracker intelligence and pattern analysis
- Follow-up suggestions tied to application state

Phase 5:
- ATS PDF export
- Optional design-preserving export path
- Better resume packaging per role type

## Product Decisions That Were Already Chosen

These were explicitly selected and should not be reopened unless implementation proves they are unworkable:

- First major wave: **evaluation + tailoring strategy**
- UX direction: **incremental UI**, not a full app shell rewrite
- Human loop: **hard gates**
- Weak-fit handling: **stop and ask**
- Primary saved record per job: **Job Strategy Brief**
- Role focus for first version: **tech + adjacent roles**
- No need to preserve older workflow behavior for compatibility

## What Has Already Been Implemented On This Branch

Branch:
- `feat/strategy-brief-workflow`

Baseline checkpoint on `main` before this branch:
- `f285956` (`chore: checkpoint current resume-agent state`)

### Code already added or changed

1. `resume_agent/models/agent_models.py`
   - Added:
     - `StrategyDirective`
     - `RequirementEvidence`
     - `GapAssessment`
     - `JobStrategyBrief`
   - Extended `ReviewBundle` to include `strategy_alignment`
   - Extended workflow result model in `resume_workflow.py` to include:
     - `strategy_brief`
     - `strategy_brief_id`
     - `approval_stage`
   - Added new workflow step `BUILDING_STRATEGY`

2. `resume_agent/storage/user_store.py`
   - Added table creation for `job_strategy_briefs`
   - Added persistence helpers:
     - `save_job_strategy_brief_for_user`
     - `get_job_strategy_brief_for_user`
     - `update_job_strategy_brief_status_for_user`

3. `resume_agent/tracking/application_tracker.py`
   - Added `strategy_brief_id` column to applications
   - Updated add/update functions to store `strategy_brief_id`

4. `resume_agent/services/strategy_brief_service.py`
   - New file created
   - Includes:
     - LLM-driven brief generation
     - fallback archetype selection
     - fallback requirement evidence generation
     - fallback directives generation
     - persistence helper method

5. `resume_agent/review/bundle_builder.py`
   - Added `strategy_alignment` review section builder
   - Updated overall score composition to include strategy alignment
   - Updated `build_review_bundle(...)` signature to accept `strategy_brief`

6. `resume_agent/agents/review_agent.py`
   - Updated `review(...)` to accept `strategy_brief`
   - Passes strategy brief into `build_review_bundle(...)`

### Important note about current implementation state

The implementation is **in progress** and **not finished**.

The backend data model and some review plumbing are started, but the following major pieces are still incomplete:
- strategy brief integration into the main workflow service
- approval-stage split between strategy approval and final resume approval
- API serialization of strategy brief data
- UI rendering for strategy brief approval
- tailoring prompt/context integration with strategy brief
- tests for the new workflow

Also note:
- An `apply_patch` edit to `resume_tailor_agent.py` was started but interrupted by the user before completion.
- The next session should inspect the current file state before resuming edits there.

## What Still Needs To Be Implemented

### A. Finish tailoring agent integration

File:
- `resume_agent/agents/resume_tailor_agent.py`

Needs:
- Add optional `strategy_brief` input to `tailor(...)`
- Thread `strategy_brief` into `_build_tailoring_context(...)`
- Include approved directives, positioning strategy, and gap mitigation in the tailoring context
- Ensure only enabled directives are used
- Do not let strategy context cause unsupported keyword stuffing

Important behavior:
- Strategy brief should guide tailoring, not override truthfulness rules
- If strategy directives conflict with source-grounded evidence, truth wins

### B. Integrate strategy brief into `MultiAgentWorkflowService`

File:
- `resume_agent/services/multi_agent_workflow.py`

This is the most important missing piece.

Required changes:
1. Instantiate `StrategyBriefService`
2. After fit evaluation, create a strategy brief
3. Persist it if a local user exists
4. Store it on `TailorResumeResult`
5. If fit is weak, mark gating decision and stop for review rather than using the current poor-fit-only event path
6. Insert a new `BUILDING_STRATEGY` workflow stage
7. Make the first approval gate be **strategy approval**, not final resume approval
8. After strategy approval, tailoring should use the approved brief
9. Final validation/review should continue using the existing resume approval shell
10. When tracking the application, persist the `strategy_brief_id`

Also reconsider:
- whether the current `poor_fit_stopped` flow should be removed entirely and replaced by strategy-stage gating
- whether `approval_required` should now be stage-specific rather than a generic flag

Recommended direction:
- make approval stage explicit with values like `strategy` and `final_resume`
- stop carrying multiple overlapping gate concepts if a simpler state machine is possible

### C. Update API contracts in `api/main.py`

Needed work:
- Extend request/response serialization to include strategy brief data
- Add approval stage info to serialized results
- Update SSE events so the frontend can distinguish:
  - strategy approval required
  - final resume approval required
- Decide whether to keep one endpoint (`/api/tailor-resume`) with staged events or split out explicit strategy endpoints

Recommended direction:
- keep the existing main endpoint for now, but make its event model cleaner
- add stage-aware approval payloads instead of duplicating endpoints unnecessarily

Likely serialization additions:
- `strategy_brief`
- `strategy_brief_id`
- `approval_stage`
- `gating_decision`

### D. Update approval storage model if needed

Current approval storage stores `TailorResumeResult` as one blob.

That may still be fine, but check whether it now needs to safely support two distinct stages:
- strategy approval state
- final resume approval state

If the current structure can support that cleanly, keep it.
If not, refactor it rather than layering brittle stage hacks.

### E. Update frontend `TailorResume.jsx`

The existing UI already has an approval shell. Reuse it.

Required UI changes:
1. Add strategy brief rendering before resume generation
2. Show sections like:
   - role summary
   - requirement evidence
   - gaps
   - positioning strategy
   - tailoring directives
   - risk notes
3. Let users review directives before approval
4. Show weak-fit stop-and-ask UI at the strategy stage
5. After strategy approval, run tailoring and then show the existing resume approval flow
6. Extend review UI to show `strategy_alignment`
7. Update delta/review displays to include the new section

Recommended approach:
- keep the current page and state model
- add a new stage-specific panel rather than inventing a second page

### F. Rework event flow cleanly

Current events include:
- `poor_fit_stopped`
- `approval_required`
- `complete`

This likely needs cleanup.

Recommended direction:
- strategy gate should become the canonical pre-tailor gate
- avoid separate special-case event types if `approval_required` can include stage info
- do not keep multiple redundant workflow stop mechanisms unless clearly necessary

### G. Add tests

Files likely needing updates:
- `tests/test_review_bundle.py`
- `tests/test_multi_agent_workflow.py`
- `tests/test_workflow_integration.py`
- `tests/test_backend_api_integration.py`
- frontend workflow tests if present/relevant

Required test coverage:
1. Strategy brief generation returns usable structured content
2. Weak-fit roles stop at strategy approval stage
3. Strategy approval proceeds to tailoring
4. Final review bundle includes `strategy_alignment`
5. Tailoring uses enabled directives only
6. Applications persist `strategy_brief_id`
7. Resume approval still works after the workflow shift
8. No fabricated claims are introduced when strategy directives mention unsupported gaps

## Risks To Think About During Implementation

### 1. Over-complication of workflow state

Biggest risk: introducing too many states and branching conditions.

Avoid:
- one set of flags for fit stop
- another for strategy gate
- another for approval required
- another for preview state

Prefer:
- one explicit workflow stage model
- one explicit approval stage model
- one clean transition path

### 2. Strategy brief becoming just another LLM blob

If the brief is only opaque text, the whole value is lost.

The brief must stay structured enough that:
- UI can render it section by section
- directives can be toggled or at least selectively reasoned about
- later features can reuse it reliably

### 3. Keyword stuffing regression

If the strategy brief is naively passed into tailoring, it may worsen the exact problem this rework is supposed to solve.

The tailoring layer must keep these invariants:
- do not add unsupported technologies
- do not “satisfy” a directive by fabricating evidence
- use adjacent evidence carefully and explicitly

### 4. Review bundle drift

If `strategy_alignment` is added but not shown in the UI, the user loses value.
If it is shown but not factored into overall reasoning, it becomes cosmetic.

Keep backend review semantics and frontend rendering aligned.

### 5. Dirty branching / legacy branches

Do not leave both:
- old poor-fit stop flow
- and new strategy gate flow

unless there is a truly strong reason.

Default assumption should be: remove obsolete workflow branches if the new one supersedes them.

## Recommended Implementation Order From Here

1. Inspect current `resume_tailor_agent.py` and complete the interrupted strategy-brief integration safely.
2. Refactor `multi_agent_workflow.py` to introduce `BUILDING_STRATEGY` and stage-aware approval handling.
3. Update `api/main.py` serialization and SSE event payloads.
4. Update `TailorResume.jsx` to render and approve the strategy brief before tailoring.
5. Update review bundle UI for `strategy_alignment`.
6. Thread `strategy_brief_id` into tracking and final save/approve flows.
7. Add tests.
8. Run backend tests and targeted frontend checks.
9. Clean up obsolete workflow paths instead of leaving both systems alive.

## Acceptance Criteria

The implementation is complete when all of the following are true:

1. A normal tailoring run creates a strategy brief before any tailored resume draft exists.
2. The user must review and approve that strategy brief before tailoring proceeds.
3. Weak-fit roles default to stop-and-ask at the strategy stage.
4. Tailoring uses the approved strategy brief as explicit context.
5. Final review shows `strategy_alignment` alongside authenticity, ATS, job match, and editorial.
6. Final approval and save still work cleanly.
7. Application tracking stores the linked strategy brief id.
8. The new flow replaces the old one instead of sitting beside it as legacy fallback logic.
9. No fabrication safeguards regress.
10. The code is cleaner after the rework, not just more featureful.

## Final Guidance To The Next Session

When continuing this branch:
- read this document first
- inspect the current worktree to confirm the partial changes already present
- prefer simplifying the workflow over preserving old branches
- do not be conservative about removing obsolete compatibility paths
- treat regressions and truthfulness failures as first-class implementation risks
- if a better architecture emerges during implementation, take it, but keep the core product intent unchanged

The main idea to preserve is simple:

**`resume-agent` should stop being “generate a tailored resume from a JD” and become “generate and approve a job strategy first, then tailor from that approved strategy.”**
