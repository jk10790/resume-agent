"""Prompts live in `library/` as one Markdown file per task.

Load them through `resume_agent.llm.tasks`, which reads each file's frontmatter
for the tier, output format and cache policy alongside the prompt text.
"""
