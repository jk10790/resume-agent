"""
Unit tests for LLMService (provider-based).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from langchain_core.messages import SystemMessage

from resume_agent.services.llm_service import LLMService
from resume_agent.models.resume import FitEvaluation


class _DummyCacheStore:
    def get(self, *_args: Any, **_kwargs: Any):
        return None

    def put(self, *_args: Any, **_kwargs: Any):
        return None


class _DummyProvider:
    def __init__(self, response_text: str):
        self._response_text = response_text
        self.invoke_call_count = 0

    def get_model_name(self) -> str:
        return "dummy-model"

    def invoke(self, _messages):
        self.invoke_call_count += 1
        return self._response_text


@pytest.fixture
def llm_service_and_provider():
    provider = _DummyProvider(
        '{"score": 8, "should_apply": true, "matching_areas": ["Python"], "missing_areas": [], "recommendations": [], "confidence": 0.9}'
    )
    with (
        patch("resume_agent.services.llm_service.create_provider", return_value=provider),
        patch("resume_agent.services.llm_service.get_cache_store", return_value=_DummyCacheStore()),
    ):
        service = LLMService(provider_type="openai", model_name="dummy", api_key="x")
        return service, provider


def test_invoke_with_retry_success(llm_service_and_provider):
    service, provider = llm_service_and_provider
    messages = [SystemMessage(content="Test")]
    result = service.invoke_with_retry(messages, use_cache=False, max_retries=1)
    assert provider.invoke_call_count == 1
    assert isinstance(result, str)


def test_invoke_with_cache_in_memory(llm_service_and_provider):
    service, provider = llm_service_and_provider
    messages = [SystemMessage(content="Test")]

    result1 = service.invoke_with_retry(messages, use_cache=True, max_retries=1)
    result2 = service.invoke_with_retry(messages, use_cache=True, max_retries=1)

    assert provider.invoke_call_count == 1
    assert result1 == result2


def test_invoke_structured_json(llm_service_and_provider):
    service, _provider = llm_service_and_provider
    messages = [SystemMessage(content="Test")]
    result = service.invoke_structured(messages, max_retries=1)
    assert isinstance(result, dict)
    assert result["score"] == 8


def test_evaluate_fit_structured(llm_service_and_provider):
    service, _provider = llm_service_and_provider

    class _T:
        def format_messages(self, **_kwargs):
            return [SystemMessage(content="System")]

    with patch("resume_agent.prompts.templates.get_prompt", return_value=_T()):
        evaluation = service.evaluate_fit_structured(
            resume_text="Python developer with AWS experience",
            jd_text="Looking for Python developer with AWS",
            known_skills=["Python", "AWS"],
            prompt_version="latest",
        )
    assert isinstance(evaluation, FitEvaluation)
    assert evaluation.score == 8
    assert evaluation.should_apply is True

