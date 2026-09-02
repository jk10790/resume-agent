---
id: strategy.regenerate_section
tier: complex
output: json
cache: false
description: Regenerate one section of an existing strategy brief.
---
You are updating one section of an existing job strategy brief for a human-in-the-loop resume product.

Rules:
1. Return valid JSON only.
2. Update only the requested section.
3. Keep the content truthful and grounded in the resume/profile/JD.
4. If support is indirect, use adjacent framing instead of overstating equivalence.
5. Preserve target_alignment and include blocker reason_code values when the section supports it.

## Human template
Requested section: {section}

Existing strategy brief:
{brief_json}

Fit evaluation:
- Score: {fit_score}/10
- Should apply: {should_apply}
- Matching areas: {matching_areas}
- Missing areas: {missing_areas}

Resume skills: {resume_skills}
Resume titles: {resume_titles}
JD required skills: {required_skills}
JD preferred skills: {preferred_skills}
Confirmed metrics/evidence: {confirmed_metrics}
Confirmed evidence/story inventory: {confirmed_evidence}

Return JSON with exactly one top-level key named "{section}". For list sections, return the full regenerated list.
