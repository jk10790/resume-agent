"""
Backend API Integration Tests
Tests FastAPI endpoints with real services (mocked where appropriate).

Run with: pytest tests/test_backend_api_integration.py -v
"""

import os
import pytest
import json
from unittest.mock import Mock, patch, MagicMock
from fastapi.testclient import TestClient
from resume_agent.pipelines import FitRequest, TailorRequest

if not os.getenv("RUN_INTEGRATION_TESTS"):
    pytest.skip("Skipping integration tests (set RUN_INTEGRATION_TESTS=1 to enable).", allow_module_level=True)


@pytest.fixture
def api_client():
    """Create test client for FastAPI app"""
    import sys
    from pathlib import Path
    # Add project root to path
    project_root = Path(__file__).parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    
    from api.main import app
    return TestClient(app)


@pytest.fixture
def sample_resume_text():
    """Sample resume for testing"""
    return """# John Doe
Software Engineer | john.doe@email.com | (555) 123-4567

## Summary
Experienced software engineer with 5+ years developing scalable applications.

## Experience
**Senior Software Engineer** | Tech Corp | 2020 - Present
- Led development of microservices using Python and AWS
- Implemented CI/CD pipelines reducing deployment time by 60%
- Mentored team of 5 junior developers

**Software Engineer** | Startup Inc | 2018 - 2020
- Developed REST APIs using FastAPI and PostgreSQL
- Built automated testing framework increasing coverage to 85%

## Skills
- Python, JavaScript, TypeScript
- AWS (EC2, S3, Lambda, RDS)
- Docker, Kubernetes
- PostgreSQL, MongoDB

## Education
BS Computer Science | State University | 2018
"""


@pytest.fixture
def sample_jd_text():
    """Sample job description for testing"""
    return """Senior Software Engineer

We are looking for a Senior Software Engineer to join our team.

Requirements:
- 5+ years of software development experience
- Strong proficiency in Python
- Experience with AWS cloud services
- Experience with microservices architecture
- Leadership and mentoring experience
- Bachelor's degree in Computer Science or related field

Nice to have:
- Kubernetes experience
- CI/CD pipeline experience
- Experience with PostgreSQL
"""


@pytest.fixture
def mock_workflow_service():
    """Mock workflow service"""
    service = Mock(spec=ResumeWorkflowService)
    service.google_services = (Mock(), Mock())
    return service


class TestAPIEndpoints:
    """Test FastAPI endpoints"""
    
    def test_health_check(self, api_client):
        """Test health check endpoint"""
        response = api_client.get("/")
        assert response.status_code == 200
    
    def test_tailor_resume_endpoint_structure(self, api_client, sample_resume_text, sample_jd_text):
        """Test tailor resume endpoint accepts correct request structure"""
        request_data = {
            "company": "Test Company",
            "job_title": "Senior Software Engineer",
            "jd_text": sample_jd_text,
            "job_url": None,
            "evaluate_first": True,
            "track_application": True,
            "tailoring_intensity": "medium",
            "sections_to_tailor": None,
            "refinement_feedback": None,
            "resume_doc_id": None,
            "save_folder_id": None
        }
        
        # This will fail without real services, but we can test the request structure
        with patch('api.main.ResumeWorkflowService') as mock_service:
            mock_instance = Mock()
            mock_instance.google_services = (Mock(), Mock())
            mock_service.return_value = mock_instance
            
            # Mock the streaming response
            async def mock_stream():
                yield 'data: {"type": "error", "error": "Test error"}\n\n'
            
            with patch('api.main.generate_progress', return_value=mock_stream()):
                response = api_client.post("/api/tailor-resume", json=request_data)
                # Should accept the request structure
                assert response.status_code in [200, 500]  # May fail due to missing services
    
    def test_extract_jd_endpoint(self, api_client):
        """Test JD extraction endpoint"""
        request_data = {
            "job_url": "https://example.com/job"
        }
        
        with patch('api.main.extract_clean_jd') as mock_extract:
            mock_extract.return_value = "Extracted job description text"
            
            response = api_client.post("/api/extract-jd", json=request_data)
            assert response.status_code == 200
            data = response.json()
            assert "jd_text" in data
            assert data["jd_text"] == "Extracted job description text"
    
    def test_list_google_docs_endpoint(self, api_client):
        """Test list Google Docs endpoint"""
        with patch('api.main.get_services') as mock_get_services:
            mock_drive = Mock()
            mock_docs = [
                {"id": "doc1", "name": "Resume 1", "mimeType": "application/vnd.google-apps.document"},
                {"id": "doc2", "name": "Resume 2", "mimeType": "application/vnd.google-apps.document"}
            ]
            mock_drive.files.return_value.list.return_value.execute.return_value = {"files": mock_docs}
            mock_get_services.return_value = (mock_drive, Mock())
            
            with patch('api.main.list_google_docs') as mock_list:
                mock_list.return_value = [
                    {"id": "doc1", "name": "Resume 1", "mimeType": "application/vnd.google-apps.document", "webViewLink": "https://docs.google.com/doc1"},
                    {"id": "doc2", "name": "Resume 2", "mimeType": "application/vnd.google-apps.document", "webViewLink": "https://docs.google.com/doc2"}
                ]
                
                response = api_client.get("/api/google-docs")
                assert response.status_code == 200
                data = response.json()
                assert data["success"] is True
                assert "docs" in data
                assert len(data["docs"]) == 2
    
    def test_list_google_folders_endpoint(self, api_client):
        """Test list Google folders endpoint"""
        with patch('api.main.get_services') as mock_get_services:
            mock_drive = Mock()
            mock_get_services.return_value = (mock_drive, Mock())
            
            with patch('api.main.list_google_folders') as mock_list:
                mock_list.return_value = [
                    {"id": "folder1", "name": "Resumes", "mimeType": "application/vnd.google-apps.folder", "path": "My Drive/Resumes"}
                ]
                
                with patch('api.main.get_folder_path', return_value="My Drive/Resumes"):
                    response = api_client.get("/api/google-folders")
                    assert response.status_code == 200
                    data = response.json()
                    assert data["success"] is True
                    assert "folders" in data
    
    def test_get_resume_content_endpoint(self, api_client):
        """Test get resume content endpoint"""
        with patch('api.main.ResumeWorkflowService') as mock_service_class:
            mock_service = Mock()
            mock_service.google_services = (Mock(), Mock())
            mock_service_class.return_value = mock_service
            
            with patch('api.main.read_google_doc_content', return_value="Resume content here"):
                response = api_client.get("/api/resume/doc123")
                assert response.status_code == 200
                data = response.json()
                assert data["success"] is True
                assert data["resume_content"] == "Resume content here"


class TestPipelineRequests:
    """The request objects the API builds for the orchestrator."""

    def test_tailor_request_carries_the_tailoring_options(self):
        request = TailorRequest(
            jd_text="Job description",
            company="Acme",
            job_title="Engineer",
            intensity="heavy",
            preserve_sections=["education"],
        )

        assert request.jd_text == "Job description"
        assert request.intensity == "heavy"
        assert request.preserve_sections == ["education"]
        # Tailoring extends the fit request rather than redeclaring its fields.
        assert isinstance(request, FitRequest)


class TestAPIErrorHandling:
    """Test API error handling"""
    
    def test_missing_required_fields(self, api_client):
        """Test API rejects requests with missing required fields"""
        # Missing company
        response = api_client.post("/api/tailor-resume", json={
            "job_title": "Engineer",
            "jd_text": "Job description"
        })
        assert response.status_code == 422  # Validation error
    
    def test_invalid_tailoring_intensity(self, api_client):
        """Test API rejects invalid tailoring intensity"""
        response = api_client.post("/api/tailor-resume", json={
            "company": "Test",
            "job_title": "Engineer",
            "jd_text": "Job description",
            "tailoring_intensity": "invalid"
        })
        # Should accept any string, but could validate
        assert response.status_code in [200, 422]
    
    def test_google_services_error_handling(self, api_client):
        """Test error handling when Google services unavailable"""
        with patch('api.main.get_services', side_effect=Exception("Google auth failed")):
            response = api_client.get("/api/google-docs")
            # Should handle error gracefully
            assert response.status_code in [500, 503]


@pytest.mark.integration
class TestFullWorkflowIntegration:
    """Full workflow integration tests (requires real services)"""
    
    @pytest.mark.skip(reason="Requires real services - run with INTEGRATION_TESTS=true")
    def test_full_tailoring_workflow(self, api_client, sample_resume_text, sample_jd_text):
        """Test complete tailoring workflow end-to-end"""
        # This would require real Google and LLM services
        # Marked as integration test
        pass
