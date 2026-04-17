"""
Structured in-memory resume document model for analysis and targeted rewrites.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Optional
import hashlib
import re

from .resume_parser import parse_resume_sections


@dataclass
class ResumeEntry:
    id: str
    section_name: str
    kind: str
    text: str


@dataclass
class ResumeSectionDocument:
    name: str
    title: str
    entries: List[ResumeEntry] = field(default_factory=list)


@dataclass
class ResumeDocument:
    sections: List[ResumeSectionDocument] = field(default_factory=list)

    def iter_entries(self) -> Iterable[ResumeEntry]:
        for section in self.sections:
            for entry in section.entries:
                yield entry

    def find_entry_by_text(self, text: str) -> Optional[ResumeEntry]:
        needle = (text or "").strip()
        if not needle:
            return None
        for entry in self.iter_entries():
            if entry.text.strip() == needle:
                return entry
        return None

    def find_entry_by_id(self, entry_id: str) -> Optional[ResumeEntry]:
        if not entry_id:
            return None
        for entry in self.iter_entries():
            if entry.id == entry_id:
                return entry
        return None

    def render(self) -> str:
        parts: List[str] = []
        for section in self.sections:
            if section.name != "header":
                parts.append(section.title)
            parts.extend(entry.text for entry in section.entries if entry.text.strip())
            parts.append("")
        return "\n".join(parts).strip()


def parse_resume_document(resume_text: str) -> ResumeDocument:
    sections = parse_resume_sections(resume_text)
    if not sections:
        return ResumeDocument(
            sections=[
                ResumeSectionDocument(
                    name="document",
                    title="",
                    entries=_lines_to_entries("document", [line for line in resume_text.splitlines() if line.strip()]),
                )
            ]
        )

    ordered_sections = sorted(sections.values(), key=lambda item: item.start_index)
    document_sections: List[ResumeSectionDocument] = []
    for section in ordered_sections:
        lines = [line.rstrip() for line in section.content.splitlines() if line.strip()]
        title = "" if section.name == "header" else f"**{section.name.upper()}**"
        document_sections.append(
            ResumeSectionDocument(
                name=section.name,
                title=title,
                entries=_lines_to_entries(section.name, lines),
            )
        )
    return ResumeDocument(sections=document_sections)


def _lines_to_entries(section_name: str, lines: List[str]) -> List[ResumeEntry]:
    entries: List[ResumeEntry] = []
    for index, line in enumerate(lines):
        kind = "bullet" if re.match(r"^\s*[-•*]\s+", line) else "paragraph"
        stable = f"{section_name}|{index}|{line.strip()}"
        entry_id = hashlib.sha1(stable.encode("utf-8")).hexdigest()[:12]
        entries.append(
            ResumeEntry(
                id=f"{section_name}-{entry_id}",
                section_name=section_name,
                kind=kind,
                text=line.strip(),
            )
        )
    return entries
