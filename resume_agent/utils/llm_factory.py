"""
Helpers for creating optional LLMService instances with overrides.
"""

from typing import Optional
from ..services.llm_service import LLMService
from ..utils.logger import logger


def create_llm_service_with_fallback(
    fallback: LLMService,
    provider: Optional[str],
    model: Optional[str],
    temperature: Optional[float],
    top_p: Optional[float],
    max_tokens: Optional[int],
    tag: str
) -> LLMService:
    """Create an LLMService with overrides or fall back safely."""
    if not any([provider, model, temperature is not None, top_p is not None, max_tokens is not None]):
        return fallback

    provider_type = provider or fallback.provider_type
    provider_kwargs = {}
    if temperature is not None:
        provider_kwargs["temperature"] = temperature
    if top_p is not None:
        provider_kwargs["top_p"] = top_p
    if max_tokens is not None:
        provider_kwargs["max_tokens"] = max_tokens

    try:
        return LLMService(
            provider_type=provider_type,
            model_name=model,
            **provider_kwargs
        )
    except Exception as e:
        logger.warning(
            "Failed to initialize override LLM; falling back",
            tag=tag,
            error=str(e)
        )
        return fallback
