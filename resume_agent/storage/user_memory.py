# user_memory.py
"""
User and resume-related persistence. Uses storage.memory for raw file I/O.
Single backing file; default structure includes user_skills and project_mentions.
"""

from .memory import load_memory as _raw_load_memory, save_memory as _raw_save_memory, clear_memory as _clear_memory_file


def _ensure_structure(memory: dict) -> dict:
    """Ensure default keys exist for user memory."""
    memory.setdefault("user_skills", [])
    memory.setdefault("project_mentions", {})
    return memory


def load_memory():
    """Load memory with default structure (user_skills, project_mentions)."""
    data = _raw_load_memory()
    return _ensure_structure(data)


def save_memory(memory: dict):
    """Save memory; ensures default structure before writing."""
    _ensure_structure(memory)
    _raw_save_memory(memory)


def clear_memory():
    """Remove the memory file from disk (full reset)."""
    _clear_memory_file()

def has_skill(skill):
    memory = load_memory()
    return skill.lower() in [s.lower() for s in memory["user_skills"]]

def add_skill(skill):
    memory = load_memory()
    if skill not in memory["user_skills"]:
        memory["user_skills"].append(skill)
        save_memory(memory)

def get_skills():
    return load_memory().get("user_skills", [])


def remove_skill(skill: str) -> bool:
    """Remove a skill from the list (case-insensitive)"""
    memory = load_memory()
    user_skills = memory.get("user_skills", [])
    original_count = len(user_skills)
    
    # Remove skill (case-insensitive)
    user_skills = [s for s in user_skills if s.lower() != skill.lower()]
    memory["user_skills"] = user_skills
    save_memory(memory)
    
    return len(user_skills) < original_count  # True if something was removed


def update_skill(old_skill: str, new_skill: str) -> bool:
    """Update/rename a skill (case-insensitive match)"""
    memory = load_memory()
    user_skills = memory.get("user_skills", [])
    
    # Find and replace (case-insensitive)
    updated = False
    for i, s in enumerate(user_skills):
        if s.lower() == old_skill.lower():
            user_skills[i] = new_skill
            updated = True
            break
    
    if updated:
        memory["user_skills"] = user_skills
        save_memory(memory)
    
    return updated


def reset_skills():
    """Clear all skills"""
    memory = load_memory()
    memory["user_skills"] = []
    save_memory(memory)


def set_skills(skills: list):
    """Replace all skills with a new list (bulk update)"""
    memory = load_memory()
    # Deduplicate and clean
    unique_skills = []
    seen = set()
    for skill in skills:
        skill_clean = skill.strip()
        if skill_clean and skill_clean.lower() not in seen:
            unique_skills.append(skill_clean)
            seen.add(skill_clean.lower())
    
    memory["user_skills"] = unique_skills
    save_memory(memory)
    return unique_skills


# ============================================
# Improved Resume Cache
# ============================================

def save_improved_resume(resume_text: str, original_doc_id: str = None, score: int = 0, metadata: dict = None):
    """
    Cache an improved resume for later retrieval.
    
    Args:
        resume_text: The improved resume text
        original_doc_id: The Google Doc ID of the original resume
        score: Quality score after improvement
        metadata: Additional metadata (changes made, etc.)
    """
    from datetime import datetime
    
    memory = load_memory()
    
    if "improved_resumes" not in memory:
        memory["improved_resumes"] = {}
    
    # Use original_doc_id as key, or 'latest' if none
    key = original_doc_id or "latest"
    
    memory["improved_resumes"][key] = {
        "text": resume_text,
        "score": score,
        "original_doc_id": original_doc_id,
        "metadata": metadata or {},
        "updated_at": datetime.now().isoformat(),
        "version": memory["improved_resumes"].get(key, {}).get("version", 0) + 1
    }
    
    # Also store as 'latest' for quick access
    memory["improved_resumes"]["latest"] = memory["improved_resumes"][key]
    
    save_memory(memory)
    return memory["improved_resumes"][key]


def get_improved_resume(doc_id: str = None) -> dict:
    """
    Get cached improved resume.
    
    Args:
        doc_id: Original doc ID, or None for latest
        
    Returns:
        Dict with text, score, metadata, etc. or None if not found
    """
    memory = load_memory()
    improved_resumes = memory.get("improved_resumes", {})
    
    if doc_id:
        return improved_resumes.get(doc_id)
    return improved_resumes.get("latest")


def get_improved_resume_history(doc_id: str = None) -> list:
    """Get all cached improved resumes"""
    memory = load_memory()
    improved_resumes = memory.get("improved_resumes", {})
    
    if doc_id:
        resume = improved_resumes.get(doc_id)
        return [resume] if resume else []
    
    # Return all except 'latest' (which is a duplicate)
    return [v for k, v in improved_resumes.items() if k != "latest"]


def clear_improved_resume(doc_id: str = None):
    """Clear cached improved resume(s)"""
    memory = load_memory()
    
    if "improved_resumes" not in memory:
        return
    
    if doc_id:
        memory["improved_resumes"].pop(doc_id, None)
        # If we cleared the doc that was 'latest', clear that too
        latest = memory["improved_resumes"].get("latest", {})
        if latest.get("original_doc_id") == doc_id:
            memory["improved_resumes"].pop("latest", None)
    else:
        memory["improved_resumes"] = {}
    
    save_memory(memory)


# ============================================
# Resume Quality Cache
# ============================================

def save_quality_report(doc_id: str, report: dict):
    """Cache a resume quality report for later use."""
    from datetime import datetime
    memory = load_memory()

    if "quality_reports" not in memory:
        memory["quality_reports"] = {}

    key = doc_id or "latest"
    memory["quality_reports"][key] = {
        "report": report,
        "doc_id": doc_id,
        "updated_at": datetime.now().isoformat()
    }
    memory["quality_reports"]["latest"] = memory["quality_reports"][key]
    save_memory(memory)
    return memory["quality_reports"][key]


def get_quality_report(doc_id: str = None) -> dict:
    """Get cached resume quality report."""
    memory = load_memory()
    quality_reports = memory.get("quality_reports", {})
    if doc_id:
        return quality_reports.get(doc_id)
    return quality_reports.get("latest")


def clear_quality_report(doc_id: str = None):
    """Clear cached resume quality report(s)."""
    memory = load_memory()
    if "quality_reports" not in memory:
        return

    if doc_id:
        memory["quality_reports"].pop(doc_id, None)
        latest = memory["quality_reports"].get("latest", {})
        if latest.get("doc_id") == doc_id:
            memory["quality_reports"].pop("latest", None)
    else:
        memory["quality_reports"] = {}
    save_memory(memory)
