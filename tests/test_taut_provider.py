"""Unit tests for the taut middleware provider.

The pipeline itself is taut's to test; what matters here is the boundary: the
LangChain -> taut message mapping, whether routing is left to decide the model,
and that usage/cost from the response reaches LLMService's metadata.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from resume_agent.services.llm_providers import TautProvider, create_provider
from resume_agent.services.llm_service import LLMService
from resume_agent.utils.exceptions import LLMError


class _FakeUsage:
    def __init__(self, input_tokens: int = 120, output_tokens: int = 40, cached_tokens: int = 0):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cached_tokens = cached_tokens


class _FakeResponse:
    def __init__(self, content: str = '{"ok": true}', model: str = "anthropic/claude-haiku-4-5-20251001"):
        self.content = content
        self.model = model
        self.usage = _FakeUsage()
        self.cost_usd = 0.00031


class _FakePipeline:
    def __init__(self, response: _FakeResponse | None = None):
        self.requests: list[Any] = []
        self._response = response or _FakeResponse()

    def run_sync(self, request):
        self.requests.append(request)
        return self._response


class _DummyCacheStore:
    def get(self, *_args: Any, **_kwargs: Any):
        return None

    def put(self, *_args: Any, **_kwargs: Any):
        return None


TIERS = {
    "simple": ["anthropic/claude-haiku-4-5-20251001"],
    "standard": ["anthropic/claude-sonnet-4-5-20250929"],
    "complex": ["anthropic/claude-sonnet-4-5-20250929"],
}


def _provider(pipeline: _FakePipeline, **kwargs) -> TautProvider:
    with patch("taut.create_pipeline", return_value=pipeline):
        return TautProvider(
            api_key="test-key",
            default_model="anthropic/claude-sonnet-4-5-20250929",
            tiers=kwargs.pop("tiers", TIERS),
            **kwargs,
        )


def test_missing_api_key_raises():
    with pytest.raises(LLMError):
        TautProvider(api_key="", default_model="anthropic/claude-sonnet-4-5-20250929")


def test_messages_are_mapped_to_taut_roles():
    pipeline = _FakePipeline()
    provider = _provider(pipeline)

    provider.invoke(
        [
            SystemMessage(content="You are a RESUME PARSER."),
            HumanMessage(content="Resume text"),
            AIMessage(content="Prior answer"),
            HumanMessage(content="   "),  # empty content is dropped
        ]
    )

    sent = pipeline.requests[0]
    assert [(m.role, m.content) for m in sent.messages] == [
        ("system", "You are a RESUME PARSER."),
        ("user", "Resume text"),
        ("assistant", "Prior answer"),
    ]


def test_routing_enabled_leaves_the_model_unset():
    pipeline = _FakePipeline()
    provider = _provider(pipeline)

    provider.invoke([SystemMessage(content="Extract education.")])

    # An unset model is what hands the choice to taut's routing layer.
    assert pipeline.requests[0].model is None


def test_declared_tier_pins_that_tier_s_model():
    """A call site that names a tier must get that tier, not a classified guess.

    taut's classifier scores on length and keyword hits, and its length factor
    saturates at exactly the "simple" threshold -- so the revision and fit calls
    landed on the cheap tier by accident while the draft did not.
    """
    pipeline = _FakePipeline()
    provider = _provider(pipeline)

    provider.invoke([SystemMessage(content="Rewrite this resume.")], tier="complex")
    provider.invoke([SystemMessage(content="Extract education.")], tier="simple")

    assert pipeline.requests[0].model == TIERS["complex"][0]
    assert pipeline.requests[1].model == TIERS["simple"][0]


def test_unknown_tier_falls_back_to_the_classifier():
    pipeline = _FakePipeline()
    provider = _provider(pipeline)

    provider.invoke([SystemMessage(content="Extract education.")], tier="nonexistent")

    assert pipeline.requests[0].model is None


def test_routing_disabled_pins_the_default_model():
    pipeline = _FakePipeline()
    provider = _provider(pipeline, routing_enabled=False)

    provider.invoke([SystemMessage(content="Extract education.")])

    assert pipeline.requests[0].model == "anthropic/claude-sonnet-4-5-20250929"


def test_no_tiers_configured_pins_the_default_model():
    pipeline = _FakePipeline()
    provider = _provider(pipeline, tiers=None)

    provider.invoke([SystemMessage(content="Extract education.")])

    assert pipeline.requests[0].model == "anthropic/claude-sonnet-4-5-20250929"


def test_empty_message_list_raises():
    pipeline = _FakePipeline()
    provider = _provider(pipeline)

    with pytest.raises(LLMError):
        provider.invoke([SystemMessage(content="   ")])


def test_pipeline_failure_is_wrapped_as_llm_error():
    class _Boom:
        def run_sync(self, _request):
            raise RuntimeError("upstream exploded")

    provider = _provider(_Boom())

    with pytest.raises(LLMError) as exc:
        provider.invoke([SystemMessage(content="Extract education.")])
    assert "upstream exploded" in str(exc.value)


def test_usage_is_recorded_on_the_provider():
    pipeline = _FakePipeline()
    provider = _provider(pipeline)

    provider.invoke([SystemMessage(content="Extract education.")])

    assert provider.last_usage == {
        "routed_model": "anthropic/claude-haiku-4-5-20251001",
        "input_tokens": 120,
        "output_tokens": 40,
        "cached_tokens": 0,
        "cost_usd": 0.00031,
    }


def test_usage_reaches_llm_service_metadata():
    pipeline = _FakePipeline()
    provider = _provider(pipeline)

    with (
        patch("resume_agent.services.llm_service.create_provider", return_value=provider),
        patch("resume_agent.services.llm_service.get_cache_store", return_value=_DummyCacheStore()),
    ):
        service = LLMService(provider_type="taut", api_key="test-key")
        service.invoke_with_retry([SystemMessage(content="Extract education.")], use_cache=False, max_retries=1)

    assert service.last_invoke_metadata["routed_model"] == "anthropic/claude-haiku-4-5-20251001"
    assert service.last_invoke_metadata["input_tokens"] == 120
    assert service.last_invoke_metadata["cost_usd"] == 0.00031
    # The configured model stays distinguishable from the one routing picked.
    assert service.last_invoke_metadata["model"] == "anthropic/claude-sonnet-4-5-20250929"


def test_factory_builds_a_taut_provider():
    pipeline = _FakePipeline()
    with patch("taut.create_pipeline", return_value=pipeline):
        provider = create_provider(
            "taut",
            api_key="test-key",
            default_model="anthropic/claude-sonnet-4-5-20250929",
            tiers=TIERS,
        )
    assert isinstance(provider, TautProvider)
    assert provider.get_model_name() == "anthropic/claude-sonnet-4-5-20250929"
