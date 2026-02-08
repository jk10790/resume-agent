"""
Resume Parser
Parses resumes into structured sections for section-by-section tailoring.
"""

from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
import re
from ..utils.logger import logger


@dataclass
class ResumeSection:
    """A section of a resume"""
    name: str
    content: str
    start_index: int
    end_index: int
    level: int  # Heading level (1, 2, 3)


def parse_resume_sections(resume_text: str) -> Dict[str, ResumeSection]:
    """
    Parse a resume into named sections.
    
    Common sections:
    - Header/Contact (name, email, phone, location)
    - Summary/Objective
    - Experience/Work Experience
    - Education
    - Skills
    - Projects
    - Certifications
    - Awards
    - Publications
    - Languages
    
    Returns:
        Dict mapping section names to ResumeSection objects
    """
    sections = {}
    lines = resume_text.split('\n')
    
    # Find header (first few lines with name, contact info)
    header_end = _find_header_end(lines)
    if header_end > 0:
        sections['header'] = ResumeSection(
            name='header',
            content='\n'.join(lines[:header_end]),
            start_index=0,
            end_index=header_end,
            level=0
        )
    
    # Find all headings (markdown # or plain text headings)
    current_section_name = None
    current_section_start = header_end
    current_section_lines = []
    
    i = header_end
    while i < len(lines):
        line = lines[i].strip()
        
        # Check for markdown heading
        heading_match = re.match(r'^(#{1,3})\s+(.+)$', line)
        if heading_match:
            # Save previous section
            if current_section_name and current_section_lines:
                sections[current_section_name] = ResumeSection(
                    name=current_section_name,
                    content='\n'.join(current_section_lines).strip(),
                    start_index=current_section_start,
                    end_index=i,
                    level=len(heading_match.group(1))
                )
            
            # Start new section
            section_name = _normalize_section_name(heading_match.group(2))
            current_section_name = section_name
            current_section_start = i
            current_section_lines = []
            i += 1
            continue
        
        # Check for plain text heading (all caps, short line, followed by content)
        if _is_plain_heading(line, lines, i):
            # Save previous section
            if current_section_name and current_section_lines:
                sections[current_section_name] = ResumeSection(
                    name=current_section_name,
                    content='\n'.join(current_section_lines).strip(),
                    start_index=current_section_start,
                    end_index=i,
                    level=2
                )
            
            # Start new section
            section_name = _normalize_section_name(line)
            current_section_name = section_name
            current_section_start = i
            current_section_lines = []
            i += 1
            continue
        
        # Add line to current section
        if current_section_name:
            current_section_lines.append(lines[i])
        i += 1
    
    # Save last section
    if current_section_name and current_section_lines:
        sections[current_section_name] = ResumeSection(
            name=current_section_name,
            content='\n'.join(current_section_lines).strip(),
            start_index=current_section_start,
            end_index=len(lines),
            level=2
        )
    
    logger.info("Parsed resume sections", sections=list(sections.keys()))
    return sections


def _find_header_end(lines: List[str]) -> int:
    """Find where the header section ends"""
    from ..config import settings
    # Header typically has: name, email, phone, location, maybe LinkedIn/GitHub
    # Usually ends before first major section or after configured max lines
    max_header_lines = settings.resume_header_max_lines
    email_pattern = r'[\w\.-]+@[\w\.-]+\.\w+'
    phone_pattern = r'[\d\s\-\(\)\+]+'
    
    for i, line in enumerate(lines[:max_header_lines]):
        stripped = line.strip()
        # If we find a clear section marker, header ended before this
        if re.match(r'^#{1,3}\s+', stripped):
            return i
        if stripped.upper() in ['EXPERIENCE', 'EDUCATION', 'SKILLS', 'SUMMARY', 'OBJECTIVE']:
            return i
    
    # Look for email/phone patterns to determine header
    for i in range(min(max_header_lines, len(lines))):
        if re.search(email_pattern, lines[i]) or re.search(phone_pattern, lines[i]):
            # Header likely continues for a few more lines
            continue
        if i > 3 and not any(re.search(email_pattern, line) or re.search(phone_pattern, line) 
                             for line in lines[max(0, i-2):i+1]):
            return i
    
    return min(max_header_lines, len(lines))


def _normalize_section_name(name: str) -> str:
    """Normalize section name to standard format"""
    from ..config import settings
    import json
    
    name = name.strip().lower()
    
    # Load mappings from config if provided, otherwise use defaults
    if settings.resume_section_name_mappings:
        try:
            mappings = json.loads(settings.resume_section_name_mappings)
        except json.JSONDecodeError:
            logger.warning("Invalid JSON in RESUME_SECTION_MAPPINGS, using defaults")
            mappings = _get_default_section_mappings()
    else:
        mappings = _get_default_section_mappings()
    
    return mappings.get(name, name)


def _get_default_section_mappings() -> Dict[str, str]:
    """Get default section name mappings"""
    return {
        'work experience': 'experience',
        'professional experience': 'experience',
        'employment': 'experience',
        'employment history': 'experience',
        'work history': 'experience',
        'career history': 'experience',
        'professional summary': 'summary',
        'summary': 'summary',
        'objective': 'summary',
        'profile': 'summary',
        'about': 'summary',
        'technical skills': 'skills',
        'core competencies': 'skills',
        'competencies': 'skills',
        'qualifications': 'skills',
        'academic background': 'education',
        'academic qualifications': 'education',
        'certification': 'certifications',
        'certificate': 'certifications',
        'award': 'awards',
        'honor': 'awards',
        'achievement': 'awards',
        'publication': 'publications',
        'paper': 'publications',
        'language': 'languages',
        'language skills': 'languages',
    }


def _is_plain_heading(line: str, all_lines: List[str], index: int) -> bool:
    """Check if a line is a plain text heading (not markdown)"""
    if not line:
        return False
    
    # All caps and short (likely a heading)
    if line.isupper() and len(line) < 50 and len(line.split()) < 5:
        # Check if next line has content (not another heading)
        if index + 1 < len(all_lines):
            next_line = all_lines[index + 1].strip()
            if next_line and not next_line.isupper():
                return True
    
    # Title case with common section names
    title_case_patterns = [
        r'^(Experience|Education|Skills|Summary|Objective|Projects|Certifications|Awards|Languages|Publications)$',
    ]
    for pattern in title_case_patterns:
        if re.match(pattern, line, re.IGNORECASE):
            return True
    
    return False


def merge_resume_sections(
    original_sections: Dict[str, ResumeSection],
    tailored_sections: Dict[str, ResumeSection],
    sections_to_tailor: List[str]
) -> str:
    """
    Merge original and tailored sections based on selection.
    
    Args:
        original_sections: Original resume sections
        tailored_sections: Tailored resume sections
        sections_to_tailor: List of section names to use from tailored (others use original)
    
    Returns:
        Merged resume text
    """
    # Determine order of sections (use original order)
    section_order = list(original_sections.keys())
    
    # Add any new sections from tailored that aren't in original
    for section_name in tailored_sections:
        if section_name not in section_order:
            section_order.append(section_name)
    
    # Build merged resume
    merged_lines = []
    
    for section_name in section_order:
        # Decide which version to use
        if section_name in sections_to_tailor and section_name in tailored_sections:
            section = tailored_sections[section_name]
        elif section_name in original_sections:
            section = original_sections[section_name]
        else:
            continue
        
        # Add section heading
        if section.level > 0:
            heading_prefix = '#' * section.level + ' '
            merged_lines.append(heading_prefix + section.name.capitalize())
        else:
            # Header doesn't need heading
            pass
        
        # Add section content
        if section.content:
            merged_lines.append(section.content)
        
        merged_lines.append('')  # Blank line between sections
    
    return '\n'.join(merged_lines).strip()
