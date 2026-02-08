"""
Resume Fixer Agent
Single responsibility: Fix specific validation errors in tailored resumes.
Does NOT modify anything else - only fixes the exact errors passed to it.
"""

from typing import List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

from ..services.llm_service import LLMService
from ..models.agent_models import ValidationIssue, Severity
from ..utils.logger import logger


class FixType(Enum):
    """Types of fixes this agent can perform"""
    REMOVE_FABRICATED_SKILL = "remove_fabricated_skill"
    REMOVE_FABRICATED_EXPERIENCE = "remove_fabricated_experience"
    RESTORE_EDUCATION = "restore_education"
    REMOVE_FABRICATED_CERTIFICATION = "remove_fabricated_certification"
    REMOVE_FABRICATED_METRIC = "remove_fabricated_metric"


@dataclass
class FixResult:
    """Result of a fix operation"""
    fixed_resume: str
    changes_made: List[str]
    errors_fixed: int
    errors_remaining: int


class ResumeFixerAgent:
    """
    Agent responsible for fixing validation errors in tailored resumes.
    
    SINGLE RESPONSIBILITY: Remove fabricated content and restore original information.
    
    This agent does NOT:
    - Add new content
    - Improve formatting
    - Enhance descriptions
    - Make any changes beyond fixing specific errors
    """
    
    def __init__(self, llm_service: LLMService):
        self.llm_service = llm_service
    
    def fix_errors(
        self,
        tailored_resume: str,
        original_resume: str,
        errors: List[ValidationIssue],
        user_skills: List[str] = None
    ) -> FixResult:
        """
        Fix ERROR-level validation issues in the tailored resume.
        
        Args:
            tailored_resume: The resume with errors to fix
            original_resume: Original resume for reference
            errors: List of validation issues (only ERRORs will be processed)
            user_skills: User's confirmed skills list
            
        Returns:
            FixResult with the fixed resume and list of changes
        """
        # Filter only ERROR-level issues
        error_issues = [
            issue for issue in errors 
            if issue.severity == Severity.ERROR or issue.severity == "error"
        ]
        
        if not error_issues:
            logger.info("Resume Fixer Agent: No errors to fix")
            return FixResult(
                fixed_resume=tailored_resume,
                changes_made=[],
                errors_fixed=0,
                errors_remaining=0
            )
        
        logger.info(
            "Resume Fixer Agent: Starting fix",
            error_count=len(error_issues)
        )
        
        # Categorize errors by type
        fabricated_skills = []
        fabricated_experience = []
        education_errors = []
        fabricated_metrics = []
        other_errors = []
        
        for error in error_issues:
            msg_lower = error.message.lower()
            if "fabricated technology" in msg_lower or "fabricated skill" in msg_lower:
                # Extract the skill name from the message
                skill = self._extract_skill_from_message(error.message)
                if skill:
                    fabricated_skills.append(skill)
            elif "fabricated experience" in msg_lower or "years of experience" in msg_lower:
                fabricated_experience.append(error.message)
            elif "degree" in msg_lower or "education" in msg_lower:
                education_errors.append(error.message)
            elif "unverified metric" in msg_lower or "metric" in msg_lower:
                metric = self._extract_metric_from_message(error.message)
                if metric:
                    fabricated_metrics.append(metric)
                else:
                    other_errors.append(error.message)
            else:
                other_errors.append(error.message)
        
        fixed_resume = tailored_resume
        changes_made = []
        
        # Fix fabricated skills (most common issue)
        if fabricated_skills:
            fixed_resume, skill_changes = self._remove_fabricated_skills(
                fixed_resume, fabricated_skills, user_skills or []
            )
            changes_made.extend(skill_changes)
        
        # Fix fabricated experience
        if fabricated_experience:
            fixed_resume, exp_changes = self._remove_fabricated_experience(
                fixed_resume, fabricated_experience, original_resume
            )
            changes_made.extend(exp_changes)
        
        # Fix education errors
        if education_errors:
            fixed_resume, edu_changes = self._restore_education(
                fixed_resume, education_errors, original_resume
            )
            changes_made.extend(edu_changes)

        # Fix fabricated metrics
        if fabricated_metrics:
            fixed_resume, metric_changes = self._remove_fabricated_metrics(
                fixed_resume, fabricated_metrics
            )
            changes_made.extend(metric_changes)
        
        # Handle other errors with generic fix
        if other_errors:
            fixed_resume, other_changes = self._fix_other_errors(
                fixed_resume, other_errors, original_resume
            )
            changes_made.extend(other_changes)
        
        logger.info(
            "Resume Fixer Agent: Fix complete",
            changes_made=len(changes_made)
        )
        
        return FixResult(
            fixed_resume=fixed_resume,
            changes_made=changes_made,
            errors_fixed=len(changes_made),
            errors_remaining=len(error_issues) - len(changes_made)
        )
    
    def _extract_skill_from_message(self, message: str) -> Optional[str]:
        """Extract skill name from error message like 'Fabricated technology/skill: Jenkins was added'"""
        import re
        # Pattern: "Fabricated technology/skill: SKILLNAME was added"
        match = re.search(r'Fabricated (?:technology|skill)[:/]\s*([^\s]+)\s+was', message, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        
        # Try another pattern: "skill: SKILLNAME"
        match = re.search(r'skill[:/]\s*([^\s,]+)', message, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        
        return None

    def _extract_metric_from_message(self, message: str) -> Optional[str]:
        """Extract metric text from message like 'Unverified metric: \"20 services\"'."""
        import re
        match = re.search(r'Unverified metric:\s*"([^"]+)"', message, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return None
    
    def _remove_fabricated_skills(
        self,
        resume: str,
        fabricated_skills: List[str],
        user_skills: List[str]
    ) -> Tuple[str, List[str]]:
        """Remove fabricated skills from resume using targeted LLM call."""
        from langchain_core.messages import SystemMessage, HumanMessage
        
        skills_to_remove = ", ".join(fabricated_skills)
        allowed_skills = ", ".join(user_skills) if user_skills else "Only skills from original resume"
        
        prompt = SystemMessage(content=f"""You are a RESUME SKILL REMOVER. Your ONLY job is to remove specific fabricated skills.

SKILLS TO REMOVE (delete ALL mentions of these):
{skills_to_remove}

ALLOWED SKILLS (keep these if present):
{allowed_skills}

RULES:
1. Find and REMOVE all mentions of the skills listed above
2. Remove entire bullet points or sentences that ONLY mention the removed skill
3. If a skill appears in a list, remove just that skill from the list
4. DO NOT add anything new
5. DO NOT change anything else
6. Preserve all formatting and structure

Return ONLY the modified resume text.""")
        
        human_prompt = HumanMessage(content=f"""Resume to fix:
---
{resume}
---

Remove the fabricated skills listed above. Return only the fixed resume.""")
        
        try:
            fixed = self.llm_service.invoke_with_retry([prompt, human_prompt])
            fixed = self._clean_response(fixed)
            changes = [f"Removed fabricated skill: {skill}" for skill in fabricated_skills]
            return fixed, changes
        except Exception as e:
            logger.error(f"Failed to remove fabricated skills: {e}")
            return resume, []
    
    def _remove_fabricated_experience(
        self,
        resume: str,
        experience_errors: List[str],
        original_resume: str
    ) -> Tuple[str, List[str]]:
        """Remove fabricated experience claims."""
        from langchain_core.messages import SystemMessage, HumanMessage
        import re
        
        errors_text = "\n".join(f"- {err}" for err in experience_errors)
        
        prompt = SystemMessage(content=f"""You are a RESUME EXPERIENCE CORRECTOR. Your ONLY job is to remove fabricated experience claims.

ERRORS TO FIX:
{errors_text}

RULES:
1. Remove ANY years of experience claims that were added (e.g., "8 years of experience")
2. If the original resume had specific experience years, those are fine to keep
3. Remove exaggerated experience claims
4. DO NOT add anything new
5. DO NOT change anything else
6. Preserve all formatting and structure

Return ONLY the modified resume text.""")
        
        human_prompt = HumanMessage(content=f"""Original resume (for reference):
---
{original_resume[:2000]}
---

Resume to fix:
---
{resume}
---

Remove the fabricated experience claims. Return only the fixed resume.""")
        
        try:
            fixed = self.llm_service.invoke_with_retry([prompt, human_prompt])
            fixed = self._clean_response(fixed)
            changes = ["Removed fabricated experience claims"]
            return fixed, changes
        except Exception as e:
            logger.error(f"Failed to remove fabricated experience: {e}")
            return resume, []

    def _remove_fabricated_metrics(
        self,
        resume: str,
        fabricated_metrics: List[str]
    ) -> Tuple[str, List[str]]:
        """Remove or soften fabricated metrics."""
        from langchain_core.messages import SystemMessage, HumanMessage

        metrics_text = "\n".join(f"- {m}" for m in fabricated_metrics)

        prompt = SystemMessage(content=f"""You are a RESUME METRIC CLEANER. Your ONLY job is to remove specific unverified numeric claims.

METRICS TO REMOVE OR SOFTEN:
{metrics_text}

RULES:
1. Remove the numeric values for these metrics or rewrite the line qualitatively without numbers
2. Do NOT remove or change other valid numbers in the resume
3. Preserve formatting and structure
4. Do NOT add new information

Return ONLY the modified resume text.""")

        human_prompt = HumanMessage(content=f"""Resume to fix:
---
{resume}
---

Remove or soften ONLY the listed metrics. Return only the fixed resume.""")

        try:
            fixed = self.llm_service.invoke_with_retry([prompt, human_prompt])
            fixed = self._clean_response(fixed)
            changes = [f"Removed unverified metric: {metric}" for metric in fabricated_metrics]
            return fixed, changes
        except Exception as e:
            logger.error(f"Failed to remove fabricated metrics: {e}")
            return resume, []
    
    def _restore_education(
        self,
        resume: str,
        education_errors: List[str],
        original_resume: str
    ) -> Tuple[str, List[str]]:
        """Restore original education information."""
        from langchain_core.messages import SystemMessage, HumanMessage
        import re
        
        # Extract education section from original resume
        edu_pattern = r'(education|academic|degree).*?(?=\n\n[A-Z]|\n\n\n|$)'
        edu_match = re.search(edu_pattern, original_resume, re.IGNORECASE | re.DOTALL)
        original_education = edu_match.group(0) if edu_match else "Not found"
        
        errors_text = "\n".join(f"- {err}" for err in education_errors)
        
        prompt = SystemMessage(content=f"""You are a RESUME EDUCATION RESTORER. Your ONLY job is to restore original education information.

ERRORS TO FIX:
{errors_text}

ORIGINAL EDUCATION SECTION:
{original_education}

RULES:
1. Replace the education section with the EXACT information from the original
2. Keep degree names, fields of study, and institutions exactly as in original
3. DO NOT add anything new
4. DO NOT change anything else outside the education section
5. Preserve all formatting and structure

Return ONLY the modified resume text.""")
        
        human_prompt = HumanMessage(content=f"""Resume to fix:
---
{resume}
---

Restore the original education information. Return only the fixed resume.""")
        
        try:
            fixed = self.llm_service.invoke_with_retry([prompt, human_prompt])
            fixed = self._clean_response(fixed)
            changes = ["Restored original education information"]
            return fixed, changes
        except Exception as e:
            logger.error(f"Failed to restore education: {e}")
            return resume, []
    
    def _fix_other_errors(
        self,
        resume: str,
        other_errors: List[str],
        original_resume: str
    ) -> Tuple[str, List[str]]:
        """Generic fix for other error types."""
        from langchain_core.messages import SystemMessage, HumanMessage
        
        errors_text = "\n".join(f"- {err}" for err in other_errors)
        
        prompt = SystemMessage(content=f"""You are a RESUME ERROR FIXER. Fix ONLY the specific errors listed.

ERRORS TO FIX:
{errors_text}

RULES:
1. Fix ONLY the specific errors listed above
2. If something was added that wasn't in original, REMOVE it
3. DO NOT add anything new
4. DO NOT change anything else
5. Preserve all formatting and structure

Return ONLY the modified resume text.""")
        
        human_prompt = HumanMessage(content=f"""Original resume (for reference):
---
{original_resume[:2000]}
---

Resume to fix:
---
{resume}
---

Fix the errors listed above. Return only the fixed resume.""")
        
        try:
            fixed = self.llm_service.invoke_with_retry([prompt, human_prompt])
            fixed = self._clean_response(fixed)
            changes = [f"Fixed: {err[:50]}..." for err in other_errors]
            return fixed, changes
        except Exception as e:
            logger.error(f"Failed to fix other errors: {e}")
            return resume, []
    
    def _clean_response(self, response: str) -> str:
        """Clean up LLM response, removing any markdown formatting."""
        response = response.strip()
        
        # Remove markdown code blocks
        if response.startswith("```"):
            lines = response.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            response = "\n".join(lines)
        
        return response.strip()
