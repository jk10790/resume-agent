"""
Multi-Agent Workflow Service Integration Tests
Tests the specialized agent workflow with all components working together.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path

from resume_agent.services.multi_agent_workflow import MultiAgentWorkflowService
from resume_agent.services.resume_workflow import (
    TailorResumeRequest,
    TailorResumeResult,
    WorkflowStep
)
from resume_agent.models.agent_models import (
    ParsedResume,
    AnalyzedJD,
    FitAnalysis,
    ATSScore
)


@pytest.fixture
def sample_resume_text():
    """Sample resume for testing"""
    return """# Jane Smith
Senior Software Engineer | jane.smith@email.com | San Francisco, CA

## Professional Summary
Experienced software engineer with 7 years of experience in Python, cloud computing, and distributed systems.

## Experience

**Senior Software Engineer** | Google | 2020-Present
- Led development of microservices handling 1M+ requests/day using Python and Kubernetes
- Designed and implemented CI/CD pipelines reducing deployment time by 50%
- Mentored 5 junior engineers and conducted code reviews

**Software Engineer** | Amazon | 2017-2020
- Built AWS Lambda functions processing real-time data streams
- Developed Python libraries used by 20+ internal teams
- Improved API response time by 40% through optimization

## Education
**Bachelor of Science in Computer Science** | Stanford University | 2017

## Technical Skills
- Languages: Python, Java, Go, JavaScript
- Cloud: AWS (Lambda, EC2, S3, DynamoDB), GCP
- Tools: Docker, Kubernetes, Terraform, Jenkins
- Databases: PostgreSQL, MongoDB, Redis
"""


@pytest.fixture
def sample_jd_text():
    """Sample job description"""
    return """Senior Software Engineer - Platform Team

About the Role:
We're looking for a Senior Software Engineer to join our Platform team. You'll be building scalable systems that power our core infrastructure.

Requirements:
- 5+ years of experience in software development
- Strong proficiency in Python or Go
- Experience with cloud platforms (AWS preferred)
- Experience with containerization (Docker, Kubernetes)
- Strong understanding of distributed systems

Nice to Have:
- Experience with Terraform or similar IaC tools
- Machine learning background
- Experience mentoring junior engineers

Responsibilities:
- Design and build scalable microservices
- Lead technical projects and mentor team members
- Collaborate with cross-functional teams
- Participate in on-call rotation
"""


@pytest.fixture
def mock_llm_service():
    """Mock LLM service that returns structured responses"""
    service = Mock()
    
    # Mock basic invoke
    service.invoke_with_retry = Mock(return_value="Tailored resume content with matching keywords")
    
    # Mock structured invoke for parsing
    def mock_invoke_structured(messages, response_model, **kwargs):
        model_name = response_model.__name__ if hasattr(response_model, '__name__') else str(response_model)
        
        if 'ParsedResume' in model_name or 'parsed' in str(messages).lower():
            return {
                "skills": {
                    "technical": ["Python", "Java", "Go", "AWS", "Docker", "Kubernetes"],
                    "tools": ["Terraform", "Jenkins", "Git"],
                    "soft": ["Leadership", "Mentoring"]
                },
                "experience": {
                    "total_years": 7,
                    "job_history": [
                        {"title": "Senior Software Engineer", "company": "Google", "years": 4},
                        {"title": "Software Engineer", "company": "Amazon", "years": 3}
                    ]
                },
                "education": [
                    {"degree": "Bachelor of Science", "field": "Computer Science", "institution": "Stanford University"}
                ]
            }
        elif 'AnalyzedJD' in model_name or 'requirements' in str(messages).lower():
            return {
                "requirements": {
                    "required": ["5+ years experience", "Python or Go", "AWS", "Docker/Kubernetes"],
                    "preferred": ["Terraform", "ML background", "Mentoring experience"]
                },
                "responsibilities": {
                    "primary": ["Build microservices", "Lead projects"],
                    "secondary": ["On-call rotation"]
                },
                "technologies": {
                    "required": ["Python", "Go", "AWS", "Docker", "Kubernetes"],
                    "preferred": ["Terraform"]
                },
                "role_info": {
                    "title": "Senior Software Engineer",
                    "level": "Senior",
                    "team": "Platform",
                    "experience_years": 5
                }
            }
        elif 'FitAnalysis' in model_name or 'fit' in str(messages).lower():
            return {
                "fit_score": 8,
                "should_apply": True,
                "confidence": 0.85,
                "experience_match": "exceeds",
                "education_match": True,
                "strengths": ["Strong Python experience", "AWS expertise", "Leadership"],
                "weaknesses": ["No explicit ML background"],
                "recommendations": ["Highlight Kubernetes experience more"]
            }
        elif 'ATSScore' in model_name or 'ats' in str(messages).lower():
            return {
                "format_score": 85,
                "content_score": 88,
                "keyword_density": 0.72,
                "recommendations": ["Add more quantifiable achievements"]
            }
        else:
            return {"result": "default mock response"}
    
    service.invoke_structured = Mock(side_effect=mock_invoke_structured)
    
    return service


@pytest.fixture
def mock_google_services():
    """Mock Google services"""
    drive_service = Mock()
    docs_service = Mock()
    
    # Mock document read
    docs_service.documents.return_value.get.return_value.execute.return_value = {
        "body": {"content": [{"paragraph": {"elements": [{"textRun": {"content": "Resume content"}}]}}]}
    }
    
    # Mock folder operations
    drive_service.files.return_value.copy.return_value.execute.return_value = {"id": "new_doc_123"}
    drive_service.files.return_value.create.return_value.execute.return_value = {"id": "folder_123"}
    drive_service.files.return_value.list.return_value.execute.return_value = {"files": []}
    
    return (drive_service, docs_service)


class TestMultiAgentWorkflowService:
    """Test multi-agent workflow service"""
    
    def test_initialization(self, mock_llm_service, mock_google_services):
        """Test service initialization creates all agents"""
        service = MultiAgentWorkflowService(
            llm_service=mock_llm_service,
            google_services=mock_google_services
        )
        
        assert service.resume_parser is not None
        assert service.jd_analyzer is not None
        assert service.fit_evaluator is not None
        assert service.ats_scorer is not None
        assert service.resume_tailor is not None
        assert service.review_agent is not None
    
    def test_load_resume_step(self, mock_llm_service, mock_google_services, sample_resume_text):
        """Test resume loading step"""
        service = MultiAgentWorkflowService(
            llm_service=mock_llm_service,
            google_services=mock_google_services
        )
        
        request = TailorResumeRequest(
            company="Test Corp",
            job_title="Senior Engineer",
            jd_text="Requirements: Python, AWS"
        )
        
        with patch('resume_agent.services.multi_agent_workflow.read_google_doc', return_value=sample_resume_text):
            result = service.execute_workflow_step(
                request=request,
                current_step=WorkflowStep.LOADING_RESUME,
                previous_result=None
            )
            
            assert result.original_resume is not None
            assert result.error is None
    
    def test_evaluate_fit_step(self, mock_llm_service, mock_google_services, sample_resume_text, sample_jd_text):
        """Test fit evaluation step"""
        service = MultiAgentWorkflowService(
            llm_service=mock_llm_service,
            google_services=mock_google_services
        )
        
        request = TailorResumeRequest(
            company="Test Corp",
            job_title="Senior Engineer",
            jd_text=sample_jd_text
        )
        
        # Start with loaded resume
        previous_result = TailorResumeResult(
            current_step=WorkflowStep.EVALUATING_FIT,
            original_resume=sample_resume_text
        )
        
        with patch.object(service.resume_parser, 'parse', return_value=Mock(
            all_skills=["Python", "AWS", "Kubernetes"],
            total_experience_years=7
        )):
            with patch.object(service.jd_analyzer, 'analyze', return_value=Mock(
                required_skills=["Python", "AWS"],
                preferred_skills=["Kubernetes"],
                min_experience_years=5
            )):
                with patch.object(service.fit_evaluator, 'evaluate', return_value=Mock(
                    fit_score=8,
                    should_apply=True,
                    matching_skills=["Python", "AWS"],
                    missing_required_skills=[]
                )):
                    result = service.execute_workflow_step(
                        request=request,
                        current_step=WorkflowStep.EVALUATING_FIT,
                        previous_result=previous_result
                    )
                    
                    assert result.evaluation is not None
    
    def test_workflow_uses_progress_callback(self, mock_llm_service, mock_google_services, sample_resume_text):
        """Test that progress callbacks are invoked"""
        service = MultiAgentWorkflowService(
            llm_service=mock_llm_service,
            google_services=mock_google_services
        )
        
        progress_messages = []
        def track_progress(msg):
            progress_messages.append(msg)
        
        request = TailorResumeRequest(
            company="Test Corp",
            job_title="Senior Engineer",
            jd_text="Requirements: Python"
        )
        
        with patch('resume_agent.services.multi_agent_workflow.read_google_doc', return_value=sample_resume_text):
            result = service.execute_workflow_step(
                request=request,
                current_step=WorkflowStep.LOADING_RESUME,
                previous_result=None,
                progress_callback=track_progress
            )
            
            # Should have at least one progress message
            assert len(progress_messages) > 0


class TestAgentIntegration:
    """Test individual agent integration"""
    
    def test_resume_parser_extracts_skills(self, mock_llm_service):
        """Test resume parser extracts skills correctly"""
        from resume_agent.agents.resume_parser_agent import ResumeParserAgent
        
        parser = ResumeParserAgent(mock_llm_service)
        
        with patch.object(parser, '_extract_all_structured', return_value={
            "all_skills": ["Python", "AWS", "Docker"],
            "technical_skills": ["Python", "AWS", "Docker"],
            "tools": [],
            "soft_skills": [],
            "total_experience_years": 5,
            "job_history": [],
            "education": []
        }):
            result = parser.parse("Sample resume text")
            assert result is not None
    
    def test_jd_analyzer_extracts_requirements(self, mock_llm_service):
        """Test JD analyzer extracts requirements correctly"""
        from resume_agent.agents.jd_analyzer_agent import JDAnalyzerAgent
        
        analyzer = JDAnalyzerAgent(mock_llm_service)
        
        with patch.object(analyzer, '_extract_all_structured', return_value={
            "required_skills": ["Python", "AWS"],
            "preferred_skills": ["Kubernetes"],
            "required_experience": ["5+ years"],
            "preferred_experience": [],
            "technologies_needed": ["Python", "AWS"],
            "tools_needed": [],
            "frameworks_needed": [],
            "role_title": "Senior Engineer",
            "role_level": "Senior",
            "team": "Platform",
            "min_experience_years": 5
        }):
            result = analyzer.analyze("Sample JD text")
            assert result is not None
    
    def test_fit_evaluator_compares_correctly(self, mock_llm_service):
        """Test fit evaluator properly compares resume to JD"""
        from resume_agent.agents.fit_evaluator_agent import FitEvaluatorAgent
        
        evaluator = FitEvaluatorAgent(mock_llm_service)
        
        mock_resume = Mock()
        mock_resume.all_skills = ["Python", "AWS", "Docker"]
        mock_resume.total_experience_years = 7
        
        mock_jd = Mock()
        mock_jd.required_skills = ["Python", "AWS"]
        mock_jd.preferred_skills = ["Kubernetes"]
        mock_jd.min_experience_years = 5
        
        with patch.object(evaluator, '_analyze_fit', return_value=Mock(
            fit_score=8,
            should_apply=True,
            confidence=0.85,
            matching_skills=["Python", "AWS"],
            missing_required_skills=[],
            experience_match="exceeds"
        )):
            result = evaluator.evaluate(mock_resume, mock_jd)
            assert result is not None


class TestCaseInsensitiveSkillMatching:
    """Test the case-insensitive skill matching fix"""
    
    def test_skill_matching_is_case_insensitive(self, mock_llm_service):
        """Test that 'Python' matches 'python' and vice versa"""
        from resume_agent.agents.fit_evaluator_agent import _case_insensitive_skill_match
        
        resume_skills = {"Python", "aws", "DOCKER"}
        jd_skills = {"python", "AWS", "Docker", "Kubernetes"}
        
        matching, missing = _case_insensitive_skill_match(resume_skills, jd_skills)
        
        # Should match 3 skills (case-insensitive)
        assert len(matching) == 3
        # Should only miss Kubernetes
        assert len(missing) == 1
        assert "Kubernetes" in missing


class TestWordBoundaryKeywordMatching:
    """Test the word boundary keyword matching fix"""
    
    def test_java_does_not_match_javascript(self):
        """Test that 'Java' doesn't match inside 'JavaScript'"""
        from resume_agent.agents.ats_scorer_agent import _count_keyword_matches
        
        text = "Experience with JavaScript, TypeScript, and NodeJS"
        
        # Java should NOT match inside JavaScript
        java_count = _count_keyword_matches(text, "Java")
        assert java_count == 0
        
        # JavaScript should match
        js_count = _count_keyword_matches(text, "JavaScript")
        assert js_count == 1
    
    def test_exact_word_matching(self):
        """Test exact word boundary matching"""
        from resume_agent.agents.ats_scorer_agent import _count_keyword_matches
        
        text = "Python developer with python scripting experience. PYTHON is great."
        
        # Should match all 3 occurrences (case-insensitive)
        count = _count_keyword_matches(text, "Python")
        assert count == 3
    
    def test_go_does_not_match_google(self):
        """Test that 'Go' language doesn't match inside 'Google'"""
        from resume_agent.agents.ats_scorer_agent import _count_keyword_matches
        
        text = "Worked at Google on Go services. Go is fast."
        
        # Go should match twice (as standalone word)
        go_count = _count_keyword_matches(text, "Go")
        assert go_count == 2
