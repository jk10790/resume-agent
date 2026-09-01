"""Harvest ATS board tokens from the Common Crawl URL index.

The discovery catalog can only ever surface roles at companies it already
lists, so catalog breadth — not extraction quality — is the ceiling on what
Discover can find. Common Crawl already indexed the public career pages of
essentially every company using a hosted ATS, so the tokens can be read out of
its index rather than scraped from anyone's site.

    python scripts/harvest_ats_companies.py --crawl CC-MAIN-2026-33 \
        --providers greenhouse ashby --out config/harvested_companies.yml

Output is a catalog fragment. Review it, then merge the companies you want into
config/discovery_sources.yml — this deliberately does not write that file, since
sponsorship_policy and tags are hand-curated judgements.
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.parse import quote

import requests
import yaml


CDX_ENDPOINT = "https://index.commoncrawl.org/{crawl}-index"
COLLINFO_URL = "https://index.commoncrawl.org/collinfo.json"
USER_AGENT = "ResumeAgent-CatalogHarvester/1.0"

# One pattern per provider: the index query, and the regex that lifts the board
# token out of a matching URL.
PROVIDERS: dict[str, dict[str, str]] = {
    "greenhouse": {
        "query": "boards.greenhouse.io/*",
        "pattern": r"^https?://(?:job-boards|boards)(?:\.eu)?\.greenhouse\.io/([a-z0-9][a-z0-9._-]{1,60})(?:/|$)",
        "careers_url": "https://job-boards.greenhouse.io/{token}",
        "api_url": "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true",
    },
    "ashby": {
        "query": "jobs.ashbyhq.com/*",
        "pattern": r"^https?://jobs\.ashbyhq\.com/([a-z0-9][a-z0-9._-]{1,60})(?:/|$)",
        "careers_url": "https://jobs.ashbyhq.com/{token}",
        "api_url": "https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true",
    },
    "smartrecruiters": {
        "query": "jobs.smartrecruiters.com/*",
        "pattern": r"^https?://jobs\.smartrecruiters\.com/([A-Za-z0-9][A-Za-z0-9._-]{1,60})(?:/|$)",
        "careers_url": "https://jobs.smartrecruiters.com/{token}",
        "api_url": "https://api.smartrecruiters.com/v1/companies/{token}/postings",
    },
}

# Lever is deliberately absent. jobs.lever.co/robots.txt sets "User-agent: CCBot
# / Disallow: /", so Common Crawl holds nothing but its robots.txt and there are
# no tokens to harvest. Reading api.lever.co at runtime stays fine — that host
# allows all agents — but Lever boards have to be added to the catalog by hand.


# Paths that look like tokens but are site chrome rather than a company board.
RESERVED_TOKENS = {
    "embed", "static", "assets", "api", "v1", "search", "jobs", "job",
    "careers", "company", "companies", "login", "signup", "about", "privacy",
    "terms", "favicon.ico", "robots.txt", "sitemap.xml", "index.html",
}


def resolve_crawl(requested: str | None) -> str:
    if requested and requested.lower() != "latest":
        return requested
    response = requests.get(COLLINFO_URL, headers={"User-Agent": USER_AGENT}, timeout=60)
    response.raise_for_status()
    return response.json()[0]["id"]


def harvest_provider(crawl: str, provider: str, limit: int, pause: float) -> Counter:
    """Page the CDX index for one provider, returning token -> observation count."""
    spec = PROVIDERS[provider]
    pattern = re.compile(spec["pattern"], re.IGNORECASE)
    endpoint = CDX_ENDPOINT.format(crawl=crawl)
    tokens: Counter = Counter()

    query = quote(spec["query"], safe="")
    # Ask how many pages exist rather than walking until it errors: the index
    # answers a request past the last page with a 400, not a 404.
    total_pages = None
    try:
        meta = requests.get(
            f"{endpoint}?url={query}&output=json&showNumPages=true",
            headers={"User-Agent": USER_AGENT},
            timeout=300,
        )
        if meta.ok:
            total_pages = int(meta.json().get("pages") or 0)
            print(f"  {provider}: index reports {total_pages} pages", file=sys.stderr)
    except Exception as exc:
        print(f"  {provider}: could not read page count ({exc}); walking until exhausted", file=sys.stderr)

    page = 0
    while total_pages is None or page < total_pages:
        url = f"{endpoint}?url={query}&output=json&page={page}"
        # The index gateway times out regularly on the larger providers; a
        # transient 5xx should cost one page at worst, not the whole run.
        response = None
        for attempt in range(5):
            try:
                response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=300, stream=True)
            except requests.RequestException as exc:
                print(f"  {provider}: page {page} request failed ({exc}); retrying", file=sys.stderr)
                time.sleep(pause * (2 ** attempt) + 2)
                continue
            if response.status_code < 500:
                break
            print(f"  {provider}: page {page} returned {response.status_code}; retrying", file=sys.stderr)
            time.sleep(pause * (2 ** attempt) + 2)
        if response is None or response.status_code >= 500:
            print(f"  {provider}: page {page} still failing, stopping this provider", file=sys.stderr)
            break
        if response.status_code in (400, 404):
            break  # past the last page
        response.raise_for_status()

        rows = 0
        body = response.content
        if body[:2] == b"\x1f\x8b":
            body = gzip.decompress(body)
        for line in body.splitlines():
            if not line.strip():
                continue
            rows += 1
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            match = pattern.match(record.get("url") or "")
            if not match:
                continue
            token = match.group(1).lower().strip("._-")
            if token and token not in RESERVED_TOKENS and not token.endswith((".js", ".css", ".png", ".svg")):
                tokens[token] += 1

        print(f"  {provider}: page {page}, {rows} index rows, {len(tokens)} distinct tokens", file=sys.stderr)
        if rows == 0 or (limit and len(tokens) >= limit):
            break
        page += 1
        time.sleep(pause)

    return tokens


def verify_token(provider: str, token: str, timeout: int) -> int | None:
    """Return the live posting count, or None if the board does not resolve.

    Worth doing: Common Crawl is a snapshot, so a meaningful share of harvested
    tokens are boards that have since moved or closed.
    """
    api_url = PROVIDERS[provider]["api_url"].format(token=token)
    try:
        response = requests.get(api_url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
        if not response.ok:
            return None
        payload = response.json()
    except Exception:
        return None

    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        for key in ("jobs", "content"):
            if isinstance(payload.get(key), list):
                return len(payload[key])
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--crawl", default="latest", help="Common Crawl id, e.g. CC-MAIN-2026-33 (default: newest)")
    parser.add_argument("--providers", nargs="+", default=["greenhouse", "ashby"], choices=sorted(PROVIDERS))
    parser.add_argument("--out", default="config/harvested_companies.yml")
    parser.add_argument("--limit", type=int, default=0, help="stop after this many tokens per provider (0 = no limit)")
    parser.add_argument("--min-hits", type=int, default=2, help="drop tokens seen fewer times than this in the index")
    parser.add_argument("--verify", action="store_true", help="check each board resolves, and record its posting count")
    parser.add_argument("--min-postings", type=int, default=1, help="with --verify, drop boards with fewer postings")
    parser.add_argument("--pause", type=float, default=0.5, help="seconds between index requests")
    args = parser.parse_args()

    crawl = resolve_crawl(args.crawl)
    print(f"Harvesting from {crawl}", file=sys.stderr)

    companies: list[dict] = []
    for provider in args.providers:
        try:
            tokens = harvest_provider(crawl, provider, args.limit, args.pause)
        except Exception as exc:
            print(f"{provider}: harvest failed ({exc}); skipping", file=sys.stderr)
            continue
        kept = [token for token, hits in tokens.most_common() if hits >= args.min_hits]
        print(f"{provider}: {len(tokens)} tokens, {len(kept)} above --min-hits", file=sys.stderr)

        for token in kept:
            postings = None
            if args.verify:
                postings = verify_token(provider, token, timeout=20)
                if postings is None or postings < args.min_postings:
                    continue
                time.sleep(args.pause / 5)
            companies.append(
                {
                    "name": token,
                    "enabled": True,
                    "provider": provider,
                    "careers_url": PROVIDERS[provider]["careers_url"].format(token=token),
                    "api_url": PROVIDERS[provider]["api_url"].format(token=token),
                    "tags": ["harvested"],
                    # Left unknown on purpose: sponsorship is a curated judgement.
                    # scripts/build_sponsors.py can fill this from DOL PERM data.
                    "sponsorship_policy": "unknown",
                    **({"observed_postings": postings} if postings is not None else {}),
                }
            )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        yaml.safe_dump({"version": 1, "tracked_companies": companies}, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    print(f"Wrote {len(companies)} companies to {out_path}", file=sys.stderr)
    print("Review it, then merge the entries you want into config/discovery_sources.yml.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
