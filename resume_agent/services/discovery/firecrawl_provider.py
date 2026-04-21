from __future__ import annotations

from urllib.parse import urlparse

import requests

from ...config import settings
from ...utils.logger import logger
from .search_provider import SearchHit


class FirecrawlSearchProvider:
    name = "firecrawl"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.discover_firecrawl_api_key
        if not self.api_key:
            raise ValueError("Firecrawl API key is not configured")

    def search(self, query: str, max_results: int) -> list[SearchHit]:
        response = requests.post(
            "https://api.firecrawl.dev/v1/search",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "query": query,
                "limit": max_results,
                "scrapeOptions": {"formats": ["markdown"]},
            },
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        raw_results = payload.get("data") or payload.get("results") or []
        hits: list[SearchHit] = []
        for item in raw_results[:max_results]:
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            parsed = urlparse(url)
            source_domain = parsed.netloc.lower()
            hits.append(
                {
                    "url": url,
                    "title": str(item.get("title") or "").strip(),
                    "snippet": str(item.get("description") or item.get("snippet") or "").strip(),
                    "source_domain": source_domain,
                }
            )
        logger.info("Discovery provider search completed", provider=self.name, query=query, hits=len(hits))
        return hits

