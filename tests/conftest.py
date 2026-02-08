"""
Pytest configuration and fixtures.
"""

import pytest
import tempfile
import shutil
from pathlib import Path


@pytest.fixture(scope="session")
def temp_base_dir():
    """Create base temporary directory for all tests"""
    temp_path = tempfile.mkdtemp()
    yield Path(temp_path)
    shutil.rmtree(temp_path)


@pytest.fixture
def sample_resume():
    """Sample resume for testing"""
    from resume_agent.models.resume import Resume
    return Resume(
        content="""John Doe
Software Engineer

Experience:
- 5 years Python development
- AWS cloud infrastructure
- Team leadership""",
        version="1.0",
        source="test"
    )


@pytest.fixture
def sample_jd():
    """Sample job description for testing"""
    from resume_agent.models.resume import JobDescription
    return JobDescription(
        title="Senior Software Engineer",
        company="Tech Corp",
        content="""Looking for Senior Software Engineer with:
- 5+ years Python experience
- AWS cloud experience
- Leadership skills""",
        url="http://example.com/job"
    )


@pytest.fixture
def mock_llm_service():
    """Mock LLM service for testing"""
    from unittest.mock import Mock
    from resume_agent.services.llm_service import LLMService
    
    service = Mock(spec=LLMService)
    service.model = Mock()
    service.invoke_with_retry = Mock(return_value='{"score": 8, "should_apply": true}')
    service.invoke_structured = Mock(return_value={
        "score": 8,
        "should_apply": True,
        "matching_areas": ["Python", "AWS"],
        "missing_areas": [],
        "recommendations": [],
        "confidence": 0.9
    })
    
    return service
