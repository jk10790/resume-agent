---
id: discover.enrich
tier: simple
description: Summarise a batch of discovered postings without judging fit.
---
Return valid JSON only as an array with one object per posting.
Describe the role only. Do not evaluate candidate fit. Do not recommend whether to apply.
Do not invent company facts. Do not add blockers not grounded in the page text.

## Human template
Return JSON array with keys id, tldr, archetype, seniority, remote_mode, possible_blockers.

{postings}
