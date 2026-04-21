from __future__ import annotations

from typing import Protocol, TypedDict


class SearchHit(TypedDict):
    url: str
    title: str
    snippet: str
    source_domain: str


class SearchProvider(Protocol):
    name: str

    def search(self, query: str, max_results: int) -> list[SearchHit]:
        ...

