# jd_extraction_agent.py

import requests
from bs4 import BeautifulSoup
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
        visible_text = visible_text.strip()
        if visible_text:
            return visible_text

        # Fallback for thin/client-rendered pages where body blocks are empty.
        fallback_parts = []
        title = (soup.title.string or "").strip() if soup.title and soup.title.string else ""
        if title:
            fallback_parts.append(title)
        for meta_name in ("description", "og:description", "twitter:description"):
            tag = soup.find("meta", attrs={"name": meta_name}) or soup.find("meta", attrs={"property": meta_name})
            content = tag.get("content", "").strip() if tag else ""
            if content:
                fallback_parts.append(content)
        fallback_text = "\n".join(part for part in fallback_parts if part).strip()
        if fallback_text:
            return fallback_text

        raise ExtractionError(
            (
                f"No readable job description content was found at {url}. "
                "This page may require JavaScript rendering or login. Paste the JD text manually instead."
            ),
            url=url,
        )
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

def prompt_llm_to_extract_jd(llm_service, raw_text):
    """Pull the posting out of scraped page text.

    One task, one call. Three differently-worded prompt styles used to be tried
    in turn, each followed by an LLM call asking whether the output looked like a
    job description -- up to six calls to read one posting. The styles differed
    only in tone, and `looks_like_a_job_description` answers the same question
    from the text itself, for nothing.
    """
    from ..config import settings

    trimmed_text = (raw_text or "").strip()
    if not trimmed_text:
        raise ExtractionError(
            "No readable job description text was extracted from the page. Paste the JD text manually instead."
        )

    return llm_service.run_task("jd.extract", raw_text=trimmed_text[: settings.jd_text_limit])


# Sections essentially every posting has, in one wording or another.
_JD_SIGNALS = (
    "responsibilit",
    "qualification",
    "requirement",
    "experience",
    "skills",
    "you will",
    "we are looking",
    "about the role",
    "what you",
)


def looks_like_a_job_description(text: str, *, min_words: int = 60) -> bool:
    """Whether extracted text plausibly is a posting.

    Deterministic on purpose: this is a shape question, and the answer does not
    improve for being asked of a model once per extraction attempt.
    """
    body = (text or "").strip()
    if len(body.split()) < min_words:
        return False
    lowered = body.lower()
    return sum(signal in lowered for signal in _JD_SIGNALS) >= 2


def extract_clean_jd(url, llm_service, use_cache=True):
    """
    Extract clean job description from URL with caching support.
    
    Args:
        url: Job listing URL
        llm_service: LLMService instance
        use_cache: Whether to use cache
    
    Returns:
        Extracted job description text
    """
    # Check cache first
    if use_cache:
        cached = _jd_cache.get(url)
        if cached:
            logger.info("Using cached JD", url=url)
            return cached.get("content", "")
    
    with track_operation("Extracting job description"):
        raw_text = extract_raw_text(url)
        jd_text = prompt_llm_to_extract_jd(llm_service, raw_text)
        is_valid = looks_like_a_job_description(jd_text)

        if not is_valid:
            logger.warning("Extracted text does not look like a job description", url=url)

        if use_cache:
            record = {"content": jd_text, "url": url}
            if not is_valid:
                record["warning"] = "Did not look like a job description"
            _jd_cache.set(url, record)
        else:
            logger.info("JD extraction complete", url=url, valid=is_valid)

        return jd_text
