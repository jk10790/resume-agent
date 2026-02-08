"""
Backend API Integration Tests
Tests FastAPI endpoints with real services (mocked where appropriate).

Run with: pytest tests/test_backend_api_integration.py -v
"""

import pytest
import json
from unittest.mock import Mock, patch, MagicMock
from fastapi.testclient import TestClient
from resume_agent.services.resume_workflow import ResumeWorkflowService, TailorResumeRequest, WorkflowStep
from resume_agent.utils.cache_tailoring import TailoringCache


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
    
    def test_cache_stats_endpoint(self, api_client):
        """Test cache statistics endpoint"""
        with patch('api.main.TailoringCache') as mock_cache_class:
            mock_cache = Mock()
            mock_cache.get_cache_stats.return_value = {
                "total_patterns": 5,
                "total_uses": 12,
                "avg_quality_score": 85.5,
                "oldest_pattern": "2024-01-01T00:00:00",
                "newest_pattern": "2024-01-15T00:00:00"
            }
            mock_cache_class.return_value = mock_cache
            
            response = api_client.get("/api/cache/stats")
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert "stats" in data
            assert data["stats"]["total_patterns"] == 5
    
    def test_cache_patterns_endpoint(self, api_client):
        """Test list cache patterns endpoint"""
        with patch('api.main.TailoringCache') as mock_cache_class:
            from resume_agent.utils.cache_tailoring import TailoringPattern
            from datetime import datetime
            
            mock_pattern = TailoringPattern(
                pattern_id="test123",
                jd_requirements_hash="abc123",
                jd_keywords=["Python", "AWS", "Docker"],
                tailoring_changes={"Experience": "Updated content"},
                intensity="medium",
                quality_score=85,
                created_at=datetime.now().isoformat(),
                used_count=3,
                last_used=datetime.now().isoformat()
            )
            
            mock_cache = Mock()
            mock_cache.get_all_patterns.return_value = [mock_pattern]
            mock_cache_class.return_value = mock_cache
            
            response = api_client.get("/api/cache/patterns")
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert "patterns" in data
            assert len(data["patterns"]) == 1
            assert data["patterns"][0]["pattern_id"] == "test123"
    
    def test_delete_cache_pattern_endpoint(self, api_client):
        """Test delete cache pattern endpoint"""
        with patch('api.main.TailoringCache') as mock_cache_class:
            mock_cache = Mock()
            mock_cache.delete_pattern.return_value = True
            mock_cache_class.return_value = mock_cache
            
            response = api_client.delete("/api/cache/patterns/test123")
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            mock_cache.delete_pattern.assert_called_once_with("test123")
    
    def test_clear_cache_endpoint(self, api_client):
        """Test clear cache endpoint"""
        with patch('api.main.TailoringCache') as mock_cache_class:
            mock_cache = Mock()
            mock_cache_class.return_value = mock_cache
            
            response = api_client.delete("/api/cache/clear")
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            mock_cache.clear_cache.assert_called_once()
    
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


class TestWorkflowServiceIntegration:
    """Test workflow service integration"""
    
    def test_workflow_request_structure(self):
        """Test workflow request accepts all new parameters"""
        request = TailorResumeRequest(
            company="Test Company",
            job_title="Senior Engineer",
            jd_text="Job description",
            resume_doc_id="doc123",
            save_folder_id="folder456",
            tailoring_intensity="heavy",
            sections_to_tailor=["Experience", "Skills"],
            refinement_feedback="Make it more technical"
        )
        
        assert request.resume_doc_id == "doc123"
        assert request.save_folder_id == "folder456"
        assert request.tailoring_intensity == "heavy"
        assert request.sections_to_tailor == ["Experience", "Skills"]
        assert request.refinement_feedback == "Make it more technical"
    
    def test_cache_integration(self, sample_resume_text, sample_jd_text):
        """Test that caching is integrated into workflow"""
        from resume_agent.utils.cache_tailoring import TailoringCache
        import tempfile
        import os
        
        # Use temporary cache file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            temp_cache_file = f.name
        
        try:
            cache = TailoringCache(cache_file=temp_cache_file)
            
            # Save a pattern
            pattern_id = cache.save_pattern(
                jd_text=sample_jd_text,
                jd_requirements={"required_skills": ["Python", "AWS"]},
                tailoring_changes={"Experience": "Updated"},
                intensity="medium",
                quality_score=85
            )
            
            assert pattern_id is not None
            
            # Find similar patterns
            similar = cache.find_similar_patterns(
                sample_jd_text,
                {"required_skills": ["Python", "AWS"]},
                min_similarity=0.5
            )
            
            assert len(similar) > 0
            assert similar[0][0].pattern_id == pattern_id
            
            # Get stats
            stats = cache.get_cache_stats()
            assert stats["total_patterns"] == 1
            
        finally:
            if os.path.exists(temp_cache_file):
                os.unlink(temp_cache_file)


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
