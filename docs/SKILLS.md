# Prompts and tasks

Every LLM call this service makes is a **task**: one Markdown file in
`resume_agent/prompts/library/`. There is no second home for prompt text.

## Anatomy of a task file

```markdown
---
id: fit.evaluate
tier: complex
output: json
cache: true
mcp_exposed: true
description: Judge how well a resume matches a job description.
---
You are a FIT EVALUATOR. ...

## Human template
RESUME:
---
{resume_excerpt}
---
```

| Frontmatter | Meaning |
|---|---|
| `id` | How code refers to it: `llm_service.run_task("fit.evaluate", ...)`. Defaults to the filename. |
| `tier` | **Required.** `simple`, `standard` or `complex` — the cost/capability band. Maps to `TAUT_TIER_*` in `.env`. |
| `output` | `json` when the caller parses structured output; otherwise text. |
| `cache` | `false` for anything generative, so "regenerate" produces a new draft. |
| `mcp_exposed` | `true` to offer the task as a Claude Agent SDK tool. |
| `description` | One line; also the MCP tool description. |

Both the system prompt and the human template are `.format()`-ed with the
variables the caller passes, so **literal braces must be doubled** (`{{` / `}}`).
A JSON schema in a prompt needs this. A missing variable raises rather than
silently rendering an empty section.

## Adding a task

1. Write `resume_agent/prompts/library/<id>.md` with frontmatter and body.
2. Call it: `llm_service.run_task("<id>", var=value, ...)`.

That is all — no manifest to update. `tests/test_task_registry.py` covers every
task in the library automatically, including the tier and cache-policy rules.

## Why the tier is required

The routing provider will otherwise classify a call by prompt length and keyword
hits. That heuristic saturates at the same score as its "simple" threshold, so
whole-resume rewrites were landing on the cheap tier while shorter extraction
calls did not. Declaring the tier makes model choice a property of the work.

`test_work_that_writes_the_resume_runs_on_the_expensive_tier` and
`test_mechanical_extraction_runs_on_the_cheap_tier` enforce the policy.

## The rule about numbers

No prompt may show a worked example containing an invented figure. A prompt that
demonstrates `"Improved response time by 40%"` teaches the model to invent one,
whatever prohibition follows it — this is where fabricated metrics came from.
`test_no_prompt_asks_for_a_metric_it_cannot_verify` fails the build on the known
templates. The deterministic backstop is
`resume_agent/validation/rules.py::check_metric_provenance`, which flags any
number in a draft that is not in the source resume or the user's confirmed
metrics.

## Pipelines

Tasks are composed by the two pipelines in
`resume_agent/pipelines/definitions.py`, run through
`ResumeOrchestrator`. Reading those two lists tells you what work happens, in
what order, and what each step needs.
