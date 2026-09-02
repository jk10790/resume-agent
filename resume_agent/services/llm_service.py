"""
Centralized LLM service with retry logic, caching, and structured output.
Supports multiple providers: Ollama, Groq, OpenAI, Anthropic
"""

import json
import time
import hashlib
from typing import Optional, Dict, Any, List
from langchain_core.messages import BaseMessage, SystemMessage

from ..utils.logger import logger
from ..utils.exceptions import LLMError
from .llm_providers import create_provider, LLMProvider
from ..storage.cache_store import get_cache_store


class LLMService:
    """Centralized LLM service with retry, caching, and structured output"""
    
    def __init__(
        self,
        provider_type: Optional[str] = None,
        model_name: Optional[str] = None,
        cache_size: Optional[int] = None,
        **provider_kwargs
    ):
        """
        Initialize LLM service with configurable provider.
        
        Args:
            provider_type: One of "ollama", "groq", "openai", or "anthropic". If None, uses settings.
            model_name: Model name (provider-specific). If None, uses settings defaults.
            cache_size: Cache size for responses
            **provider_kwargs: Additional provider-specific arguments
        """
        from ..config import settings
        
        # Use settings if not provided
        if provider_type is None:
            provider_type = settings.llm_provider
        
        self.provider_type = provider_type.lower().strip()
        
        # Use settings for cache size if not provided
        if cache_size is None:
            cache_size = settings.llm_cache_size
        
        # Create provider with appropriate configuration
        if self.provider_type == "ollama":
            model = model_name or settings.ollama_model
            self.provider = create_provider("ollama", model_name=model)
            self.model_name = model
            
        elif self.provider_type == "groq":
            api_key = provider_kwargs.get("api_key") or settings.groq_api_key
            model = model_name or settings.groq_model
            self.provider = create_provider(
                "groq",
                api_key=api_key,
                model_name=model,
                temperature=provider_kwargs.get("temperature", settings.groq_temperature),
                top_p=provider_kwargs.get("top_p", settings.groq_top_p),
                max_tokens=provider_kwargs.get("max_tokens", settings.groq_max_tokens)
            )
            self.model_name = model
            
        elif self.provider_type == "openai":
            api_key = provider_kwargs.get("api_key") or settings.openai_api_key
            model = model_name or settings.openai_model
            self.provider = create_provider(
                "openai",
                api_key=api_key,
                model_name=model,
                temperature=provider_kwargs.get("temperature", settings.openai_temperature),
                top_p=provider_kwargs.get("top_p", settings.openai_top_p),
                max_tokens=provider_kwargs.get("max_tokens", settings.openai_max_tokens)
            )
            self.model_name = model

        elif self.provider_type == "anthropic":
            api_key = provider_kwargs.get("api_key") or settings.anthropic_api_key
            model = model_name or settings.anthropic_model
            self.provider = create_provider(
                "anthropic",
                api_key=api_key,
                model_name=model,
                temperature=provider_kwargs.get("temperature", settings.anthropic_temperature),
                max_tokens=provider_kwargs.get("max_tokens", settings.anthropic_max_tokens),
            )
            self.model_name = model

        elif self.provider_type == "taut":
            # taut fronts the real provider, so the upstream key still comes from
            # that provider's setting; the model ids are litellm-prefixed.
            api_key = (
                provider_kwargs.get("api_key")
                or settings.anthropic_api_key
                or settings.openai_api_key
                or settings.groq_api_key
            )
            model = model_name or settings.taut_default_model
            self.provider = create_provider(
                "taut",
                api_key=api_key,
                default_model=model,
                routing_enabled=settings.taut_routing_enabled,
                tiers={
                    "simple": [settings.taut_tier_simple],
                    "standard": [settings.taut_tier_standard],
                    "complex": [settings.taut_tier_complex],
                },
                temperature=provider_kwargs.get("temperature", settings.anthropic_temperature),
                max_tokens=provider_kwargs.get("max_tokens", settings.anthropic_max_tokens),
                timeout=settings.taut_timeout,
            )
            self.model_name = model

        else:
            from ..utils.exceptions import ConfigError
            raise ConfigError(
                f"Unknown provider: {provider_type}",
                config_key="LLM_PROVIDER",
                fix_instructions=(
                    f"1. Set LLM_PROVIDER to one of: ollama, groq, openai, anthropic, taut\n"
                    f"2. Current value: {provider_type}\n"
                    f"3. Update your .env file with: LLM_PROVIDER=anthropic (or groq/ollama/openai)"
                )
            )
        
        # Providers are duck-typed in tests and by any custom implementation, so
        # check once rather than assuming the tier-aware signature.
        try:
            import inspect
            self._provider_accepts_tier = "tier" in inspect.signature(self.provider.invoke).parameters
        except (TypeError, ValueError):
            self._provider_accepts_tier = False

        self.cache: Dict[str, str] = {}
        self.cache_size = cache_size
        self.cache_store = get_cache_store()
        self.last_invoke_metadata: Dict[str, Any] = {}
        # Running totals for this service instance. The taut provider is the only
        # one that reports usage today; the rest leave these at zero rather than
        # guessing a token count.
        self.usage_totals: Dict[str, Any] = {
            "calls": 0,
            "cache_hits": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_tokens": 0,
            "cost_usd": 0.0,
            "by_model": {},
        }
        logger.info(f"Initialized LLM service with provider: {self.provider_type}, model: {self.model_name}")
    
    def _get_cache_key(self, messages: List[BaseMessage], tier: Optional[str] = None) -> str:
        """Generate cache key from messages, resolved model, and sampling params.

        `self.model_name` is not enough on its own: under a routing provider it is
        the configured default while the call may be served by any tier, so two
        different models would otherwise share one key.
        """
        content = "|".join([str(msg.content) for msg in messages])
        resolve = getattr(self.provider, "resolve_tier_model", None)
        resolved_model = (resolve(tier) if callable(resolve) else None) or self.model_name
        sampling = (
            getattr(self.provider, "temperature", None),
            getattr(self.provider, "max_tokens", None),
        )
        return hashlib.md5(f"{content}:{resolved_model}:{sampling}".encode()).hexdigest()
    
    def _get_from_cache(self, key: str) -> Optional[str]:
        """Get response from cache"""
        if key in self.cache:
            logger.debug("Cache hit", cache_key=key[:8])
            self.last_invoke_metadata = {
                "cache_hit": True,
                "cache_layer": "memory",
                "provider": self.provider_type,
                "model": self.model_name,
            }
            return self.cache[key]
        persistent = self.cache_store.get("llm_response", key)
        if persistent and isinstance(persistent.get("response"), str):
            logger.debug("Persistent cache hit", cache_key=key[:8])
            self._set_cache(key, persistent["response"], persist=False)
            self.last_invoke_metadata = {
                "cache_hit": True,
                "cache_layer": "persistent",
                "provider": self.provider_type,
                "model": self.model_name,
            }
            return persistent["response"]
        return None
    
    def _cache_expiry(self) -> Optional[str]:
        """Expiry stamp for persisted responses, or None to keep them forever."""
        from ..config import settings
        ttl_hours = getattr(settings, "llm_cache_ttl_hours", 0)
        if not ttl_hours or ttl_hours <= 0:
            return None
        from datetime import datetime, timedelta, timezone
        return (datetime.now(timezone.utc) + timedelta(hours=ttl_hours)).isoformat()

    def _set_cache(self, key: str, value: str, *, persist: bool = True):
        """Store response in cache"""
        if len(self.cache) >= self.cache_size:
            # Remove oldest entry (simple FIFO)
            oldest_key = next(iter(self.cache))
            del self.cache[oldest_key]
        self.cache[key] = value
        if persist:
            self.cache_store.put(
                "llm_response",
                key,
                {"response": value},
                source_hash=key,
                schema_version="llm_response_v1",
                provider=self.provider_type,
                model=self.model_name,
                # Without this these rows never expired. A prompt whose wording
                # has since changed would otherwise be answered from a response
                # written against the old one, indefinitely.
                expires_at=self._cache_expiry(),
            )
        logger.debug("Cached response", cache_key=key[:8])
    
    def usage_snapshot(self) -> Dict[str, Any]:
        """Copy of the running totals, safe to diff against a later snapshot."""
        snapshot = dict(self.usage_totals)
        snapshot["by_model"] = dict(self.usage_totals.get("by_model", {}))
        return snapshot

    def _record_usage(self, metadata: Dict[str, Any]) -> None:
        """Fold one call's reported usage into this service's running totals.

        The taut provider reports per-call tokens and cost -- the reason it was
        adopted -- but nothing outside the quality agent was reading it, so a
        tailoring run had no cost attached to it at all.
        """
        totals = self.usage_totals
        totals["calls"] += 1
        if metadata.get("cache_hit"):
            totals["cache_hits"] += 1
            return
        for field in ("input_tokens", "output_tokens", "cached_tokens"):
            value = metadata.get(field)
            if isinstance(value, (int, float)):
                totals[field] += value
        cost = metadata.get("cost_usd")
        if isinstance(cost, (int, float)):
            totals["cost_usd"] += cost
        model = metadata.get("routed_model") or metadata.get("model")
        if model:
            totals["by_model"][model] = totals["by_model"].get(model, 0) + 1

    def invoke_with_retry(
        self,
        messages: List[BaseMessage],
        max_retries: Optional[int] = None,
        retry_delay: Optional[float] = None,
        use_cache: bool = True,
        tier: Optional[str] = None
    ) -> str:
        """
        Invoke LLM with automatic retry on failure.
        
        Args:
            messages: List of messages to send to LLM
            max_retries: Maximum number of retry attempts (uses settings if None)
            retry_delay: Delay between retries in seconds (uses settings if None)
            use_cache: Whether to use caching
            tier: Cost/capability band for this call ("simple", "standard",
                "complex"). Providers pinned to one model ignore it.
        
        Returns:
            LLM response text
        
        Raises:
            LLMError: If all retries fail
        """
        from ..config import settings
        
        # Use settings if not provided
        if max_retries is None:
            max_retries = settings.llm_max_retries
        if retry_delay is None:
            retry_delay = settings.llm_retry_delay
        
        # Check cache
        if use_cache:
            cache_key = self._get_cache_key(messages, tier)
            cached = self._get_from_cache(cache_key)
            if cached:
                self._record_usage(self.last_invoke_metadata)
                return cached
        else:
            cache_key = None

        self.last_invoke_metadata = {
            "cache_hit": False,
            "cache_layer": None,
            "provider": self.provider_type,
            "model": self.model_name,
            "attempts": 0,
        }
        
        last_error = None
        for attempt in range(max_retries):
            try:
                logger.info(f"LLM API call - attempt {attempt + 1}/{max_retries}", provider=self.provider_type, model=self.provider.get_model_name())
                if self._provider_accepts_tier:
                    response = self.provider.invoke(messages, tier=tier)
                else:
                    response = self.provider.invoke(messages)
                result = response.strip() if hasattr(response, 'strip') else str(response).strip()
                self.last_invoke_metadata = {
                    "cache_hit": False,
                    "cache_layer": None,
                    "provider": self.provider_type,
                    "model": self.model_name,
                    "attempts": attempt + 1,
                    "response_length": len(result),
                    "cache_key": cache_key[:8] if cache_key else None,
                    # Only providers that report usage populate this; the rest
                    # leave it empty rather than guessing a token count.
                    **(getattr(self.provider, "last_usage", None) or {}),
                }
                
                logger.info("LLM API call successful", provider=self.provider_type, response_length=len(result))
                self._record_usage(self.last_invoke_metadata)
                
                # Cache successful response
                if use_cache:
                    self._set_cache(cache_key, result)
                
                return result
                
            except Exception as e:
                last_error = e
                self.last_invoke_metadata = {
                    "cache_hit": False,
                    "cache_layer": None,
                    "provider": self.provider_type,
                    "model": self.model_name,
                    "attempts": attempt + 1,
                    "error": str(e),
                    "cache_key": cache_key[:8] if cache_key else None,
                }
                logger.warning(
                    f"LLM invoke failed (attempt {attempt + 1}/{max_retries})",
                    error=e,
                    attempt=attempt + 1,
                    provider=self.provider_type
                )
                if attempt < max_retries - 1:
                    # True exponential backoff: base_delay * (2 ^ attempt)
                    # Add jitter to avoid thundering herd
                    import random
                    exponential_delay = retry_delay * (2 ** attempt)
                    jitter = exponential_delay * random.uniform(0, 0.25)  # 0-25% jitter
                    delay = min(exponential_delay + jitter, 60.0)  # Cap at 60 seconds
                    logger.debug(f"Retrying in {delay:.2f}s (exponential backoff)", delay=delay)
                    time.sleep(delay)
        
        raise LLMError(
            f"LLM invocation failed after {max_retries} attempts: {last_error}",
            provider=self.provider_type
        )
    
    def run_task(self, task_id: str, **variables: Any):
        """Run a registered task by id.

        The task file declares its own tier, cache policy and output format, so
        no call site names a model or repeats those decisions. Returns a parsed
        dict for tasks declaring `output: json`, otherwise the response text.
        """
        from ..llm.tasks import get_task

        task = get_task(task_id)
        messages = task.render(**variables)
        if task.expects_json:
            return self.invoke_structured(messages, tier=task.tier, use_cache=task.cache)
        return self.invoke_with_retry(messages, use_cache=task.cache, tier=task.tier)

    def invoke_structured(
        self,
        messages: List[BaseMessage],
        output_schema: Optional[Dict[str, Any]] = None,
        max_retries: int = 3,
        validation_retries: int = 2,
        tier: Optional[str] = None,
        use_cache: bool = True
    ) -> Dict[str, Any]:
        """
        Invoke LLM and parse structured JSON output with retry on validation failures.
        
        Args:
            messages: List of messages to send to LLM
            output_schema: Optional JSON schema for validation
            max_retries: Maximum number of retry attempts for LLM calls
            validation_retries: Maximum number of retries for validation failures
        
        Returns:
            Parsed JSON response as dictionary
        """
        from ..config import settings
        import random
        
        # Add JSON format instruction if schema provided
        if output_schema:
            system_msg = messages[0] if messages and isinstance(messages[0], SystemMessage) else None
            if system_msg:
                json_instruction = "\n\nIMPORTANT: Respond with valid JSON only. No markdown, no explanations, just JSON."
                system_msg.content += json_instruction
        
        # Retry on both LLM failures and validation failures
        last_error = None
        for validation_attempt in range(validation_retries + 1):
            try:
                # Get LLM response (with its own retry logic)
                response_text = self.invoke_with_retry(
                    messages, max_retries, tier=tier, use_cache=use_cache
                )
                
                # Try to extract JSON from response
                try:
                    # Try parsing as-is
                    parsed = json.loads(response_text)
                    return parsed
                except json.JSONDecodeError:
                    # Try extracting JSON from markdown code blocks
                    import re
                    json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response_text, re.DOTALL)
                    if json_match:
                        parsed = json.loads(json_match.group(1))
                        return parsed
                    
                    # Try finding JSON object in text
                    json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
                    if json_match:
                        parsed = json.loads(json_match.group(0))
                        return parsed
                    
                    # If we're on the last validation attempt, raise error
                    if validation_attempt >= validation_retries:
                        raise LLMError(
                            f"Could not parse JSON from LLM response after {validation_retries + 1} attempts: {response_text[:200]}",
                            provider=self.provider_type,
                            fix_instructions=(
                                "1. The LLM response was not valid JSON.\n"
                                "2. This may indicate the model is not following instructions.\n"
                                "3. Try using a different model or provider.\n"
                                "4. Check the prompt template for JSON format requirements."
                            )
                        )
                    
                    # Retry with exponential backoff
                    delay = settings.llm_retry_delay * (2 ** validation_attempt)
                    jitter = delay * random.uniform(0, 0.25)
                    delay = min(delay + jitter, 10.0)  # Cap at 10 seconds for validation retries
                    logger.warning(
                        f"JSON parsing failed (validation attempt {validation_attempt + 1}/{validation_retries + 1}), retrying in {delay:.2f}s",
                        attempt=validation_attempt + 1,
                        delay=delay
                    )
                    time.sleep(delay)
                    continue
                    
            except Exception as e:
                last_error = e
                if validation_attempt >= validation_retries:
                    raise
                
                # Retry with exponential backoff
                delay = settings.llm_retry_delay * (2 ** validation_attempt)
                jitter = delay * random.uniform(0, 0.25)
                delay = min(delay + jitter, 10.0)
                logger.warning(
                    f"Structured invocation failed (validation attempt {validation_attempt + 1}/{validation_retries + 1}), retrying in {delay:.2f}s",
                    error=str(e),
                    attempt=validation_attempt + 1,
                    delay=delay
                )
                time.sleep(delay)
        
        # Should not reach here, but handle just in case
        raise last_error or LLMError(
            f"Structured invocation failed after {validation_retries + 1} validation attempts",
            provider=self.provider_type
        )
    

_default_llm_service: Optional[LLMService] = None


def get_llm_service() -> LLMService:
    """Shared LLMService configured from settings.

    Constructing one is not free -- the routing provider builds a whole pipeline
    in its __init__ -- and the per-instance response cache starts empty, so a
    service built per request threw away both. Call sites needing a specific
    provider or model still construct their own.
    """
    global _default_llm_service
    if _default_llm_service is None:
        _default_llm_service = LLMService()
    return _default_llm_service


def reset_llm_service() -> None:
    """Drop the shared instance (tests, or a settings change at runtime)."""
    global _default_llm_service
    _default_llm_service = None
