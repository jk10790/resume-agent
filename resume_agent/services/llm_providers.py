"""
LLM provider implementations for different services (Ollama, Groq, OpenAI).
"""

import json
import time
import requests
from typing import Optional, List, Dict, Any
from abc import ABC, abstractmethod
from langchain_core.messages import BaseMessage

from ..utils.logger import logger
from ..utils.exceptions import LLMError


class LLMProvider(ABC):
    """Abstract base class for LLM providers"""
    
    @abstractmethod
    def invoke(self, messages: List[BaseMessage]) -> str:
        """Invoke the LLM with messages and return response"""
        pass
    
    @abstractmethod
    def get_model_name(self) -> str:
        """Get the model name being used"""
        pass


class OllamaProvider(LLMProvider):
    """Ollama local LLM provider"""
    
    def __init__(self, model_name: str):
        try:
            from langchain_ollama import OllamaLLM
            self.model_name = model_name
            self.model = OllamaLLM(model=model_name)
            logger.info(f"Initialized Ollama provider with model: {model_name}")
        except ImportError:
            raise LLMError(
                "langchain-ollama not installed",
                provider="ollama",
                fix_instructions="Install with: pip install langchain-ollama"
            )
    
    def invoke(self, messages: List[BaseMessage]) -> str:
        """Invoke Ollama LLM"""
        try:
            response = self.model.invoke(messages)
            return response.strip() if hasattr(response, 'strip') else str(response).strip()
        except Exception as e:
            raise LLMError(
                f"Ollama invocation failed: {e}",
                provider="ollama"
            )
    
    def get_model_name(self) -> str:
        return self.model_name


class GroqProvider(LLMProvider):
    """Groq API provider"""
    
    def __init__(self, api_key: str, model_name: str = "llama-3.3-70b-versatile", 
                 temperature: float = 0.3, top_p: float = 0.9, max_tokens: int = 4000):
        if not api_key:
            raise LLMError(
                "GROQ_API_KEY is required for Groq provider",
                provider="groq",
                fix_instructions=(
                    "1. Get your Groq API key from: https://console.groq.com\n"
                    "2. Add to .env file: GROQ_API_KEY=your_key_here\n"
                    "3. Set provider: LLM_PROVIDER=groq"
                )
            )
        
        self.api_key = api_key
        self.model_name = model_name
        self.temperature = temperature
        self.top_p = top_p
        self.max_tokens = max_tokens
        self.base_url = "https://api.groq.com/openai/v1/chat/completions"
        logger.info(f"Initialized Groq provider with model: {model_name}")
    
    def _messages_to_dict(self, messages: List[BaseMessage]) -> List[Dict[str, str]]:
        """Convert LangChain messages to Groq API format"""
        result = []
        for msg in messages:
            role = "system" if msg.__class__.__name__ == "SystemMessage" else "user"
            content = str(msg.content)
            result.append({"role": role, "content": content})
        return result
    
    def invoke(self, messages: List[BaseMessage]) -> str:
        """Invoke Groq API"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": self.model_name,
            "messages": self._messages_to_dict(messages),
            "temperature": self.temperature,
            "top_p": self.top_p,
            "max_tokens": self.max_tokens,
            "stream": False
        }
        
        # Retry logic for transient errors
        from ..config import settings
        from ..utils.logger import logger
        timeout = settings.google_api_timeout  # Reuse timeout setting
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                logger.info("Calling Groq API", model=self.model_name, attempt=attempt+1, max_retries=max_retries)
                response = requests.post(
                    self.base_url,
                    headers=headers,
                    json=payload,
                    timeout=timeout
                )
                
                if response.ok:
                    data = response.json()
                    content = data["choices"][0]["message"]["content"]
                    logger.info("Groq API call successful", response_length=len(content) if content else 0)
                    return content.strip() if content else ""
                
                # Handle rate limiting and server errors
                if response.status_code in (429, 500, 502, 503, 504) and attempt < max_retries - 1:
                    wait_time = 1.5 * (attempt + 1)
                    logger.warning(f"Groq API error {response.status_code}, retrying in {wait_time}s")
                    time.sleep(wait_time)
                    continue
                
                error_msg = response.text[:300] if response.text else "Unknown error"
                raise LLMError(
                    f"Groq API error {response.status_code}: {error_msg}",
                    provider="groq"
                )
                
            except requests.exceptions.RequestException as e:
                if attempt < max_retries - 1:
                    wait_time = 1.5 * (attempt + 1)
                    logger.warning(f"Groq request failed, retrying in {wait_time}s: {e}")
                    time.sleep(wait_time)
                    continue
                raise LLMError(
                    f"Groq API request failed: {e}",
                    provider="groq"
                )
        
        raise LLMError(
            f"Groq API failed after {max_retries} attempts",
            provider="groq"
        )
    
    def get_model_name(self) -> str:
        return self.model_name


class OpenAIProvider(LLMProvider):
    """OpenAI API provider"""
    
    def __init__(self, api_key: str, model_name: str = "gpt-4o-mini",
                 temperature: float = 0.3, top_p: float = 0.9, max_tokens: int = 4000):
        if not api_key:
            raise LLMError(
                "OPENAI_API_KEY is required for OpenAI provider",
                provider="openai",
                fix_instructions=(
                    "1. Get your OpenAI API key from: https://platform.openai.com/api-keys\n"
                    "2. Add to .env file: OPENAI_API_KEY=your_key_here\n"
                    "3. Set provider: LLM_PROVIDER=openai"
                )
            )
        
        self.api_key = api_key
        self.model_name = model_name
        self.temperature = temperature
        self.top_p = top_p
        self.max_tokens = max_tokens
        self.base_url = "https://api.openai.com/v1/chat/completions"
        logger.info(f"Initialized OpenAI provider with model: {model_name}")
    
    def _messages_to_dict(self, messages: List[BaseMessage]) -> List[Dict[str, str]]:
        """Convert LangChain messages to OpenAI API format"""
        result = []
        for msg in messages:
            role = "system" if msg.__class__.__name__ == "SystemMessage" else "user"
            content = str(msg.content)
            result.append({"role": role, "content": content})
        return result
    
    def invoke(self, messages: List[BaseMessage]) -> str:
        """Invoke OpenAI API"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": self.model_name,
            "messages": self._messages_to_dict(messages),
            "temperature": self.temperature,
            "top_p": self.top_p,
            "max_tokens": self.max_tokens
        }
        
        # Retry logic
        from ..config import settings
        timeout = settings.google_api_timeout  # Reuse timeout setting
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = requests.post(
                    self.base_url,
                    headers=headers,
                    json=payload,
                    timeout=timeout
                )
                
                if response.ok:
                    data = response.json()
                    content = data["choices"][0]["message"]["content"]
                    return content.strip() if content else ""
                
                if response.status_code in (429, 500, 502, 503, 504) and attempt < max_retries - 1:
                    wait_time = 1.5 * (attempt + 1)
                    logger.warning(f"OpenAI API error {response.status_code}, retrying in {wait_time}s")
                    time.sleep(wait_time)
                    continue
                
                error_msg = response.text[:300] if response.text else "Unknown error"
                raise LLMError(
                    f"OpenAI API error {response.status_code}: {error_msg}",
                    provider="openai"
                )
                
            except requests.exceptions.RequestException as e:
                if attempt < max_retries - 1:
                    wait_time = 1.5 * (attempt + 1)
                    logger.warning(f"OpenAI request failed, retrying in {wait_time}s: {e}")
                    time.sleep(wait_time)
                    continue
                raise LLMError(
                    f"OpenAI API request failed: {e}",
                    provider="openai"
                )
        
        raise LLMError(
            f"OpenAI API failed after {max_retries} attempts",
            provider="openai"
        )
    
    def get_model_name(self) -> str:
        return self.model_name


def create_provider(provider_type: str, **kwargs) -> LLMProvider:
    """
    Factory function to create LLM provider instances.
    
    Args:
        provider_type: One of "ollama", "groq", or "openai"
        **kwargs: Provider-specific configuration
    
    Returns:
        LLMProvider instance
    """
    provider_type = provider_type.lower().strip()
    
    if provider_type == "ollama":
        model_name = kwargs.get("model_name", "llama2")
        return OllamaProvider(model_name=model_name)
    
    elif provider_type == "groq":
        api_key = kwargs.get("api_key")
        model_name = kwargs.get("model_name", "llama-3.3-70b-versatile")
        temperature = kwargs.get("temperature", 0.3)
        top_p = kwargs.get("top_p", 0.9)
        max_tokens = kwargs.get("max_tokens", 4000)
        return GroqProvider(
            api_key=api_key,
            model_name=model_name,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens
        )
    
    elif provider_type == "openai":
        api_key = kwargs.get("api_key")
        model_name = kwargs.get("model_name", "gpt-4o-mini")
        temperature = kwargs.get("temperature", 0.3)
        top_p = kwargs.get("top_p", 0.9)
        max_tokens = kwargs.get("max_tokens", 4000)
        return OpenAIProvider(
            api_key=api_key,
            model_name=model_name,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens
        )
    
    else:
        from ..utils.exceptions import ConfigError
        raise ConfigError(
            f"Unknown LLM provider: {provider_type}",
            config_key="LLM_PROVIDER",
            fix_instructions=(
                f"1. Set LLM_PROVIDER to one of: ollama, groq, openai\n"
                f"2. Current value: {provider_type}\n"
                f"3. Update your .env file with: LLM_PROVIDER=groq (or ollama/openai)"
            )
        )
