"""
Centralized LLM service with retry logic, caching, and structured output.
Supports multiple providers: Ollama, Groq, OpenAI
"""

import json
import time
import hashlib
from typing import Optional, Dict, Any, List
from langchain_core.messages import SystemMessage, HumanMessage, BaseMessage

from ..utils.logger import logger
from ..utils.exceptions import LLMError
from ..models.resume import FitEvaluation
from .llm_providers import create_provider, LLMProvider


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
            provider_type: One of "ollama", "groq", or "openai". If None, uses settings.
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
        else:
            from ..utils.exceptions import ConfigError
            raise ConfigError(
                f"Unknown provider: {provider_type}",
                config_key="LLM_PROVIDER",
                fix_instructions=(
                    f"1. Set LLM_PROVIDER to one of: ollama, groq, openai\n"
                    f"2. Current value: {provider_type}\n"
                    f"3. Update your .env file with: LLM_PROVIDER=groq (or ollama/openai)"
                )
            )
        
        self.cache: Dict[str, str] = {}
        self.cache_size = cache_size
        logger.info(f"Initialized LLM service with provider: {self.provider_type}, model: {self.model_name}")
    
    def _get_cache_key(self, messages: List[BaseMessage]) -> str:
        """Generate cache key from messages"""
        content = "|".join([str(msg.content) for msg in messages])
        return hashlib.md5(f"{content}:{self.model_name}".encode()).hexdigest()
    
    def _get_from_cache(self, key: str) -> Optional[str]:
        """Get response from cache"""
        if key in self.cache:
            logger.debug("Cache hit", cache_key=key[:8])
            return self.cache[key]
        return None
    
    def _set_cache(self, key: str, value: str):
        """Store response in cache"""
        if len(self.cache) >= self.cache_size:
            # Remove oldest entry (simple FIFO)
            oldest_key = next(iter(self.cache))
            del self.cache[oldest_key]
        self.cache[key] = value
        logger.debug("Cached response", cache_key=key[:8])
    
    def invoke_with_retry(
        self,
        messages: List[BaseMessage],
        max_retries: Optional[int] = None,
        retry_delay: Optional[float] = None,
        use_cache: bool = True
    ) -> str:
        """
        Invoke LLM with automatic retry on failure.
        
        Args:
            messages: List of messages to send to LLM
            max_retries: Maximum number of retry attempts (uses settings if None)
            retry_delay: Delay between retries in seconds (uses settings if None)
            use_cache: Whether to use caching
        
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
            cache_key = self._get_cache_key(messages)
            cached = self._get_from_cache(cache_key)
            if cached:
                return cached
        
        last_error = None
        for attempt in range(max_retries):
            try:
                logger.info(f"LLM API call - attempt {attempt + 1}/{max_retries}", provider=self.provider_type, model=self.provider.get_model_name())
                response = self.provider.invoke(messages)
                result = response.strip() if hasattr(response, 'strip') else str(response).strip()
                
                logger.info("LLM API call successful", provider=self.provider_type, response_length=len(result))
                
                # Cache successful response
                if use_cache:
                    self._set_cache(cache_key, result)
                
                return result
                
            except Exception as e:
                last_error = e
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
    
    def invoke_structured(
        self,
        messages: List[BaseMessage],
        output_schema: Optional[Dict[str, Any]] = None,
        max_retries: int = 3,
        validation_retries: int = 2
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
                response_text = self.invoke_with_retry(messages, max_retries)
                
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
    
    def evaluate_fit_structured(
        self,
        resume_text: str,
        jd_text: str,
        known_skills: List[str],
        prompt_version: str = "latest"
    ) -> FitEvaluation:
        """
        Evaluate resume fit with structured output.
        
        Args:
            resume_text: Resume content
            jd_text: Job description content
            known_skills: List of user's confirmed skills
            prompt_version: Prompt version to use
        
        Returns:
            FitEvaluation model
        """
        from ..prompts.templates import get_prompt
        
        skills_str = ", ".join(known_skills) if known_skills else "None"
        
        # Use versioned prompt template
        try:
            prompt_template = get_prompt("fit_evaluation", prompt_version)
            messages = prompt_template.format_messages(
                job_description=jd_text,
                resume=resume_text,
                known_skills=skills_str
            )
        except Exception as e:
            logger.warning("Failed to load prompt template, using fallback", error=e)
            # Fallback to direct messages
            from langchain_core.messages import SystemMessage, HumanMessage
            messages = [
                SystemMessage(content="""You are a strict job fit evaluator. Respond with JSON:
{
    "score": <1-10>,
    "should_apply": <true/false>,
    "matching_areas": [],
    "missing_areas": [],
    "recommendations": [],
    "confidence": <0.0-1.0>,
    "reasoning": ""
}"""),
                HumanMessage(content=f"Job: {jd_text}\nResume: {resume_text}\nSkills: {skills_str}")
            ]
        
        try:
            result = self.invoke_structured(messages, max_retries=3)
            return FitEvaluation(**result)
        except Exception as e:
            logger.error("Failed to get structured fit evaluation", error=e)
            # Fallback to text parsing
            text_result = self.invoke_with_retry(messages, max_retries=1, use_cache=False)
            return self._parse_fit_evaluation_text(text_result)
    
    def _parse_fit_evaluation_text(self, text: str) -> FitEvaluation:
        """Fallback: Parse free-text evaluation"""
        import re
        
        # Extract score
        score_match = re.search(r'[Ff]it\s+[Ss]core[:\s]+(\d+)', text)
        score = int(score_match.group(1)) if score_match else 5
        
        # Extract should_apply
        should_apply = "yes" in text.lower() or "should apply" in text.lower()
        
        # Extract matching areas (look for sections)
        matching = []
        if "matching" in text.lower() or "top matching" in text.lower():
            # Try to extract list items
            lines = text.split('\n')
            in_matching = False
            for line in lines:
                if "matching" in line.lower():
                    in_matching = True
                    continue
                if in_matching and (line.strip().startswith('-') or line.strip().startswith('•')):
                    matching.append(line.strip().lstrip('-•').strip())
                elif in_matching and line.strip() == "":
                    break
        
        # Similar for missing areas
        missing = []
        if "missing" in text.lower():
            lines = text.split('\n')
            in_missing = False
            for line in lines:
                if "missing" in line.lower():
                    in_missing = True
                    continue
                if in_missing and (line.strip().startswith('-') or line.strip().startswith('•')):
                    missing.append(line.strip().lstrip('-•').strip())
                elif in_missing and line.strip() == "":
                    break
        
        return FitEvaluation(
            score=score,
            should_apply=should_apply,
            matching_areas=matching[:5],  # Limit to 5
            missing_areas=missing[:5],
            recommendations=[],
            confidence=0.6  # Lower confidence for parsed results
        )
