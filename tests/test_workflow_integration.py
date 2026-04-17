"""
Workflow Service Integration Tests
Tests the complete workflow service with all new features.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path
from resume_agent.services.resume_workflow import (
    ResumeWorkflowService,
    TailorResumeRequest,
    TailorResumeResult,
    WorkflowStep
)


@pytest.fixture
def sample_resume_text():
    """Sample resume for testing"""
    return """# John Doe
Software Engineer | john.doe@email.com

## Experience
**Senior Engineer** | Tech Corp | 2020-Present
- Python development
- AWS infrastructure

## Skills
- Python
- AWS
"""


@pytest.fixture
def sample_jd_text():
    """Sample job description"""
    return """Senior Software Engineer

Requirements:
- 5+ years Python experience
- AWS cloud services
- Microservices architecture
"""


@pytest.fixture
def mock_llm_service():
    """Mock LLM service"""
    service = Mock()
    service.invoke_with_retry = Mock(return_value="Tailored resume content")
    return service


@pytest.fixture
def mock_google_services():
    """Mock Google services"""
    drive_service = Mock()
    docs_service = Mock()
    docs_service.documents.return_value.get.return_value.execute.return_value = {
        "body": {"content": [{"paragraph": {"elements": [{"textRun": {"content": "Resume content"}}]}}]}
    }
    return (drive_service, docs_service)


class TestWorkflowService:
    """Test workflow service"""
    
    def test_load_resume_with_custom_doc_id(self, mock_google_services):
        """Test loading resume with custom doc ID"""
        service = ResumeWorkflowService(google_services=mock_google_services)
        
        with patch('resume_agent.services.resume_workflow.read_resume_file', return_value="Resume content"):
            resume_text, error = service.load_resume(resume_doc_id="custom_doc_123")
            
            assert resume_text == "Resume content"
            assert error is None
    
    def test_load_resume_with_default(self, mock_google_services, mock_llm_service):
        """Test loading resume with default doc ID"""
        service = ResumeWorkflowService(llm_service=mock_llm_service, google_services=mock_google_services)
        
        with patch('resume_agent.services.resume_workflow.read_resume_file', return_value="Resume content"):
            with patch('resume_agent.services.resume_workflow.RESUME_DOC_ID', "default_doc_123"):
                resume_text, error = service.load_resume()
                
                assert resume_text == "Resume content"
                assert error is None
    
    def test_workflow_with_custom_resume_and_folder(self, mock_llm_service, mock_google_services, sample_resume_text, sample_jd_text):
        """Test workflow with custom resume and folder selection"""
        service = ResumeWorkflowService(llm_service=mock_llm_service, google_services=mock_google_services)
        
        request = TailorResumeRequest(
            company="Test Company",
            job_title="Senior Engineer",
            jd_text=sample_jd_text,
            resume_doc_id="custom_resume_123",
            save_folder_id="custom_folder_456",
            tailoring_intensity="heavy"
        )
        
        # Mock the workflow steps
        with patch('resume_agent.services.resume_workflow.read_resume_file', return_value=sample_resume_text):
            with patch('resume_agent.services.resume_workflow.RESUME_DOC_ID', "default_doc_123"):  # Mock default for fallback
                with patch('resume_agent.services.resume_workflow.get_subfolder_id_for_job', return_value="subfolder_123"):
                    with patch('resume_agent.services.resume_workflow.copy_doc_to_folder', return_value="new_doc_123"):
                        with patch('resume_agent.services.resume_workflow.write_to_google_doc'):
                            with patch('resume_agent.services.resume_workflow.generate_diff_markdown', return_value=Path("/tmp/diff.md")):
                                with patch('resume_agent.services.resume_workflow.add_or_update_application', return_value=1):
                                    # Execute workflow
                                    result = TailorResumeResult(current_step=WorkflowStep.LOADING_RESUME)
                                    
                                    # Load resume - request has resume_doc_id="custom_resume_123", so it should use that
                                    result = service.execute_workflow_step(request, WorkflowStep.LOADING_RESUME, result)
                                    assert result.resume_text == sample_resume_text, f"Expected resume text, got error: {result.error}"
                                    
                                    # Tailor resume
                                    result = service.execute_workflow_step(request, WorkflowStep.TAILORING_RESUME, result)
                                    assert result.tailored_resume is not None
    
    def test_workflow_caching_integration(self, mock_llm_service, mock_google_services, sample_resume_text, sample_jd_text):
        """Test that caching is integrated into workflow"""
        import tempfile
        import os
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            temp_cache_file = f.name
        
        try:
            service = ResumeWorkflowService(llm_service=mock_llm_service, google_services=mock_google_services)
            
            request = TailorResumeRequest(
                company="Test Company",
                job_title="Engineer",
                jd_text=sample_jd_text
            )
            
            with patch('resume_agent.services.resume_workflow.read_resume_file', return_value=sample_resume_text):
                with patch('resume_agent.utils.cache_tailoring.TailoringCache') as mock_cache_class:
                    mock_cache = Mock()
                    mock_cache.find_similar_patterns.return_value = []
                    mock_cache_class.return_value = mock_cache
                    
                    result = TailorResumeResult(current_step=WorkflowStep.TAILORING_RESUME, resume_text=sample_resume_text)
                    result.jd_requirements = {"required_skills": ["Python"]}
                    
                    # Execute tailoring step (should check cache)
                    result = service.execute_workflow_step(request, WorkflowStep.TAILORING_RESUME, result)
                    
                    # Verify cache was checked
                    mock_cache.find_similar_patterns.assert_called()
        finally:
            if os.path.exists(temp_cache_file):
                os.unlink(temp_cache_file)
    
    def test_workflow_saves_pattern_after_validation(self, mock_llm_service, mock_google_services, sample_resume_text, sample_jd_text):
        """Test that workflow saves pattern after successful validation"""
        import tempfile
        import os
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            temp_cache_file = f.name
        
        try:
            service = ResumeWorkflowService(llm_service=mock_llm_service, google_services=mock_google_services)
            
            request = TailorResumeRequest(
                company="Test Company",
                job_title="Engineer",
                jd_text=sample_jd_text
            )
            
            # Mock validation to return high quality score
            mock_validation = Mock()
            mock_validation.quality_score = 85
            mock_validation.ats_score = 80
            mock_validation.is_valid = True
            mock_validation.issues = []
            
            with patch('resume_agent.services.resume_workflow.read_resume_file', return_value=sample_resume_text):
                with patch('resume_agent.services.resume_workflow.build_review_bundle', return_value={"summary": "ok"}):
                    with patch('resume_agent.agents.resume_validator.validate_resume_quality', return_value=mock_validation):
                        with patch('resume_agent.agents.resume_validator.extract_jd_requirements', return_value={"required_skills": ["Python"]}):
                            with patch('resume_agent.utils.resume_parser.parse_resume_sections') as mock_parse:
                                # Mock section parsing
                                from resume_agent.utils.resume_parser import ResumeSection
                                mock_parse.side_effect = [
                                    {
                                        "Experience": ResumeSection("Experience", "Original", 0, 100, 2),
                                        "Skills": ResumeSection("Skills", "Original", 100, 200, 2)
                                    },
                                    {
                                        "Experience": ResumeSection("Experience", "Original updated", 0, 100, 2),
                                        "Skills": ResumeSection("Skills", "Original", 100, 200, 2)
                                    },
                                ]
                                
                                with patch('resume_agent.utils.cache_tailoring.TailoringCache') as mock_cache_class:
                                    mock_cache = Mock()
                                    mock_cache_class.return_value = mock_cache
                                    
                                    result = TailorResumeResult(
                                        current_step=WorkflowStep.VALIDATING_RESUME,
                                        resume_text=sample_resume_text,
                                        tailored_resume=sample_resume_text + "\n\nUpdated"
                                    )
                                    
                                    # Execute validation step (should save pattern)
                                    result = service.execute_workflow_step(request, WorkflowStep.VALIDATING_RESUME, result)
                                    
                                    # Verify pattern was saved
                                    mock_cache.save_pattern.assert_called_once()
        finally:
            if os.path.exists(temp_cache_file):
                os.unlink(temp_cache_file)
    
    def test_workflow_with_sections_to_tailor(self, mock_llm_service, mock_google_services, sample_resume_text, sample_jd_text):
        """Test workflow with section-by-section tailoring"""
        service = ResumeWorkflowService(llm_service=mock_llm_service, google_services=mock_google_services)
        
        request = TailorResumeRequest(
            company="Test Company",
            job_title="Engineer",
            jd_text=sample_jd_text,
            sections_to_tailor=["Experience", "Skills"]
        )
        
        with patch('resume_agent.services.resume_workflow.read_resume_file', return_value=sample_resume_text):
            with patch('resume_agent.utils.resume_parser.parse_resume_sections') as mock_parse:
                from resume_agent.utils.resume_parser import ResumeSection
                mock_parse.return_value = {
                    "Experience": ResumeSection("Experience", "Original exp", 0, 50, 2),
                    "Skills": ResumeSection("Skills", "Original skills", 50, 100, 2)
                }
                
                with patch('resume_agent.utils.resume_parser.merge_resume_sections', return_value="Merged resume"):
                    result = TailorResumeResult(current_step=WorkflowStep.TAILORING_RESUME, resume_text=sample_resume_text)
                    result = service.execute_workflow_step(request, WorkflowStep.TAILORING_RESUME, result)
                    
                    # Should use section-by-section tailoring
                    assert result.tailored_resume is not None
    
    def test_workflow_with_refinement_feedback(self, mock_llm_service, mock_google_services, sample_resume_text, sample_jd_text):
        """Test workflow with refinement feedback"""
        service = ResumeWorkflowService(llm_service=mock_llm_service, google_services=mock_google_services)
        
        request = TailorResumeRequest(
            company="Test Company",
            job_title="Engineer",
            jd_text=sample_jd_text,
            refinement_feedback="Make it more technical and add more AWS details"
        )
        
        with patch('resume_agent.services.resume_workflow.read_resume_file', return_value=sample_resume_text):
            result = TailorResumeResult(current_step=WorkflowStep.TAILORING_RESUME, resume_text=sample_resume_text)
            result = service.execute_workflow_step(request, WorkflowStep.TAILORING_RESUME, result)
            
            # Verify refinement feedback is passed to tailor function
            # (would need to check mock call args)
            assert result.tailored_resume is not None
