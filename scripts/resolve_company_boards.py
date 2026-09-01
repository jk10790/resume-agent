"""Resolve company names to public ATS boards.

Job-aggregator connectors (Indeed, Dice) are poor sources of *postings* for this
tool: they return a title, a company and a tracking redirect, with no job body,
so the clearance, sponsorship and discipline filters cannot run on them, and
their pool skews heavily toward staffing agencies.

They are useful for a different job — telling you which *companies* are hiring
for your profile. This script takes those company names and finds the public ATS
board for each, so they can be added to the catalog and read properly from then
on.

    python scripts/resolve_company_boards.py --names "Alarm.com" "Squarespace" \
        --out config/resolved_companies.yml

    python scripts/resolve_company_boards.py --names-file names.txt --merge
"""

from __future__ import annotations

import argparse
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
import yaml

USER_AGENT = "ResumeAgent-BoardResolver/1.0"

PROBES = [
    (
        "greenhouse",
        "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true",
        "https://job-boards.greenhouse.io/{token}",
    ),
    (
        "ashby",
        "https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true",
        "https://jobs.ashbyhq.com/{token}",
    ),
    (
        "lever",
        "https://api.lever.co/v0/postings/{token}?mode=json",
        "https://jobs.lever.co/{token}",
    ),
    (
        "smartrecruiters",
        "https://api.smartrecruiters.com/v1/companies/{token}/postings",
        "https://jobs.smartrecruiters.com/{token}",
    ),
]


def candidate_tokens(name: str) -> list[str]:
    """Board tokens a company plausibly uses, most likely first."""
    cleaned = re.sub(r"\b(inc|llc|corp|corporation|ltd|limited|co|company|group|technologies|holdings)\b\.?", " ", name, flags=re.IGNORECASE)
    words = re.findall(r"[A-Za-z0-9]+", cleaned)
    if not words:
        return []
    joined = "".join(words).lower()
    hyphenated = "-".join(word.lower() for word in words)
    return list(dict.fromkeys([joined, hyphenated, words[0].lower()]))


def count_postings(payload: object) -> int | None:
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        for key in ("jobs", "content"):
            if isinstance(payload.get(key), list):
                return len(payload[key])
    return None


def resolve(name: str, timeout: int) -> dict | None:
    for token in candidate_tokens(name):
        for provider, api_template, careers_template in PROBES:
            api_url = api_template.format(token=token)
            try:
                response = requests.get(api_url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
                if not response.ok:
                    continue
                postings = count_postings(response.json())
            except Exception:
                continue
            # An empty board is indistinguishable from a wrong guess, so a hit
            # has to actually contain postings.
            if postings:
                return {
                    "name": name,
                    "enabled": True,
                    "provider": provider,
                    "careers_url": careers_template.format(token=token),
                    "api_url": api_url,
                    "tags": ["resolved"],
                    "sponsorship_policy": "unknown",
                    "observed_postings": postings,
                }
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--names", nargs="*", default=[])
    parser.add_argument("--names-file", help="one company name per line")
    parser.add_argument("--catalog", default="config/discovery_sources.yml")
    parser.add_argument("--out", default="config/resolved_companies.yml")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--merge", action="store_true")
    args = parser.parse_args()

    names = list(args.names)
    if args.names_file:
        names.extend(
            line.strip() for line in Path(args.names_file).read_text().splitlines() if line.strip()
        )
    names = list(dict.fromkeys(names))
    if not names:
        parser.error("give --names or --names-file")

    resolved: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(resolve, name, args.timeout): name for name in names}
        for future in as_completed(futures):
            name = futures[future]
            try:
                result = future.result()
            except Exception:
                result = None
            if result:
                resolved.append(result)
                print(f"  {result['provider']:16} {result['observed_postings']:5} postings | {name}", file=sys.stderr)
            else:
                print(f"  {'no board found':16}       | {name}", file=sys.stderr)

    print(f"\nresolved {len(resolved)} of {len(names)}", file=sys.stderr)
    if not resolved:
        return 0

    Path(args.out).write_text(
        yaml.safe_dump({"version": 1, "tracked_companies": resolved}, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    print(f"wrote {args.out}", file=sys.stderr)

    if not args.merge:
        return 0

    catalog_path = Path(args.catalog)
    raw = yaml.safe_load(catalog_path.read_text())
    known = {company.get("api_url") for company in raw["tracked_companies"]}
    added = 0
    for company in resolved:
        if company["api_url"] in known:
            continue
        entry = {key: value for key, value in company.items() if key != "observed_postings"}
        raw["tracked_companies"].append(entry)
        added += 1
    catalog_path.write_text(
        yaml.safe_dump(raw, sort_keys=False, allow_unicode=True, width=1000), encoding="utf-8"
    )
    print(f"merged {added} new companies into {catalog_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
