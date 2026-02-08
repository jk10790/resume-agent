# jd_extraction_agent.py

import requests
from bs4 import BeautifulSoup
from langchain_core.messages import SystemMessage, HumanMessage
from ..utils.cache import JDCache
from ..utils.logger import logger
from ..utils.progress import track_operation
from ..utils.exceptions import ExtractionError

# Global JD cache
_jd_cache = JDCache()

def extract_raw_text(url):
    """Extract raw text from URL with error handling"""
    from ..config import settings
    
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        timeout = settings.jd_extraction_timeout
        response = requests.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        elements = soup.find_all(["p", "li", "div"])
        visible_text = "\n".join(e.get_text(strip=True) for e in elements if e.get_text(strip=True))
        return visible_text.strip()
    except requests.exceptions.RequestException as e:
        raise ExtractionError(
            f"Failed to fetch URL {url}: {e}",
            url=url
        )
    except Exception as e:
        raise ExtractionError(
            f"Unexpected error extracting text from {url}: {e}",
            url=url
        )

def prompt_llm_to_extract_jd(llm_service, raw_text, style="default"):
    system_prompts = {
        "default": (
            "You are a helpful AI assistant. Given raw text scraped from a job listing webpage, "
            "extract and return only the job description and requirements section. Ignore headers, navigation, "
            "menus, footers, social links, etc."
        ),
        "strict": (
            "You are an expert at extracting job descriptions from messy web text. Only return the core job content: "
            "the job responsibilities, qualifications, requirements, and skills. Never include menus or unrelated content."
        ),
        "lenient": (
            "You are an assistant helping to extract a job description. Return any and all text that seems related to the job itself, "
            "including responsibilities, company background, and skills. It’s okay to include broader job context."
        )
    }

    from ..services.llm_service import LLMService
    
    # Handle both LLMService and legacy model instances
    if isinstance(llm_service, LLMService):
        from ..config import settings
        
        messages = [
            SystemMessage(content=system_prompts.get(style, system_prompts["default"])),
            HumanMessage(content=raw_text[:settings.jd_text_limit])  # avoid hitting context limits
        ]
        return llm_service.invoke_with_retry(messages)
    else:
        # Legacy support for direct model instances
        from ..config import settings
        
        prompt = [
            SystemMessage(content=system_prompts.get(style, system_prompts["default"])),
            HumanMessage(content=raw_text[:settings.jd_text_limit])
        ]
        return llm_service.invoke(prompt)

def reflect_on_jd_output(llm_service, jd_text):
    from ..services.llm_service import LLMService
    
    # Handle both LLMService and legacy model instances
    if isinstance(llm_service, LLMService):
        messages = [
            SystemMessage(
                "You are an evaluator that reviews whether AI-extracted text looks like a valid job description."
            ),
            HumanMessage(
                f"Does the following text look like a valid job description? "
                f"Respond with only YES or NO.\n\n---\n{jd_text[:3000]}\n---"
            )
        ]
        response = llm_service.invoke_with_retry(messages).strip().lower()
        return "yes" in response
    else:
        # Legacy support for direct model instances
        reflection_prompt = [
            SystemMessage(
                "You are an evaluator that reviews whether AI-extracted text looks like a valid job description."
            ),
            HumanMessage(
                f"Does the following text look like a valid job description? "
                f"Respond with only YES or NO.\n\n---\n{jd_text[:3000]}\n---"
            )
        ]
        response = llm_service.invoke(reflection_prompt).strip().lower()
        return "yes" in response

def extract_clean_jd(url, llm_service, max_retries=None, use_cache=True):
    """
    Extract clean job description from URL with caching support.
    
    Args:
        url: Job listing URL
        llm_service: LLMService instance or legacy model instance
        max_retries: Maximum retry attempts (uses settings if None)
        use_cache: Whether to use cache
    
    Returns:
        Extracted job description text
    """
    from ..config import settings
    
    # Use settings if not provided
    if max_retries is None:
        max_retries = settings.jd_extraction_max_retries
    
    # Check cache first
    if use_cache:
        cached = _jd_cache.get(url)
        if cached:
            logger.info("Using cached JD", url=url)
            return cached.get("content", "")
    
    with track_operation("Extracting job description"):
        raw_text = extract_raw_text(url)
        prompt_styles = ["default", "strict", "lenient"]
        attempts = 0

        for style in prompt_styles[:max_retries]:
            logger.debug(f"JD extraction attempt {attempts + 1}", style=style, url=url)
            jd_text = prompt_llm_to_extract_jd(llm_service, raw_text, style=style)
            is_valid = reflect_on_jd_output(llm_service, jd_text)

            if is_valid:
                logger.info("JD extraction successful", style=style, url=url)
                # Cache the result
                if use_cache:
                    _jd_cache.set(url, {
                        "content": jd_text,
                        "url": url,
                        "extracted_with": style
                    })
                return jd_text
            else:
                logger.warning("JD extraction failed reflection", style=style, attempt=attempts+1)

            attempts += 1

        logger.warning("All JD extraction attempts failed", url=url)
        # Cache even failed result to avoid retrying immediately
        if use_cache:
            _jd_cache.set(url, {
                "content": jd_text,
                "url": url,
                "extracted_with": prompt_styles[-1],
                "warning": "Failed reflection"
            })
        return jd_text
