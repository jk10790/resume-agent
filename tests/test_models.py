"""
Unit tests for data models.
"""

import pytest
from datetime import datetime
from resume_agent.models.resume import Resume, JobDescription, FitEvaluation, Application, ApplicationStatus


class TestResume:
    """Tests for Resume model"""
    
    def test_resume_creation(self):
        """Test creating a resume"""
        resume = Resume(content="Test resume content")
        assert resume.content == "Test resume content"
        assert resume.version == "1.0"
        assert isinstance(resume.created_at, datetime)
    
    def test_resume_validation_empty_content(self):
        """Test that empty content is rejected"""
        with pytest.raises(ValueError):
            Resume(content="")
    
    def test_resume_validation_whitespace(self):
        """Test that whitespace-only content is rejected"""
        with pytest.raises(ValueError):
            Resume(content="   \n\t  ")


class TestJobDescription:
    """Tests for JobDescription model"""
    
    def test_jd_creation(self):
        """Test creating a job description"""
        jd = JobDescription(
            title="Software Engineer",
            company="Tech Corp",
            content="Job description content"
        )
        assert jd.title == "Software Engineer"
        assert jd.company == "Tech Corp"
        assert jd.content == "Job description content"
    
    def test_jd_validation_empty_fields(self):
        """Test that empty required fields are rejected"""
        with pytest.raises(ValueError):
            JobDescription(title="", company="Tech Corp", content="Content")
        
        with pytest.raises(ValueError):
            JobDescription(title="Engineer", company="", content="Content")
        
        with pytest.raises(ValueError):
            JobDescription(title="Engineer", company="Tech Corp", content="")


class TestFitEvaluation:
    """Tests for FitEvaluation model"""
    
    def test_fit_evaluation_creation(self):
        """Test creating a fit evaluation"""
        evaluation = FitEvaluation(
            score=8,
            should_apply=True,
            matching_areas=["Python", "AWS"],
            missing_areas=["Kubernetes"],
            recommendations=["Learn Kubernetes"]
        )
        assert evaluation.score == 8
        assert evaluation.should_apply is True
        assert len(evaluation.matching_areas) == 2
    
    def test_fit_evaluation_score_validation(self):
        """Test score validation"""
        with pytest.raises(ValueError):
            FitEvaluation(score=11, should_apply=True)  # Too high
        
        with pytest.raises(ValueError):
            FitEvaluation(score=0, should_apply=True)  # Too low
    
    def test_fit_evaluation_display_string(self):
        """Test display string generation"""
        evaluation = FitEvaluation(
            score=7,
            should_apply=True,
            matching_areas=["Python"],
            missing_areas=["Docker"],
            recommendations=["Learn Docker"]
        )
        display = evaluation.to_display_string()
        assert "7/10" in display
        assert "✅ Yes" in display
        assert "Python" in display


class TestApplication:
    """Tests for Application model"""
    
    def test_application_creation(self):
        """Test creating an application"""
        app = Application(
            job_title="Software Engineer",
            company="Tech Corp",
            status=ApplicationStatus.APPLIED
        )
        assert app.job_title == "Software Engineer"
        assert app.company == "Tech Corp"
        assert app.status == ApplicationStatus.APPLIED
    
    def test_application_default_status(self):
        """Test default status is PREPARED"""
        app = Application(job_title="Engineer", company="Corp")
        assert app.status == ApplicationStatus.PREPARED
