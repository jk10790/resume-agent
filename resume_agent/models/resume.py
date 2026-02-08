"""
Data models for resume agent using Pydantic for type safety and validation.
"""

from pydantic import BaseModel, Field, field_validator, ConfigDict
from typing import Optional, List
from datetime import datetime
from enum import Enum


class ApplicationStatus(str, Enum):
    """Application status enum"""
    PREPARED = "prepared"
    APPLIED = "applied"
    INTERVIEW = "interview"
    REJECTED = "rejected"
    OFFER = "offer"
    WITHDRAWN = "withdrawn"


class Resume(BaseModel):
    """Resume model"""
    content: str = Field(..., description="Resume content/text")
    version: str = Field(default="1.0", description="Resume version")
    created_at: datetime = Field(default_factory=datetime.now)
    source: str = Field(default="google_docs", description="Source of resume")
    doc_id: Optional[str] = Field(None, description="Google Docs ID if applicable")
    
    @field_validator('content')
    @classmethod
    def content_not_empty(cls, v):
        if not v or not v.strip():
            raise ValueError("Resume content cannot be empty")
        return v.strip()


class JobDescription(BaseModel):
    """Job description model"""
    title: str = Field(..., description="Job title")
    company: str = Field(..., description="Company name")
    url: Optional[str] = Field(None, description="Job listing URL")
    content: str = Field(..., description="Job description content")
    extracted_at: datetime = Field(default_factory=datetime.now)
    raw_html: Optional[str] = Field(None, description="Raw HTML if available")
    
    @field_validator('content')
    @classmethod
    def content_not_empty(cls, v):
        if not v or not v.strip():
            raise ValueError("Job description content cannot be empty")
        return v.strip()
    
    @field_validator('title', 'company')
    @classmethod
    def fields_not_empty(cls, v):
        if not v or not v.strip():
            raise ValueError("Title and company cannot be empty")
        return v.strip()


class FitEvaluation(BaseModel):
    """Job fit evaluation model with structured output"""
    score: int = Field(..., ge=1, le=10, description="Fit score from 1-10")
    should_apply: bool = Field(..., description="Whether to apply")
    matching_areas: List[str] = Field(default_factory=list, description="Matching skills/areas")
    missing_areas: List[str] = Field(default_factory=list, description="Missing requirements")
    recommendations: List[str] = Field(default_factory=list, description="Recommendations")
    confidence: float = Field(default=0.8, ge=0.0, le=1.0, description="Confidence in evaluation")
    reasoning: Optional[str] = Field(None, description="Reasoning behind the score")
    
    def to_display_string(self) -> str:
        """Convert to human-readable string"""
        lines = [
            "=" * 60,
            "📊 FIT EVALUATION RESULT",
            "=" * 60,
            f"Fit Score: {self.score}/10",
            f"Should Apply: {'✅ Yes' if self.should_apply else '❌ No'}",
            f"Confidence: {self.confidence:.0%}",
            "",
            "Top Matching Areas:",
            *[f"  ✅ {area}" for area in self.matching_areas],
            "",
            "Missing or Unclear Areas:",
            *[f"  ❌ {area}" for area in self.missing_areas],
            "",
            "Recommendations:",
            *[f"  💡 {rec}" for rec in self.recommendations],
        ]
        if self.reasoning:
            lines.extend([
                "",
                "Reasoning:",
                f"  {self.reasoning}"
            ])
        lines.append("=" * 60)
        return "\n".join(lines)


class Application(BaseModel):
    """Job application tracking model"""
    id: Optional[int] = Field(None, description="Database ID")
    job_title: str = Field(..., description="Job title")
    company: str = Field(..., description="Company name")
    job_url: Optional[str] = Field(None, description="Job listing URL")
    status: ApplicationStatus = Field(default=ApplicationStatus.PREPARED)
    fit_score: Optional[int] = Field(None, ge=1, le=10, description="Fit score")
    resume_doc_id: Optional[str] = Field(None, description="Google Docs resume ID")
    cover_letter_doc_id: Optional[str] = Field(None, description="Google Docs cover letter ID")
    notes: Optional[str] = Field(None, description="User notes")
    interview_date: Optional[datetime] = Field(None, description="Interview date")
    application_date: datetime = Field(default_factory=datetime.now)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    
    model_config = ConfigDict(use_enum_values=True)
