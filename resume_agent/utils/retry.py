"""
Retry utilities with exponential backoff for agent operations.
Provides configurable retry strategies for different types of failures.
"""

import time
import random
from typing import Callable, TypeVar, Optional, List, Type
from functools import wraps
from ..utils.logger import logger

T = TypeVar('T')


class RetryConfig:
    """Configuration for retry behavior"""
    
    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        exponential_base: float = 2.0,
        jitter: bool = True,
        retryable_exceptions: Optional[List[Type[Exception]]] = None
    ):
        """
        Initialize retry configuration.
        
        Args:
            max_retries: Maximum number of retry attempts
            base_delay: Base delay in seconds for exponential backoff
            max_delay: Maximum delay in seconds (caps exponential growth)
            exponential_base: Base for exponential backoff (2.0 = doubles each time)
            jitter: Whether to add random jitter to avoid thundering herd
            retryable_exceptions: List of exception types that should trigger retry
        """
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.jitter = jitter
        self.retryable_exceptions = retryable_exceptions or [Exception]
    
    def calculate_delay(self, attempt: int) -> float:
        """
        Calculate delay for a given attempt using exponential backoff.
        
        Formula: min(base_delay * (exponential_base ^ attempt), max_delay)
        With jitter: adds random 0-25% of calculated delay
        """
        # Exponential backoff: base_delay * (exponential_base ^ attempt)
        delay = self.base_delay * (self.exponential_base ** attempt)
        
        # Cap at max_delay
        delay = min(delay, self.max_delay)
        
        # Add jitter if enabled (random 0-25% of delay)
        if self.jitter:
            jitter_amount = delay * random.uniform(0, 0.25)
            delay += jitter_amount
        
        return delay
    
    def should_retry(self, exception: Exception, attempt: int) -> bool:
        """Check if exception should trigger a retry"""
        if attempt >= self.max_retries:
            return False
        
        # Check if exception type is retryable
        return any(isinstance(exception, exc_type) for exc_type in self.retryable_exceptions)


def retry_with_backoff(
    config: Optional[RetryConfig] = None,
    operation_name: Optional[str] = None
) -> Callable:
    """
    Decorator for retrying operations with exponential backoff.
    
    Args:
        config: RetryConfig instance (uses default if None)
        operation_name: Name of operation for logging
    
    Example:
        @retry_with_backoff(RetryConfig(max_retries=5, base_delay=2.0))
        def my_agent_call():
            return llm_service.invoke(...)
    """
    if config is None:
        config = RetryConfig()
    
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            op_name = operation_name or func.__name__
            last_error = None
            
            for attempt in range(config.max_retries):
                try:
                    result = func(*args, **kwargs)
                    if attempt > 0:
                        logger.info(
                            f"{op_name} succeeded after {attempt + 1} attempts",
                            operation=op_name,
                            attempts=attempt + 1
                        )
                    return result
                    
                except Exception as e:
                    last_error = e
                    
                    if not config.should_retry(e, attempt):
                        logger.error(
                            f"{op_name} failed and is not retryable",
                            operation=op_name,
                            error=str(e),
                            attempt=attempt + 1
                        )
                        raise
                    
                    if attempt < config.max_retries - 1:
                        delay = config.calculate_delay(attempt)
                        logger.warning(
                            f"{op_name} failed (attempt {attempt + 1}/{config.max_retries}), retrying in {delay:.2f}s",
                            operation=op_name,
                            error=str(e),
                            attempt=attempt + 1,
                            delay=delay
                        )
                        time.sleep(delay)
                    else:
                        logger.error(
                            f"{op_name} failed after {config.max_retries} attempts",
                            operation=op_name,
                            error=str(e),
                            attempts=config.max_retries
                        )
            
            # Should not reach here, but just in case
            raise last_error or Exception(f"{op_name} failed after {config.max_retries} attempts")
        
        return wrapper
    return decorator


# Pre-configured retry configs for different agent types
AGENT_RETRY_CONFIGS = {
    "resume_parser": RetryConfig(
        max_retries=3,
        base_delay=1.0,
        max_delay=10.0,
        exponential_base=2.0,
        jitter=True
    ),
    "jd_analyzer": RetryConfig(
        max_retries=3,
        base_delay=1.0,
        max_delay=10.0,
        exponential_base=2.0,
        jitter=True
    ),
    "fit_evaluator": RetryConfig(
        max_retries=3,
        base_delay=1.0,
        max_delay=10.0,
        exponential_base=2.0,
        jitter=True
    ),
    "ats_scorer": RetryConfig(
        max_retries=3,
        base_delay=1.0,
        max_delay=10.0,
        exponential_base=2.0,
        jitter=True
    ),
    "resume_tailor": RetryConfig(
        max_retries=4,  # More retries for critical operation
        base_delay=1.5,
        max_delay=15.0,
        exponential_base=2.0,
        jitter=True
    ),
    "review_agent": RetryConfig(
        max_retries=3,
        base_delay=1.0,
        max_delay=10.0,
        exponential_base=2.0,
        jitter=True
    ),
    "validation": RetryConfig(
        max_retries=2,  # Fewer retries for validation (less critical)
        base_delay=0.5,
        max_delay=5.0,
        exponential_base=2.0,
        jitter=True
    )
}


def get_agent_retry_config(agent_type: str) -> RetryConfig:
    """Get retry configuration for a specific agent type"""
    return AGENT_RETRY_CONFIGS.get(agent_type, RetryConfig())
