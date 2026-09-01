"""Rank harvested ATS boards by how many roles match a real search.

`harvest_ats_companies.py` produces thousands of candidate boards. Merging them
all would swamp the catalog with companies that never post anything relevant, so
this pass fetches each board once and counts the postings that clear the title
gate for the role families you actually search. A board with eight matching
Staff-level backend roles is worth tracking; one with four hundred retail jobs
is not.

    python scripts/rank_harvested_companies.py \
        --harvest config/harvested_companies.yml \
        --families software_engineering platform_infrastructure applied_ai_llmops \
        --min-matches 2 --top 300 --out config/ranked_companies.yml

Merge the result into config/discovery_sources.yml with --merge.
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from resume_agent.services.discovery.ats_provider import (  # noqa: E402
    blocking_body_matches,
    fetch_ashby_jobs,
    fetch_greenhouse_jobs,
    passes_title_gate,
)
from resume_agent.services.discovery.source_catalog import (  # noqa: E402
    TrackedCompany,
    load_source_catalog,
)

USER_AGENT = "ResumeAgent-CatalogRanker/1.0"

# Enough to tell a US-reachable posting from an offshore one. Not a location
# filter — that is the search's job — just a signal for ranking boards.
US_MARKERS = (
    "united states", "usa", " us", "us ", "u.s.", "remote - us", "us remote",
    "america", "new york", "san francisco", "seattle", "boston", "austin",
    "chicago", "denver", "atlanta", "washington", "virginia", "maryland",
    "texas", "california", "massachusetts", ", ny", ", ca", ", va", ", wa",
    ", tx", ", ma", ", il", ", co", ", ga",
)


def _has_us_location(stub: dict) -> bool:
    locations = stub.get("locations") or [stub.get("location") or ""]
    return any(any(marker in str(item).lower() for marker in US_MARKERS) for item in locations)


def score_board(company: dict, criteria: dict, guardrails, body_guardrails, timeout: int) -> dict | None:
    """Fetch one board and count postings that clear the title gate."""
    tracked = TrackedCompany(
        name=company["name"],
        enabled=True,
        provider=company["provider"],
        careers_url=company["careers_url"],
        api_url=company["api_url"],
        tags=[],
        sponsorship_policy="unknown",
    )
    try:
        response = requests.get(tracked.api_url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
        if not response.ok:
            return None
        payload = response.json()
    except Exception:
        return None

    if tracked.provider == "greenhouse":
        stubs = fetch_greenhouse_jobs(payload, tracked)
    elif tracked.provider == "ashby":
        stubs = fetch_ashby_jobs(payload, tracked)
    else:
        return None

    if not stubs:
        return None
    matches = [stub for stub in stubs if passes_title_gate(stub, criteria, guardrails)]
    if not matches:
        return None

    # Ranking on the title gate alone promotes exactly the wrong boards: defense
    # contractors whose every role needs a clearance, and large foreign-only
    # boards. Count the roles that are actually reachable instead.
    reachable = [
        stub
        for stub in matches
        if not blocking_body_matches(stub, body_guardrails) and _has_us_location(stub)
    ]
    if not reachable:
        return None
    return {
        **company,
        "total_postings": len(stubs),
        "matching_postings": len(matches),
        "reachable_postings": len(reachable),
        "sample_titles": [str(stub.get("title") or "")[:70] for stub in reachable[:3]],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--harvest", required=True, help="output of harvest_ats_companies.py")
    parser.add_argument("--catalog", default="config/discovery_sources.yml")
    parser.add_argument("--families", nargs="+", default=["software_engineering", "platform_infrastructure"])
    parser.add_argument("--min-matches", type=int, default=2)
    parser.add_argument("--top", type=int, default=300)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--out", default="config/ranked_companies.yml")
    parser.add_argument("--merge", action="store_true", help="append the winners to the catalog")
    args = parser.parse_args()

    catalog = load_source_catalog(args.catalog)
    known = {company.api_url for company in catalog.tracked_companies}
    candidates = [
        company
        for company in (yaml.safe_load(Path(args.harvest).read_text())["tracked_companies"] or [])
        if company.get("api_url") not in known
    ]
    print(f"scoring {len(candidates)} boards not already in the catalog", file=sys.stderr)

    criteria = {
        "role_families": args.families,
        "seniority": "any",
        "remote_modes": [],
        "include_locations": [],
        "exclude_locations": [],
        "must_have_keywords": [],
        "avoid_keywords": [],
        "search_intent": "",
    }

    scored: list[dict] = []
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(score_board, company, criteria, catalog.title_guardrails, catalog.body_guardrails, args.timeout): company
            for company in candidates
        }
        for future in as_completed(futures):
            done += 1
            if done % 200 == 0:
                print(f"  {done}/{len(candidates)} scored, {len(scored)} with matches", file=sys.stderr)
            try:
                result = future.result()
            except Exception:
                continue
            if result and result["reachable_postings"] >= args.min_matches:
                scored.append(result)

    scored.sort(key=lambda item: (-item["reachable_postings"], item["name"]))
    winners = scored[: args.top]
    print(f"{len(scored)} boards cleared --min-matches; keeping {len(winners)}", file=sys.stderr)

    Path(args.out).write_text(
        yaml.safe_dump({"version": 1, "tracked_companies": winners}, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    print(f"wrote {args.out}", file=sys.stderr)
    for company in winners[:15]:
        print(f"  {company['reachable_postings']:4} reachable / {company['matching_postings']:4} matched | {company['name'][:24]:24} | {company['sample_titles'][0][:44]}", file=sys.stderr)

    if not args.merge:
        print("\nRe-run with --merge to append these to the catalog.", file=sys.stderr)
        return 0

    catalog_path = Path(args.catalog)
    raw = yaml.safe_load(catalog_path.read_text())
    for company in winners:
        raw["tracked_companies"].append(
            {
                "name": company["name"],
                "enabled": True,
                "provider": company["provider"],
                "careers_url": company["careers_url"],
                "api_url": company["api_url"],
                "tags": ["harvested"],
                "sponsorship_policy": "unknown",
            }
        )
    catalog_path.write_text(
        yaml.safe_dump(raw, sort_keys=False, allow_unicode=True, width=1000), encoding="utf-8"
    )
    print(f"merged {len(winners)} companies into {catalog_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
