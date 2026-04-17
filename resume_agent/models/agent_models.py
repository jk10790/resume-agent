"""
Pydantic models for agent outputs and structured data validation.
These models provide strict validation and type safety for all agent outputs.
"""

from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Dict, List, Optional, Any
from enum import Enum


# ============================================================================
# Validation Models
# ============================================================================

class Severity(str, Enum):
    """Validation issue severity levels"""
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class ValidationIssue(BaseModel):
    """A validation issue found in the resume"""
    severity: Severity = Field(..., description="Issue severity level")
    category: str = Field(..., description="Issue category (format, content, coverage, etc.)")
    message: str = Field(..., description="Issue message")
    suggestion: Optional[str] = Field(None, description="Suggestion for fixing the issue")
    
    model_config = {"use_enum_values": True}


class ResumeValidation(BaseModel):
    """Result of resume validation"""
    quality_score: int = Field(..., ge=0, le=100, description="Quality score 0-100")
    is_valid: bool = Field(..., description="Whether resume is valid (no errors)")
    issues: List[ValidationIssue] = Field(default_factory=list, description="List of validation issues")
    jd_coverage: Dict[str, bool] = Field(default_factory=dict, description="JD requirement coverage map")
    keyword_density: float = Field(0.0, ge=0.0, le=1.0, description="Keyword density 0-1")
    length_check: Dict[str, Any] = Field(default_factory=dict, description="Length check metrics")
    recommendations: List[str] = Field(default_factory=list, description="Recommendations for improvement")
    ats_score: Optional[float] = Field(None, ge=0, le=100, description="ATS compatibility score 0-100")
    metric_provenance: Optional[Dict[str, Any]] = Field(
        default_factory=dict,
        description="Metric provenance report (allowed/tailored/flagged numbers)"
    )


# ============================================================================
# Resume Parser Agent Models
# ============================================================================

class SkillsData(BaseModel):
    """Structured skills data from resume"""
    programming_languages: List[str] = Field(default_factory=list)
    frameworks: List[str] = Field(default_factory=list)
    tools: List[str] = Field(default_factory=list)
    databases: List[str] = Field(default_factory=list)
    cloud_platforms: List[str] = Field(default_factory=list)
    testing_tools: List[str] = Field(default_factory=list)
    other_technologies: List[str] = Field(default_factory=list)
    methodologies: List[str] = Field(default_factory=list)


class ExperienceData(BaseModel):
    """Structured experience data from resume"""
    total_years: Optional[float] = Field(None, ge=0, description="Total years of experience if explicitly stated")
    years_mentioned: List[str] = Field(default_factory=list, description="All year mentions found")
    job_titles: List[str] = Field(default_factory=list, description="Job titles")
    companies: List[str] = Field(default_factory=list, description="Company names")
    summary: str = Field("", description="Brief experience summary")


class EducationEntry(BaseModel):
    """Single education entry"""
    degree: str = Field("", description="Degree name")
    field: str = Field("", description="Field of study")
    institution: str = Field("", description="Institution name")
    dates: Optional[str] = Field(None, description="Education dates")


class ParsedResumeStructured(BaseModel):
    """Structured LLM output for resume parsing"""
    skills: SkillsData = Field(default_factory=SkillsData)
    experience: ExperienceData = Field(default_factory=ExperienceData)
    education: List[EducationEntry] = Field(default_factory=list)
    
    @field_validator('skills', mode='before')
    @classmethod
    def validate_skills(cls, v):
        if not isinstance(v, dict):
            return SkillsData()
        return SkillsData(**v)
    
    @field_validator('experience', mode='before')
    @classmethod
    def validate_experience(cls, v):
        if not isinstance(v, dict):
            return ExperienceData()
        return ExperienceData(**v)
    
    @field_validator('education', mode='before')
    @classmethod
    def validate_education(cls, v):
        if not isinstance(v, list):
            return []
        return [EducationEntry(**item) if isinstance(item, dict) else item for item in v]


class ParsedResume(BaseModel):
    """Complete parsed resume structure"""
    # Skills and Technologies
    programming_languages: List[str] = Field(default_factory=list)
    frameworks: List[str] = Field(default_factory=list)
    tools: List[str] = Field(default_factory=list)
    databases: List[str] = Field(default_factory=list)
    cloud_platforms: List[str] = Field(default_factory=list)
    testing_tools: List[str] = Field(default_factory=list)
    other_technologies: List[str] = Field(default_factory=list)
    methodologies: List[str] = Field(default_factory=list)
    all_skills: List[str] = Field(default_factory=list, description="Flattened list of all skills")
    
    # Experience
    total_years_experience: Optional[float] = Field(None, ge=0)
    years_mentioned: List[str] = Field(default_factory=list)
    job_titles: List[str] = Field(default_factory=list)
    companies: List[str] = Field(default_factory=list)
    experience_summary: str = Field("", description="Brief summary of experience")
    
    # Education
    education: List[Dict[str, str]] = Field(default_factory=list, description="List of education entries")
    
    # Other sections
    certifications: List[str] = Field(default_factory=list)
    projects: List[Dict[str, str]] = Field(default_factory=list, description="List of project entries")
    summary: Optional[str] = Field(None, description="Professional summary if present")
    
    # Raw data
    raw_text: str = Field(..., description="Original resume text")
    sections: Dict[str, str] = Field(default_factory=dict, description="Section name -> content")


class UserProfileContext(BaseModel):
    """Persisted, user-scoped profile context shared across workflow steps."""
    local_user_id: Optional[int] = Field(None, description="Authenticated local user id")
    confirmed_skills: List[str] = Field(default_factory=list, description="Confirmed skills from the user profile")
    detected_skill_records: List[Dict[str, Any]] = Field(default_factory=list, description="Detected skills awaiting confirmation")
    suggested_skill_records: List[Dict[str, Any]] = Field(default_factory=list, description="Suggested skills derived from resume/role")
    confirmed_metric_records: List[Dict[str, Any]] = Field(default_factory=list, description="Confirmed metrics/evidence from the user profile")
    preferred_resume_doc_id: Optional[str] = Field(None, description="Preferred resume Google Doc id")
    preferred_resume_name: Optional[str] = Field(None, description="Preferred resume display name")


# ============================================================================
# JD Analyzer Agent Models
# ============================================================================

class RequirementsData(BaseModel):
    """Structured requirements data from JD"""
    required_skills: List[str] = Field(default_factory=list, description="Hard requirements")
    preferred_skills: List[str] = Field(default_factory=list, description="Nice to have")
    required_experience_years: Optional[float] = Field(None, ge=0, description="Required years if explicitly stated")
    required_education: List[Dict[str, str]] = Field(default_factory=list, description="Education requirements")


class ResponsibilitiesData(BaseModel):
    """Structured responsibilities data"""
    responsibilities: List[str] = Field(default_factory=list, description="Key responsibilities")


class TechnologiesData(BaseModel):
    """Structured technologies data from JD"""
    technologies: List[str] = Field(default_factory=list)
    tools: List[str] = Field(default_factory=list)
    frameworks: List[str] = Field(default_factory=list)


class RoleInfoData(BaseModel):
    """Structured role information"""
    job_title: str = Field("Unknown", description="Job title")
    company: Optional[str] = Field(None, description="Company name")
    role_type: str = Field("Unknown", description="Role type (Full-time, Contract, etc.)")
    location: Optional[str] = Field(None, description="Location")
    industry: Optional[str] = Field(None, description="Industry")
    team_size: Optional[str] = Field(None, description="Team size")
    summary: str = Field("", description="Brief role summary")


class AnalyzedJDStructured(BaseModel):
    """Structured LLM output for JD analysis"""
    requirements: RequirementsData = Field(default_factory=RequirementsData)
    responsibilities: List[str] = Field(default_factory=list)
    technologies: TechnologiesData = Field(default_factory=TechnologiesData)
    role_info: RoleInfoData = Field(default_factory=RoleInfoData)
    
    @field_validator('requirements', mode='before')
    @classmethod
    def validate_requirements(cls, v):
        if not isinstance(v, dict):
            return RequirementsData()
        return RequirementsData(**v)
    
    @field_validator('technologies', mode='before')
    @classmethod
    def validate_technologies(cls, v):
        if not isinstance(v, dict):
            return TechnologiesData()
        return TechnologiesData(**v)
    
    @field_validator('role_info', mode='before')
    @classmethod
    def validate_role_info(cls, v):
        if not isinstance(v, dict):
            return RoleInfoData()
        return RoleInfoData(**v)


class AnalyzedJD(BaseModel):
    """Complete analyzed JD structure"""
    # Role Information
    job_title: str = Field(..., description="Job title")
    company: Optional[str] = Field(None, description="Company name")
    role_type: str = Field("Unknown", description="Role type")
    location: Optional[str] = Field(None, description="Location")
    
    # Requirements
    required_skills: List[str] = Field(default_factory=list, description="Hard requirements")
    preferred_skills: List[str] = Field(default_factory=list, description="Nice to have")
    required_experience_years: Optional[float] = Field(None, ge=0, description="Required years if explicitly stated")
    required_education: List[Dict[str, str]] = Field(default_factory=list, description="Education requirements")
    
    # Responsibilities
    key_responsibilities: List[str] = Field(default_factory=list, description="Key responsibilities")
    
    # Technologies/Tools
    technologies_needed: List[str] = Field(default_factory=list)
    tools_needed: List[str] = Field(default_factory=list)
    frameworks_needed: List[str] = Field(default_factory=list)
    
    # Other
    industry: Optional[str] = Field(None, description="Industry")
    team_size: Optional[str] = Field(None, description="Team size")
    summary: str = Field("", description="Brief summary of the role")
    
    # Raw data
    raw_text: str = Field(..., description="Original JD text")


# ============================================================================
# Fit Evaluator Agent Models
# ============================================================================

class FitAnalysisStructured(BaseModel):
    """Structured LLM output for fit analysis"""
    fit_score: int = Field(5, ge=1, le=10, description="Fit score 1-10")
    should_apply: bool = Field(False, description="Whether to apply")
    confidence: float = Field(0.5, ge=0.0, le=1.0, description="Confidence 0-1")
    experience_match: str = Field("unknown", description="Experience match: exceeds|meets|below|unknown")
    experience_gap_years: Optional[float] = Field(None, ge=0, description="Experience gap if below requirement")
    education_match: bool = Field(False, description="Whether education matches")
    missing_education: List[str] = Field(default_factory=list, description="Missing education requirements")
    strengths: List[str] = Field(default_factory=list, description="Strengths")
    weaknesses: List[str] = Field(default_factory=list, description="Weaknesses")
    recommendations: List[str] = Field(default_factory=list, description="Recommendations")
    matching_areas: List[str] = Field(default_factory=list, description="Matching areas")
    missing_areas: List[str] = Field(default_factory=list, description="Missing areas")


class FitAnalysis(BaseModel):
    """Complete fit analysis structure"""
    fit_score: int = Field(5, ge=1, le=10)
    should_apply: bool = Field(False)
    confidence: float = Field(0.5, ge=0.0, le=1.0)
    
    # Matching analysis
    matching_skills: List[str] = Field(default_factory=list, description="Skills that match")
    missing_required_skills: List[str] = Field(default_factory=list, description="Required skills missing")
    matching_preferred_skills: List[str] = Field(default_factory=list, description="Preferred skills that match")
    
    # Experience analysis
    experience_match: str = Field("unknown", description="exceeds|meets|below|unknown")
    experience_gap_years: Optional[float] = Field(None, ge=0)
    
    # Education analysis
    education_match: bool = Field(False)
    missing_education: List[str] = Field(default_factory=list)
    
    # Overall assessment
    strengths: List[str] = Field(default_factory=list)
    weaknesses: List[str] = Field(default_factory=list)
    recommendations: List[str] = Field(default_factory=list)
    
    # Matching areas (for display)
    matching_areas: List[str] = Field(default_factory=list)
    missing_areas: List[str] = Field(default_factory=list)


# ============================================================================
# ATS Scorer Agent Models
# ============================================================================

class ATSScoreStructured(BaseModel):
    """Structured LLM output for ATS scoring"""
    format_score: int = Field(..., ge=0, le=100, description="Format score 0-100")
    content_score: int = Field(..., ge=0, le=100, description="Content score 0-100")
    recommendations: List[str] = Field(default_factory=list, description="Recommendations")


class ATSScore(BaseModel):
    """Complete ATS score structure"""
    score: int = Field(..., ge=0, le=100, description="Overall ATS score 0-100")
    keyword_density: float = Field(..., ge=0.0, le=1.0, description="Keyword density 0-1")
    keyword_matches: Dict[str, int] = Field(default_factory=dict, description="Keyword -> count")
    missing_keywords: List[str] = Field(default_factory=list, description="Missing keywords")
    format_score: int = Field(..., ge=0, le=100, description="Format score 0-100")
    content_score: int = Field(..., ge=0, le=100, description="Content score 0-100")
    recommendations: List[str] = Field(default_factory=list, description="Recommendations")


# ============================================================================
# Review Agent Models
# ============================================================================

class ReviewResult(BaseModel):
    """Result of review agent"""
    reviewed_resume: str = Field(..., description="Final reviewed resume")
    validation: ResumeValidation = Field(..., description="Validation results")
    review_bundle: Optional["ReviewBundle"] = Field(None, description="Structured multi-surface review bundle")
    changes_made: List[str] = Field(default_factory=list, description="List of changes made during review")
    final_quality_score: float = Field(..., ge=0, le=100, description="Final quality score 0-100")


# ============================================================================
# Review Bundle Models
# ============================================================================

class ReviewIssue(BaseModel):
    """Structured issue for a specific review surface"""
    severity: Severity = Field(..., description="Issue severity level")
    category: str = Field(..., description="Issue category")
    message: str = Field(..., description="Issue message")
    suggestion: Optional[str] = Field(None, description="Suggestion for fixing the issue")
    evidence: Optional[str] = Field(None, description="Optional supporting evidence")

    model_config = {"use_enum_values": True}


class ReviewSection(BaseModel):
    """One distinct review surface presented to the user"""
    score: int = Field(..., ge=0, le=100, description="Section score 0-100")
    verdict: str = Field(..., description="Section verdict")
    summary: str = Field("", description="Short section summary")
    issues: List[ReviewIssue] = Field(default_factory=list, description="Issues for this section")
    recommendations: List[str] = Field(default_factory=list, description="Section recommendations")
    metrics: Dict[str, Any] = Field(default_factory=dict, description="Structured section metrics")


class ReviewOverall(BaseModel):
    """Overall synthesis of the review bundle"""
    score: int = Field(..., ge=0, le=100, description="Overall score 0-100")
    verdict: str = Field(..., description="Overall verdict")
    summary: str = Field("", description="Summary of overall recommendation")
    recommendation: str = Field(..., description="Submission recommendation")


class ReviewBundle(BaseModel):
    """Structured review surfaces for authenticity, ATS, job match, and editorial quality"""
    authenticity: ReviewSection
    ats_parse: ReviewSection
    job_match: ReviewSection
    editorial: ReviewSection
    overall: ReviewOverall


ReviewResult.model_rebuild()
