"""
Manual smoke test for JD extraction.

Usage:
  ./.venv/bin/python scripts/jd_extraction_smoke.py "https://example.com/job-posting"
"""

import sys

from resume_agent.agents.jd_extractor import extract_clean_jd
from resume_agent.services.llm_service import LLMService


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: jd_extraction_smoke.py <job_url>")
        return 2
    url = sys.argv[1].strip()
    llm_service = LLMService()
    jd_text = extract_clean_jd(url, llm_service, max_retries=3)
    print(jd_text[:1500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

