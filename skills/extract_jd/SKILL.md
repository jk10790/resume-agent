---
name: extract_jd
description: Extracts and cleans the job description from raw scraped web text or URL.
model_hint: haiku
---

# Extract job description

You are a helpful AI assistant. Given raw text scraped from a job listing webpage, extract and return only the job description and requirements section. Ignore headers, navigation, menus, footers, social links, etc.

## Human template

Raw scraped text (first portion; may be truncated):

```
{raw_text}
```

Return only the cleaned job description and requirements.
