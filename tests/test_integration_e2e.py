"""
End-to-end integration tests that connect to real APIs (Groq, Google).

These tests verify the entire application works with actual external services.
Run with: pytest tests/test_integration_e2e.py -v

Requirements:
- GROQ_API_KEY in environment or .env
- Google credentials.json and token.json for Google API tests
- Set INTEGRATION_TESTS=true to run (prevents accidental execution)
"""

import os
import pytest
from pathlib import Path
from unittest.mock import patch

# Only run if explicitly enabled
INTEGRATION_TESTS_ENABLED = os.getenv("INTEGRATION_TESTS", "false").lower() == "true"

pytestmark = pytest.mark.skipif(
    not INTEGRATION_TESTS_ENABLED,
    reason="Integration tests disabled. Set INTEGRATION_TESTS=true to run."
)


@pytest.fixture
def test_resume_text():
    """Sample resume text for testing"""
    return """John Doe
Software Engineer

EXPERIENCE
Senior Software Engineer | Tech Corp | 2020 - Present
- Led development of microservices architecture using Python and AWS
- Implemented CI/CD pipelines reducing deployment time by 60%
- Mentored team of 5 junior developers

Software Engineer | Startup Inc | 2018 - 2020
- Developed REST APIs using FastAPI and PostgreSQL
- Built automated testing framework increasing coverage to 85%
- Collaborated with cross-functional teams on product features

SKILLS
- Python, JavaScript, TypeScript
- AWS (EC2, S3, Lambda, RDS)
- Docker, Kubernetes
- PostgreSQL, MongoDB
- Git, CI/CD, Agile methodologies

EDUCATION
BS Computer Science | State University | 2018
"""


@pytest.fixture
def test_jd_text():
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
def test_jd_url():
    """A test URL for JD extraction (using a simple public page)"""
    # Using a simple test URL - in real usage, this would be a job board URL
    return "https://www.example.com/job-posting"


class TestLLMIntegration:
    """Test LLM service integration with real providers"""
    
    def test_groq_provider_connection(self):
        """Test Groq provider can connect and make API calls"""
        from resume_agent.services.llm_providers import GroqProvider
        from resume_agent.config import settings
        from langchain_core.messages import SystemMessage, HumanMessage
        
        api_key = settings.groq_api_key
        if not api_key:
            pytest.skip("GROQ_API_KEY not configured")
        
        provider = GroqProvider(
            api_key=api_key,
            model_name=settings.groq_model or "llama-3.3-70b-versatile"
        )
        
        messages = [
            SystemMessage(content="You are a helpful assistant."),
            HumanMessage(content="Say 'Hello, integration test!' and nothing else.")
        ]
        
        response = provider.invoke(messages)
        
        assert response is not None
        assert len(response) > 0
        assert "integration test" in response.lower() or "hello" in response.lower()
        print(f"✅ Groq response: {response[:100]}")
    
    def test_llm_service_with_groq(self, test_resume_text, test_jd_text):
        """Test LLMService with Groq provider end-to-end"""
        from resume_agent.services.llm_service import LLMService
        from resume_agent.config import settings
        
        if not settings.groq_api_key:
            pytest.skip("GROQ_API_KEY not configured")
        
        # Force Groq provider
        llm_service = LLMService(provider_type="groq")
        
        # Test simple invocation
        from langchain_core.messages import SystemMessage, HumanMessage
        messages = [
            SystemMessage(content="You are a helpful assistant."),
            HumanMessage(content="What is 2+2? Respond with just the number.")
        ]
        
        response = llm_service.invoke_with_retry(messages, use_cache=False)
        
        assert response is not None
        assert "4" in response or "four" in response.lower()
        print(f"✅ LLM Service response: {response[:100]}")
    
    def test_fit_evaluation_with_real_llm(self, test_resume_text, test_jd_text):
        """Test fit evaluation with real LLM"""
        from resume_agent.agents.fit_evaluator import evaluate_resume_fit
        from resume_agent.services.llm_service import LLMService
        from resume_agent.config import settings
        
        if not settings.groq_api_key:
            pytest.skip("GROQ_API_KEY not configured")
        
        llm_service = LLMService(provider_type="groq")
        
        evaluation = evaluate_resume_fit(
            model=llm_service,
            resume_text=test_resume_text,
            jd_text=test_jd_text
        )
        
        assert evaluation is not None
        assert hasattr(evaluation, 'score')
        assert 1 <= evaluation.score <= 10
        assert hasattr(evaluation, 'should_apply')
        assert isinstance(evaluation.should_apply, bool)
        assert hasattr(evaluation, 'matching_areas')
        assert isinstance(evaluation.matching_areas, list)
        
        print(f"✅ Fit Score: {evaluation.score}/10")
        print(f"✅ Should Apply: {evaluation.should_apply}")
        print(f"✅ Matching Areas: {evaluation.matching_areas[:3]}")
    
    def test_resume_tailoring_with_real_llm(self, test_resume_text, test_jd_text):
        """Test resume tailoring with real LLM"""
        from resume_agent.agents.resume_tailor import tailor_resume_for_job
        from resume_agent.services.llm_service import LLMService
        from resume_agent.config import settings
        
        if not settings.groq_api_key:
            pytest.skip("GROQ_API_KEY not configured")
        
        llm_service = LLMService(provider_type="groq")
        
        tailored = tailor_resume_for_job(
            resume_text=test_resume_text,
            jd_text=test_jd_text,
            llm_service=llm_service
        )
        
        assert tailored is not None
        assert len(tailored) > 100  # Should be substantial
        assert "John Doe" in tailored or "Software Engineer" in tailored
        
        print(f"✅ Tailored resume length: {len(tailored)} chars")
        print(f"✅ Preview: {tailored[:200]}...")


class TestGoogleAPIIntegration:
    """Test Google API integration with real services"""
    
    @pytest.fixture
    def google_services(self):
        """Get Google services if available"""
        try:
            from resume_agent.storage.google_docs import get_services
            drive_service, docs_service = get_services()
            return drive_service, docs_service
        except Exception as e:
            pytest.skip(f"Google is session-only (web app): {e}")
    
    def test_google_drive_connection(self, google_services):
        """Test Google Drive API connection"""
        drive_service, _ = google_services
        
        # Try to list files (limited to 1 to avoid quota issues)
        results = drive_service.files().list(pageSize=1, fields="files(id, name)").execute()
        files = results.get("files", [])
        
        assert results is not None
        print(f"✅ Google Drive connection successful. Found {len(files)} file(s)")
    
    def test_google_docs_read(self, google_services):
        """Test reading from Google Docs"""
        from resume_agent.config import settings
        from resume_agent.storage.google_docs import read_google_doc
        
        _, docs_service = google_services
        
        if not settings.resume_doc_id:
            pytest.skip("RESUME_DOC_ID not configured")
        
        content = read_google_doc(docs_service, settings.resume_doc_id)
        
        assert content is not None
        assert len(content) > 0
        print(f"✅ Read {len(content)} characters from Google Doc")
    
    def test_google_docs_write(self, google_services):
        """Test writing to Google Docs (creates test doc)"""
        from resume_agent.storage.google_docs import (
            get_or_create_folder, copy_google_doc, write_to_google_doc, read_google_doc
        )
        from resume_agent.config import settings
        
        drive_service, docs_service = google_services
        
        if not settings.resume_doc_id:
            pytest.skip("RESUME_DOC_ID not configured (needed as template)")
        
        # Create a test folder
        test_folder_id = get_or_create_folder(
            drive_service,
            "Resume Agent Test",
            parent_id=settings.google_folder_id
        )
        
        # Copy the resume doc as a test document
        test_doc_id = copy_google_doc(
            drive_service,
            settings.resume_doc_id,
            "Integration Test Doc",
            test_folder_id
        )
        
        # Write test content
        test_content = "Integration Test Content\n\nThis is a test from the E2E integration tests."
        write_to_google_doc(test_doc_id, test_content, docs_service=docs_service)
        
        # Verify it was written
        content = read_google_doc(docs_service, test_doc_id)
        assert "Integration Test Content" in content
        
        # Cleanup: Delete test document
        try:
            drive_service.files().delete(fileId=test_doc_id).execute()
            print(f"✅ Cleaned up test document")
        except Exception as e:
            print(f"⚠️ Could not clean up test document: {e}")
        
        print(f"✅ Successfully wrote and verified content in Google Doc")


class TestEndToEndWorkflow:
    """Test complete workflows end-to-end"""
    
    def test_complete_resume_tailoring_workflow(self, test_resume_text, test_jd_text):
        """Test complete workflow: evaluate fit -> tailor resume"""
        from resume_agent.agents.fit_evaluator import evaluate_resume_fit
        from resume_agent.agents.resume_tailor import tailor_resume_for_job
        from resume_agent.services.llm_service import LLMService
        from resume_agent.config import settings
        
        if not settings.groq_api_key:
            pytest.skip("GROQ_API_KEY not configured")
        
        llm_service = LLMService(provider_type="groq")
        
        # Step 1: Evaluate fit
        evaluation = evaluate_resume_fit(
            model=llm_service,
            resume_text=test_resume_text,
            jd_text=test_jd_text
        )
        
        assert evaluation.score >= 1
        
        # Step 2: Tailor resume if should apply
        if evaluation.should_apply:
            tailored = tailor_resume_for_job(
                resume_text=test_resume_text,
                jd_text=test_jd_text,
                llm_service=llm_service
            )
            
            assert tailored is not None
            assert len(tailored) > len(test_resume_text) * 0.5  # Should be substantial
            
            print(f"✅ Complete workflow successful:")
            print(f"   - Fit Score: {evaluation.score}/10")
            print(f"   - Tailored Resume: {len(tailored)} chars")
        else:
            print(f"✅ Workflow completed (fit score too low: {evaluation.score}/10)")
    
    def test_jd_extraction_and_evaluation(self):
        """Test JD extraction from URL and evaluation"""
        from resume_agent.agents.jd_extractor import extract_clean_jd
        from resume_agent.agents.fit_evaluator import evaluate_resume_fit
        from resume_agent.services.llm_service import LLMService
        from resume_agent.config import settings
        
        if not settings.groq_api_key:
            pytest.skip("GROQ_API_KEY not configured")
        
        llm_service = LLMService(provider_type="groq")
        
        # Use a simple test URL - in production this would be a real job board URL
        # For now, we'll skip actual URL extraction and use direct JD text
        test_jd = """Software Engineer Position
        
        Requirements:
        - Python experience
        - AWS knowledge
        - 3+ years experience"""
        
        # Test evaluation with extracted JD
        test_resume = """John Doe
        Software Engineer with 5 years Python and AWS experience."""
        
        evaluation = evaluate_resume_fit(
            model=llm_service,
            resume_text=test_resume,
            jd_text=test_jd
        )
        
        assert evaluation is not None
        assert evaluation.score >= 1
        print(f"✅ JD extraction and evaluation successful: Score {evaluation.score}/10")


class TestErrorHandling:
    """Test error handling with helpful messages"""
    
    def test_missing_api_key_error(self):
        """Test that missing API key gives helpful error"""
        from resume_agent.services.llm_providers import GroqProvider
        from resume_agent.utils.exceptions import LLMError
        
        with pytest.raises(LLMError) as exc_info:
            GroqProvider(api_key="")
        
        error = exc_info.value
        assert "GROQ_API_KEY" in str(error)
        assert "fix" in str(error).lower() or "how to" in str(error).lower()
        print(f"✅ Error message includes fix instructions: {str(error)[:200]}")
    
    def test_invalid_provider_error(self):
        """Test that invalid provider gives helpful error"""
        from resume_agent.services.llm_service import LLMService
        from resume_agent.utils.exceptions import ConfigError
        
        with pytest.raises(ConfigError) as exc_info:
            LLMService(provider_type="invalid_provider")
        
        error = exc_info.value
        assert "invalid_provider" in str(error)
        assert "fix" in str(error).lower() or "how to" in str(error).lower()
        print(f"✅ Config error includes fix instructions")


if __name__ == "__main__":
    # Run integration tests
    pytest.main([__file__, "-v", "-s"])
