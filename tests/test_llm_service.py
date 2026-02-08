"""
Unit tests for LLM service.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from langchain_core.messages import SystemMessage, HumanMessage
from resume_agent.services.llm_service import LLMService
from resume_agent.models.resume import FitEvaluation


class TestLLMService:
    """Tests for LLMService"""
    
    @pytest.fixture
    def mock_model(self):
        """Mock LLM model"""
        model = Mock()
        model.invoke = Mock(return_value='{"score": 8, "should_apply": true, "matching_areas": ["Python"], "missing_areas": [], "recommendations": [], "confidence": 0.9}')
        return model
    
    @pytest.fixture
    def llm_service(self, mock_model):
        """Create LLMService with mocked model"""
        service = LLMService("test-model")
        service.model = mock_model
        return service
    
    def test_invoke_with_retry_success(self, llm_service, mock_model):
        """Test successful invocation"""
        messages = [SystemMessage(content="Test")]
        result = llm_service.invoke_with_retry(messages)
        assert mock_model.invoke.called
        assert result is not None
    
    def test_invoke_with_cache(self, llm_service, mock_model):
        """Test caching functionality"""
        messages = [SystemMessage(content="Test")]
        
        # First call
        result1 = llm_service.invoke_with_retry(messages, use_cache=True)
        
        # Second call should use cache
        result2 = llm_service.invoke_with_retry(messages, use_cache=True)
        
        # Model should only be called once
        assert mock_model.invoke.call_count == 1
        assert result1 == result2
    
    def test_invoke_structured_json(self, llm_service, mock_model):
        """Test structured JSON output"""
        messages = [SystemMessage(content="Test")]
        result = llm_service.invoke_structured(messages)
        
        assert isinstance(result, dict)
        assert "score" in result
        assert result["score"] == 8
    
    def test_evaluate_fit_structured(self, llm_service, mock_model):
        """Test fit evaluation with structured output"""
        resume_text = "Python developer with AWS experience"
        jd_text = "Looking for Python developer with AWS"
        skills = ["Python", "AWS"]
        
            with patch('resume_agent.services.llm_service.get_prompt') as mock_prompt:
            mock_template = Mock()
            mock_template.format_messages.return_value = [
                SystemMessage(content="System"),
                HumanMessage(content="Human")
            ]
            mock_prompt.return_value = mock_template
            
            evaluation = llm_service.evaluate_fit_structured(
                resume_text, jd_text, skills
            )
            
            assert isinstance(evaluation, FitEvaluation)
            assert evaluation.score == 8
            assert evaluation.should_apply is True
    
    def test_parse_fit_evaluation_text_fallback(self, llm_service):
        """Test fallback text parsing"""
        text = """
        Fit Score: 7/10
        Should Apply: Yes
        
        Top Matching Areas:
        - Python
        - AWS
        
        Missing Areas:
        - Kubernetes
        """
        
        evaluation = llm_service._parse_fit_evaluation_text(text)
        
        assert isinstance(evaluation, FitEvaluation)
        assert evaluation.score == 7
        assert evaluation.should_apply is True
        assert "Python" in evaluation.matching_areas
