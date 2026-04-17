"""
Resume Quality Validator
Validates tailored resumes for quality, JD coverage, and ATS optimization.
"""

from typing import Any, Dict, List, Optional, Tuple
from ..services.llm_service import LLMService
from ..utils.logger import logger
from ..models.agent_models import ValidationIssue, ResumeValidation, Severity
from ..utils.metrics import MetricMatch, extract_metrics, extract_metrics_from_memory, normalize_metric_set
import json
import re


def calculate_ats_score(resume_text: str) -> float:
    """
    Calculate ATS (Applicant Tracking System) compatibility score.
    
    Checks:
    - Proper formatting (no complex tables, graphics)
    - Standard section headings
    - Keyword density
    - File format compatibility indicators
    
    Uses configuration from settings for all thresholds and penalties.
    
    Returns:
        ATS score (0-100)
    """
    from ..config import settings
    
    # Start with a more realistic base score (not 100)
    score = 75.0  # Base score for a decently formatted resume
    
    # Check for problematic formatting
    if re.search(r'<table|<img|<graphic', resume_text, re.IGNORECASE):
        score -= settings.ats_table_penalty
    
    # Check for standard section headings
    standard_sections = ['experience', 'education', 'skills', 'summary', 'objective']
    found_sections = sum(1 for section in standard_sections if re.search(rf'\b{section}\b', resume_text, re.IGNORECASE))
    if found_sections < 2:
        score -= settings.ats_missing_sections_penalty
    elif found_sections >= 4:
        score += 5  # Bonus for having most standard sections
    
    # Check keyword density (should be reasonable, not keyword stuffing)
    words = resume_text.lower().split()
    word_count = len(words)
    if word_count < settings.resume_min_words:
        score -= settings.ats_short_penalty
    elif word_count > settings.resume_max_words:
        score -= settings.ats_long_penalty
    elif settings.resume_recommended_min_words <= word_count <= settings.resume_recommended_max_words:
        score += 5  # Bonus for optimal length
    
    # Check for proper structure (has name/header)
    if not re.search(r'^[A-Z][a-z]+\s+[A-Z]', resume_text[:200]):
        score -= settings.ats_missing_header_penalty
    else:
        score += 3  # Bonus for proper header
    
    # Check for contact information
    has_email = bool(re.search(r'[\w\.-]+@[\w\.-]+\.\w+', resume_text))
    has_phone = bool(re.search(r'[\d\s\-\(\)\+]{10,}', resume_text))
    if not has_email or not has_phone:
        score -= settings.ats_missing_contact_penalty
    else:
        score += 2  # Bonus for complete contact info
    
    # Avoid rewarding unverified metrics to prevent fabrication
    
    # Check for action verbs (bonus)
    action_verbs = ['developed', 'implemented', 'designed', 'managed', 'led', 'created', 'improved', 'optimized']
    action_verb_count = sum(1 for verb in action_verbs if re.search(rf'\b{verb}\b', resume_text, re.IGNORECASE))
    if action_verb_count >= 3:
        score += 2  # Bonus for good action verbs
    
    return max(0, min(100, score))


def validate_resume_quality(
    llm_service: LLMService,
    original_resume: str,
    tailored_resume: str,
    jd_text: str,
    user_skills: Optional[List[str]] = None,
    verified_metric_records: Optional[List[Dict[str, Any]]] = None,
) -> ResumeValidation:
    """
    Validate a tailored resume for quality, JD coverage, and issues.
    Uses multiple specialized LLM calls for better accuracy.
    
    Args:
        llm_service: LLMService instance
        original_resume: Original resume text
        tailored_resume: Tailored resume text
        jd_text: Job description text
    
    Returns:
        ResumeValidation with quality score and issues
    """
    from langchain_core.messages import SystemMessage, HumanMessage
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    if user_skills is None:
        from ..storage.user_memory import get_skills
        user_skills = get_skills()
    
    # Basic validation first (now includes original resume and user skills for comparison)
    basic_issues = _basic_validation(tailored_resume, jd_text, original_resume, user_skills)
    
    # Run specialized validations in parallel for better accuracy
    all_issues = basic_issues.copy()
    jd_coverage = {}
    recommendations = []
    
    # Validation 1: Skill/Technology Authenticity Check
    skill_validation_issues = _validate_skill_authenticity(
        llm_service, original_resume, tailored_resume, user_skills
    )
    all_issues.extend(skill_validation_issues)
    
    # Validation 2: Experience/Qualification Consistency Check
    experience_validation_issues = _validate_experience_consistency(
        llm_service, original_resume, tailored_resume
    )
    all_issues.extend(experience_validation_issues)
    
    # Validation 3: JD Coverage Check
    jd_coverage_result = _validate_jd_coverage(
        llm_service, tailored_resume, jd_text
    )
    jd_coverage.update(jd_coverage_result.get("coverage", {}))
    all_issues.extend(jd_coverage_result.get("issues", []))
    
    # Validation 4: Format and Structure Check
    format_validation_issues = _validate_format_structure(
        llm_service, tailored_resume
    )
    all_issues.extend(format_validation_issues)

    # Validation 5: Metric provenance (numbers must exist in original or user-provided metrics)
    metric_issues, metric_provenance = _validate_metric_provenance(
        original_resume, tailored_resume, verified_metric_records=verified_metric_records
    )
    all_issues.extend(metric_issues)
    
    # Calculate quality score based on issues
    error_count = len([i for i in all_issues if i.severity == "error"])
    warning_count = len([i for i in all_issues if i.severity == "warning"])
    
    # Start with base score and deduct for issues
    quality_score = 100
    quality_score -= error_count * 15  # Heavy penalty for errors
    quality_score -= warning_count * 5  # Moderate penalty for warnings
    quality_score = max(0, min(100, quality_score))
    
    # Calculate ATS score
    ats_score = calculate_ats_score(tailored_resume)
    
    # Generate recommendations from all validations
    if error_count > 0:
        recommendations.append("Fix critical errors before submitting resume")
    if warning_count > 3:
        recommendations.append("Address multiple warnings to improve resume quality")
    
    # Calculate length metrics
    from ..config import settings
    word_count = len(tailored_resume.split())
    length_check = {
        "word_count": word_count,
        "char_count": len(tailored_resume),
        "is_reasonable": settings.resume_recommended_min_words <= word_count <= settings.resume_recommended_max_words,
        "recommended_range": f"{settings.resume_recommended_min_words}-{settings.resume_recommended_max_words} words"
    }
    
    return ResumeValidation(
        quality_score=quality_score,
        is_valid=error_count == 0,
        issues=all_issues,
        jd_coverage=jd_coverage,
        keyword_density=0.0,  # Could be calculated separately
        length_check=length_check,
        recommendations=recommendations,
        ats_score=ats_score,
        metric_provenance=metric_provenance
    )


def _validate_skill_authenticity(
    llm_service: LLMService,
    original_resume: str,
    tailored_resume: str,
    user_skills: list
) -> List[ValidationIssue]:
    """Specialized validation: Check if skills/technologies were fabricated"""
    from langchain_core.messages import SystemMessage, HumanMessage
    
    skills_list = ", ".join(user_skills) if user_skills else "None"
    
    prompt = SystemMessage(content=f"""You are a resume authenticity validator. Your ONLY job is to check if the tailored resume added technologies, tools, frameworks, or skills that were NOT in the original resume.

CRITICAL RULES:
- If a technology/tool/skill appears in the tailored resume but NOT in the original resume, it's FABRICATED
- ONLY exception: Skills in the user's confirmed list can be added (even if not in original)
- User's confirmed skills: {skills_list}

Respond with JSON only:
{{
    "fabricated_items": [
        {{
            "item": "<technology/tool/skill name>",
            "severity": "error",
            "message": "Fabricated technology/skill: <name> was added but not in original resume",
            "suggestion": "Remove this fabricated item. Only use technologies/skills from the original resume or confirmed skills list."
        }}
    ]
}}""")
    
    human_prompt = HumanMessage(content=f"""Original Resume:
---
{original_resume[:3000]}

Tailored Resume:
---
{tailored_resume[:3000]}

User's Confirmed Skills: {skills_list}

Compare the two resumes. List ALL technologies, tools, frameworks, programming languages, databases, cloud platforms, or skills that appear in the tailored resume but NOT in the original resume (excluding user's confirmed skills).""")
    
    try:
        response = llm_service.invoke_with_retry([prompt, human_prompt])
        import json
        import re
        
        # Parse JSON response
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group(0))
            issues = []
            for item in data.get("fabricated_items", []):
                issues.append(ValidationIssue(
                    severity=Severity(item.get("severity", "error")),
                    category="consistency",
                    message=item.get("message", ""),
                    suggestion=item.get("suggestion", "")
                ))
            return issues
    except Exception as e:
        logger.warning(f"Skill authenticity validation failed: {e}")
    
    return []


def _validate_experience_consistency(
    llm_service: LLMService,
    original_resume: str,
    tailored_resume: str
) -> List[ValidationIssue]:
    """Specialized validation: Check if experience/qualifications were fabricated"""
    from langchain_core.messages import SystemMessage, HumanMessage
    
    prompt = SystemMessage(content="""You are a resume consistency validator. Your ONLY job is to check if years of experience, education details, certifications, or job titles were changed or fabricated.

CRITICAL RULES:
- If years of experience were added (e.g., "8 years") but not in original, it's FABRICATED
- If degree names, institutions, or dates changed, it's an ERROR
- If certifications/licenses were added but not in original, it's FABRICATED
- If job titles or company names changed, it's an ERROR

Respond with JSON only:
{
    "inconsistencies": [
        {
            "type": "experience_years|education|certification|job_title",
            "severity": "error",
            "message": "<description of inconsistency>",
            "suggestion": "<how to fix>"
        }
    ]
}""")
    
    human_prompt = HumanMessage(content=f"""Original Resume:
---
{original_resume[:3000]}

Tailored Resume:
---
{tailored_resume[:3000]}

Compare the two resumes. Check for:
1. Years of experience that were added
2. Education details that were changed
3. Certifications/licenses that were added
4. Job titles or company names that were changed""")
    
    try:
        response = llm_service.invoke_with_retry([prompt, human_prompt])
        import json
        import re
        
        # Parse JSON response
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group(0))
            issues = []
            for item in data.get("inconsistencies", []):
                issues.append(ValidationIssue(
                    severity=item.get("severity", "error"),
                    category="consistency",
                    message=item.get("message", ""),
                    suggestion=item.get("suggestion", "")
                ))
            return issues
    except Exception as e:
        logger.warning(f"Experience consistency validation failed: {e}")
    
    return []


def _validate_metric_provenance(
    original_resume: str,
    tailored_resume: str,
    user_metric_text: Optional[str] = None,
    verified_metric_records: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[List[ValidationIssue], Dict[str, Any]]:
    """Check that numeric claims are grounded in original resume or user-provided metrics."""
    from ..storage.user_memory import get_verified_metrics, load_memory

    issues: List[ValidationIssue] = []

    original_metrics = extract_metrics(original_resume)
    if user_metric_text:
        user_metrics = extract_metrics(user_metric_text)
    else:
        effective_verified_metric_records = verified_metric_records if verified_metric_records is not None else get_verified_metrics()
        if effective_verified_metric_records:
            user_metrics = [
                MetricMatch(
                    raw=str(metric.get("raw", "")),
                    normalized=str(metric.get("normalized", "")),
                    line=str(metric.get("line", "")),
                    category=str(metric.get("category", "number")),
                )
                for metric in effective_verified_metric_records
            ]
        else:
            memory = load_memory()
            user_metrics = extract_metrics_from_memory(memory)

    allowed_metrics = normalize_metric_set(original_metrics + user_metrics)
    tailored_metrics = extract_metrics(tailored_resume)
    tailored_map = normalize_metric_set(tailored_metrics)

    unverified = [
        metric for metric in tailored_metrics
        if metric.normalized not in allowed_metrics
    ]

    if unverified:
        for metric in unverified[:10]:
            issues.append(ValidationIssue(
                severity=Severity.ERROR,
                category="consistency",
                message=f'Unverified metric: "{metric.raw}" is not in the original resume or user-provided metrics',
                suggestion="Remove the numeric claim or rewrite without numbers unless you can confirm it."
            ))

    provenance = {
        "allowed": sorted({m.raw for m in allowed_metrics.values()}),
        "tailored": sorted({m.raw for m in tailored_map.values()}),
        "flagged": sorted({m.raw for m in unverified}),
        "flagged_details": [
            {"raw": m.raw, "line": m.line, "category": m.category}
            for m in unverified[:20]
        ]
    }

    return issues, provenance


def _validate_jd_coverage(
    llm_service: LLMService,
    tailored_resume: str,
    jd_text: str
) -> Dict[str, Any]:
    """Specialized validation: Check JD coverage"""
    from langchain_core.messages import SystemMessage, HumanMessage
    
    prompt = SystemMessage(content="""You are a JD coverage validator. Your job is to check if key requirements from the job description are addressed in the resume.

Respond with JSON only:
{
    "coverage": {
        "<requirement>": <true/false>
    },
    "issues": [
        {
            "severity": "warning|info",
            "category": "coverage",
            "message": "<missing requirement>",
            "suggestion": "<how to address>"
        }
    ]
}""")
    
    human_prompt = HumanMessage(content=f"""Job Description:
---
{jd_text[:2000]}

Tailored Resume:
---
{tailored_resume[:3000]}

Check which key requirements from the job description are covered in the resume.""")
    
    try:
        response = llm_service.invoke_with_retry([prompt, human_prompt])
        import json
        import re
        
        # Parse JSON response
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group(0))
            issues = []
            for item in data.get("issues", []):
                issues.append(ValidationIssue(
                    severity=Severity(item.get("severity", "warning")),
                    category=item.get("category", "coverage"),
                    message=item.get("message", ""),
                    suggestion=item.get("suggestion", "")
                ))
            return {
                "coverage": data.get("coverage", {}),
                "issues": issues
            }
    except Exception as e:
        logger.warning(f"JD coverage validation failed: {e}")
    
    return {"coverage": {}, "issues": []}


def _validate_format_structure(
    llm_service: LLMService,
    tailored_resume: str
) -> List[ValidationIssue]:
    """Specialized validation: Check format and structure"""
    from langchain_core.messages import SystemMessage, HumanMessage
    
    prompt = SystemMessage(content="""You are a resume format validator. Your job is to check if the resume has proper structure and formatting.

Check for:
- Proper header (name, contact info)
- Standard sections (Experience, Education, Skills, etc.)
- Proper formatting and readability

Respond with JSON only:
{
    "issues": [
        {
            "severity": "warning|info",
            "category": "format",
            "message": "<format issue>",
            "suggestion": "<how to fix>"
        }
    ]
}""")
    
    human_prompt = HumanMessage(content=f"""Resume:
---
{tailored_resume[:3000]}

Check the resume for format and structure issues.""")
    
    try:
        response = llm_service.invoke_with_retry([prompt, human_prompt])
        import json
        import re
        
        # Parse JSON response
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group(0))
            issues = []
            for item in data.get("issues", []):
                issues.append(ValidationIssue(
                    severity=Severity(item.get("severity", "warning")),
                    category=item.get("category", "format"),
                    message=item.get("message", ""),
                    suggestion=item.get("suggestion", "")
                ))
            return issues
    except Exception as e:
        logger.warning(f"Format validation failed: {e}")
    
    return []
    
    # Add skill validation issues if user has skills stored
    if user_skills:
        # Check if tailored resume mentions skills not in user's confirmed skills
        tailored_lower = tailored_resume.lower()
        missing_skills = []
        for skill in user_skills:
            # If skill is mentioned in tailored but not in original, flag it
            if skill.lower() in tailored_lower and skill.lower() not in original_resume.lower():
                # This is okay - it means we're adding a confirmed skill
                pass
        
        # Check for skills in tailored resume that aren't in user's confirmed list
        # This is a simplified check - the LLM validation will do a more thorough job
    
    # LLM-based validation
    skills_context = ""
    if user_skills:
        skills_list = ", ".join(user_skills)
        skills_context = f"\n\nIMPORTANT - USER CONFIRMED SKILLS:\nThe user has confirmed they have these skills: {skills_list}\n- ONLY mention skills from this list or skills already present in the original resume\n- If the tailored resume mentions skills NOT in this list and NOT in the original resume, flag it as an ERROR\n- Skills from the user's confirmed list can be added even if not in the original resume"
    
    validation_prompt_content = """You are a resume quality validator. Analyze a tailored resume and provide structured feedback.

Evaluate:
1. **JD Coverage**: Are key requirements from the job description addressed?
2. **Content Quality**: Is the content professional, specific, and authentic?
3. **Format**: Does it have proper structure (name, contact, sections)?
4. **Consistency**: Are there contradictions with the original resume?
5. **ATS Optimization**: Is it properly formatted for ATS systems?
6. **Skill Authenticity**: Are all skills mentioned actually skills the user has confirmed?""" + skills_context + """

Respond with valid JSON only:
{
    "quality_score": <0-100>,
    "is_valid": <true/false>,
    "issues": [
        {
            "severity": "error|warning|info",
            "category": "format|content|coverage|ats|consistency",
            "message": "<issue description>",
            "suggestion": "<how to fix>"
        }
    ],
    "jd_coverage": {
        "<requirement>": <true/false>
    },
    "keyword_density": <0.0-1.0>,
    "recommendations": ["<rec1>", "<rec2>"]
}"""
    
    validation_prompt = SystemMessage(content=validation_prompt_content)
    
    skills_info = ""
    if user_skills:
        skills_info = f"\n\nUser's Confirmed Skills (can be used even if not in original resume):\n{', '.join(user_skills)}\n\nIMPORTANT: If the tailored resume mentions skills/technologies NOT in the above list AND NOT in the original resume, this is an ERROR - flag it as a critical issue."
    
    human_prompt_content = f"""Job Description:
---
{jd_text[:2000]}

Original Resume:
---
{original_resume[:2000]}

Tailored Resume:
---
{tailored_resume}{skills_info}

Analyze the tailored resume and provide validation feedback. Pay special attention to:
1. Skill authenticity - ensure all skills/technologies mentioned are either in the original resume or in the user's confirmed skills list
2. Experience fabrication - check if years of experience were added
3. Technology additions - check if technologies/tools were added that weren't in original
4. Degree/education changes - ensure education information matches original exactly

Flag any added technologies, skills, or qualifications as ERRORS if they're not in the original resume or confirmed skills list."""
    
    human_prompt = HumanMessage(content=human_prompt_content)
    
    try:
        response = llm_service.invoke_with_retry([validation_prompt, human_prompt])
        
        # Parse JSON response
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            validation_data = json.loads(json_match.group(0))
        else:
            # Fallback to basic validation
            logger.warning("Could not parse LLM validation response, using basic validation")
            return _create_basic_validation(basic_issues, tailored_resume, jd_text)
        
        # Combine with basic issues
        all_issues = basic_issues + [
            ValidationIssue(
                severity=Severity(issue.get("severity", "warning")),
                category=issue.get("category", "content"),
                message=issue.get("message", ""),
                suggestion=issue.get("suggestion")
            )
            for issue in validation_data.get("issues", [])
        ]
        
        # Calculate length metrics (use configurable thresholds)
        from ..config import settings
        word_count = len(tailored_resume.split())
        char_count = len(tailored_resume)
        length_check = {
            "word_count": word_count,
            "char_count": char_count,
            "is_reasonable": settings.resume_recommended_min_words <= word_count <= settings.resume_recommended_max_words,
            "recommended_range": f"{settings.resume_recommended_min_words}-{settings.resume_recommended_max_words} words"
        }
        
        # Calculate ATS score
        ats_score = calculate_ats_score(tailored_resume)
        
        return ResumeValidation(
            quality_score=validation_data.get("quality_score", 70),
            is_valid=validation_data.get("is_valid", True) and len([i for i in all_issues if i.severity == "error"]) == 0,
            issues=all_issues,
            jd_coverage=validation_data.get("jd_coverage", {}),
            keyword_density=validation_data.get("keyword_density", 0.0),
            length_check=length_check,
            recommendations=validation_data.get("recommendations", []),
            ats_score=ats_score  # Add ATS score
        )
        
    except Exception as e:
        logger.error(f"Validation failed: {e}", exc_info=True)
        return _create_basic_validation(basic_issues, tailored_resume, jd_text)


def _basic_validation(tailored_resume: str, jd_text: str, original_resume: str = "", user_skills: List[str] = None) -> List[ValidationIssue]:
    """Basic validation checks that don't require LLM"""
    user_skills = user_skills or []
    issues = []
    
    # Check for JD content leakage
    if len(jd_text) > 100:
        jd_snippet = jd_text[:200].lower()
        if jd_snippet in tailored_resume.lower():
            issues.append(ValidationIssue(
                severity=Severity.ERROR,
                category="content",
                message="Job description content detected in resume",
                suggestion="Remove any JD text that was accidentally included"
            ))
    
    # Check for empty resume
    if not tailored_resume or len(tailored_resume.strip()) < 100:
        issues.append(ValidationIssue(
            severity=Severity.ERROR,
            category="format",
            message="Resume is too short or empty",
            suggestion="Ensure resume has substantial content"
        ))
    
    # Check for proper structure (has name/header)
    if not re.search(r'^[A-Z][a-z]+\s+[A-Z]', tailored_resume[:200]):
        issues.append(ValidationIssue(
            severity=Severity.WARNING,
            category="format",
            message="Resume may be missing proper header (name, contact)",
            suggestion="Ensure resume starts with your name and contact information"
        ))
    
    # Check length (use configurable thresholds)
    from ..config import settings
    word_count = len(tailored_resume.split())
    if word_count < settings.resume_min_words:
        issues.append(ValidationIssue(
            severity=Severity.WARNING,
            category="format",
            message=f"Resume is quite short ({word_count} words)",
            suggestion=f"Consider adding more detail to experiences (recommended: {settings.resume_recommended_min_words}-{settings.resume_recommended_max_words} words)"
        ))
    elif word_count > settings.resume_max_words:
        issues.append(ValidationIssue(
            severity=Severity.WARNING,
            category="format",
            message=f"Resume is quite long ({word_count} words)",
            suggestion=f"Consider condensing to 1-2 pages worth of content (recommended: {settings.resume_recommended_min_words}-{settings.resume_recommended_max_words} words)"
        ))
    
    # Check for fabricated experience years if original resume is provided
    if original_resume:
        # Extract years of experience from tailored resume
        tailored_years_pattern = r'(\d+)\s*(?:years?|yrs?)\s+of\s+experience'
        tailored_years_matches = re.findall(tailored_years_pattern, tailored_resume, re.IGNORECASE)
        
        # Extract years of experience from original resume
        original_years_pattern = r'(\d+)\s*(?:years?|yrs?)\s+of\s+experience'
        original_years_matches = re.findall(original_years_pattern, original_resume, re.IGNORECASE)
        
        # Check if tailored resume added years not in original
        for years_str in tailored_years_matches:
            if years_str not in original_years_matches:
                issues.append(ValidationIssue(
                    severity=Severity.ERROR,
                    category="consistency",
                    message=f"Fabricated experience: '{years_str} years of experience' was added but not in original resume",
                    suggestion="Remove fabricated years of experience. Only use experience levels explicitly stated in the original resume."
                ))
        
        # Check for common technologies/tools that might have been added
        # Common tech keywords to check
        common_tech_keywords = [
            'kubernetes', 'k8s', 'docker', 'containers', 'microservices', 'microservice',
            'apache nifi', 'nifi', 'kafka', 'zookeeper', 'black duck', 'checkmarx',
            'python', 'javascript', 'node.js', 'react', 'angular', 'vue',
            'aws', 'azure', 'gcp', 'cloud computing', 'pcf', 'platform cloud foundry',
            'nosql', 'mongodb', 'cassandra', 'redis', 'elasticsearch'
        ]
        
        original_lower = original_resume.lower()
        tailored_lower = tailored_resume.lower()
        
        # Create lowercase set of user's confirmed skills for checking
        user_skills_lower = {skill.lower() for skill in user_skills}
        
        added_technologies = []
        for tech in common_tech_keywords:
            # Check if tech appears in tailored but not in original
            if tech in tailored_lower and tech not in original_lower:
                # Check if it's in user's confirmed skills - if so, it's allowed
                if tech in user_skills_lower:
                    continue
                # Also check for variations in confirmed skills
                if any(tech in skill.lower() or skill.lower() in tech for skill in user_skills):
                    continue
                    
                # Also check for variations (e.g., "Docker" vs "docker containers")
                tech_variations = [
                    tech,
                    tech.replace(' ', ''),
                    tech.replace('-', ' '),
                    tech.replace('_', ' ')
                ]
                found_in_original = any(var in original_lower for var in tech_variations)
                if not found_in_original:
                    added_technologies.append(tech)
        
        if added_technologies:
            issues.append(ValidationIssue(
                severity=Severity.ERROR,
                category="consistency",
                message=f"Fabricated technologies added: {', '.join(added_technologies[:5])}{' and more' if len(added_technologies) > 5 else ''}. These were not in the original resume.",
                suggestion="Remove all technologies, tools, and skills that were not in the original resume. Only enhance descriptions of existing technologies, do not add new ones."
            ))
        
        # Check for degree/education changes
        # Extract degree mentions from both resumes
        degree_pattern = r'(Bachelor|Master|PhD|Doctorate|Associate)\s+(?:of|in)\s+([A-Za-z\s]+)'
        original_degrees = set(re.findall(degree_pattern, original_resume, re.IGNORECASE))
        tailored_degrees = set(re.findall(degree_pattern, tailored_resume, re.IGNORECASE))
        
        # Check if degrees were changed
        for degree_type, field in tailored_degrees:
            if (degree_type, field) not in original_degrees:
                # Check if it's a similar degree (might be okay) or completely different
                similar_found = False
                for orig_type, orig_field in original_degrees:
                    if degree_type.lower() == orig_type.lower() and (field.lower().strip() in orig_field.lower() or orig_field.lower().strip() in field.lower()):
                        similar_found = True
                        break
                
                if not similar_found:
                    issues.append(ValidationIssue(
                        severity=Severity.ERROR,
                        category="consistency",
                        message=f"Degree information changed: '{degree_type} in {field}' differs from original resume",
                        suggestion="Preserve degree information exactly as in the original resume. Do not change degree names or fields of study."
                    ))
    
    return issues


def _create_basic_validation(
    issues: List[ValidationIssue],
    tailored_resume: str,
    jd_text: str
) -> ResumeValidation:
    """Create basic validation result when LLM validation fails"""
    word_count = len(tailored_resume.split())
    has_errors = any(i.severity == "error" for i in issues)
    
    ats_score = calculate_ats_score(tailored_resume)
    
    from ..config import settings
    word_count = len(tailored_resume.split())
    return ResumeValidation(
        quality_score=80 if not has_errors else 50,
        is_valid=not has_errors,
        issues=issues,
        jd_coverage={},
        keyword_density=0.0,
        length_check={
            "word_count": word_count,
            "char_count": len(tailored_resume),
            "is_reasonable": settings.resume_recommended_min_words <= word_count <= settings.resume_recommended_max_words,
            "recommended_range": f"{settings.resume_recommended_min_words}-{settings.resume_recommended_max_words} words"
        },
        recommendations=["Review the tailored resume manually for quality"],
        ats_score=ats_score
    )


def extract_jd_requirements(llm_service: LLMService, jd_text: str) -> Dict[str, List[str]]:
    """
    Extract key requirements from job description.
    
    Returns:
        Dict with categories: skills, experience, qualifications, etc.
    """
    from langchain_core.messages import SystemMessage, HumanMessage
    
    prompt = SystemMessage(content="""Extract key requirements from a job description and return as JSON.

Categories to extract:
- required_skills: Technical skills that are must-haves
- preferred_skills: Nice-to-have skills
- experience_requirements: Years of experience, specific experience needed
- qualifications: Education, certifications, etc.
- responsibilities: Key responsibilities mentioned

Return JSON:
{
    "required_skills": ["skill1", "skill2"],
    "preferred_skills": ["skill1", "skill2"],
    "experience_requirements": ["req1", "req2"],
    "qualifications": ["qual1", "qual2"],
    "responsibilities": ["resp1", "resp2"]
}""")
    
    human_prompt = HumanMessage(content=f"Job Description:\n{jd_text}")
    
    try:
        response = llm_service.invoke_with_retry([prompt, human_prompt])
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            return json.loads(json_match.group(0))
    except Exception as e:
        logger.warning(f"Failed to extract JD requirements: {e}")
    
    return {
        "required_skills": [],
        "preferred_skills": [],
        "experience_requirements": [],
        "qualifications": [],
        "responsibilities": []
    }


def auto_fix_errors(
    llm_service: LLMService,
    tailored_resume: str,
    original_resume: str,
    validation_issues: List[ValidationIssue],
    user_skills: List[str] = None
) -> tuple[str, List[str]]:
    """
    Automatically fix ERROR-level issues in the tailored resume.
    
    Args:
        llm_service: LLM service for making fixes
        tailored_resume: The resume with issues
        original_resume: The original resume for reference
        validation_issues: List of validation issues (only ERRORs will be fixed)
        user_skills: User's confirmed skills list
        
    Returns:
        Tuple of (fixed_resume, list_of_changes_made)
    """
    from langchain_core.messages import SystemMessage, HumanMessage
    
    # Filter only ERROR-level issues
    errors = [issue for issue in validation_issues if issue.severity == Severity.ERROR or issue.severity == "error"]
    
    if not errors:
        logger.info("No ERROR-level issues to fix")
        return tailored_resume, []
    
    # Build list of issues to fix
    issues_to_fix = []
    for error in errors:
        issues_to_fix.append(f"- {error.message}")
    
    issues_text = "\n".join(issues_to_fix)
    user_skills_list = ", ".join(user_skills) if user_skills else "Not specified"
    
    prompt = SystemMessage(content=f"""You are a RESUME FIXER. Your job is to fix ONLY the specific errors listed below.

ERRORS TO FIX:
{issues_text}

USER'S CONFIRMED SKILLS (ONLY use these):
{user_skills_list}

CRITICAL RULES:
1. ONLY fix the specific errors listed above
2. DO NOT change anything else in the resume
3. For "Fabricated technology/skill" errors: REMOVE the fabricated skill/technology entirely
4. For "Fabricated experience" errors: REMOVE the fabricated years of experience
5. For "Degree information changed" errors: RESTORE the original degree information
6. PRESERVE all formatting, structure, and other content
7. If a skill is NOT in the user's confirmed skills AND NOT in the original resume, REMOVE it

OUTPUT: Return ONLY the fixed resume text, nothing else. No explanations, no markdown, just the resume content.""")
    
    human_prompt = HumanMessage(content=f"""ORIGINAL RESUME (reference for what was there originally):
---
{original_resume[:3000]}
---

TAILORED RESUME (with errors to fix):
---
{tailored_resume}
---

Fix the errors listed above and return ONLY the corrected resume text.""")
    
    try:
        fixed_resume = llm_service.invoke_with_retry([prompt, human_prompt])
        
        # Clean up any markdown formatting the LLM might have added
        fixed_resume = fixed_resume.strip()
        if fixed_resume.startswith("```"):
            # Remove markdown code blocks
            lines = fixed_resume.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            fixed_resume = "\n".join(lines)
        
        changes_made = [f"Fixed: {error.message}" for error in errors]
        
        logger.info(
            "Auto-fix completed",
            errors_fixed=len(errors),
            changes=changes_made
        )
        
        return fixed_resume, changes_made
        
    except Exception as e:
        logger.error(f"Auto-fix failed: {e}", exc_info=True)
        return tailored_resume, []  # Return unchanged if fix fails


def has_critical_errors(validation_issues: List[ValidationIssue]) -> bool:
    """Check if there are ERROR-level issues that need fixing."""
    return any(
        issue.severity == Severity.ERROR or issue.severity == "error"
        for issue in validation_issues
    )
