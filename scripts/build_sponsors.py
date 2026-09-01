"""Derive sponsorship_policy for catalog companies from DOL disclosure data.

Most sponsorship tooling reads LCA filings, which answer "does this employer
file H-1Bs at all". That is the right question for someone who needs a new
cap-subject petition. It is the wrong question for someone already on an H-1B
with an approved I-140, who needs an employer that carries people through to a
green card. PERM volume answers that one: heavy LCA volume with near-zero PERM
filings means an employer sponsors labor, not careers.

Download the spreadsheets from the Office of Foreign Labor Certification
performance-data page (they are large, and the URLs change every quarter, so
they are not hardcoded here):

    PERM_Disclosure_Data_FY20XX.xlsx    green card filings   (primary signal)
    LCA_Disclosure_Data_FY20XX_Qx.xlsx  H-1B filings         (optional context)

    python scripts/build_sponsors.py --perm PERM_Disclosure_Data_FY2025.xlsx \
        --lca LCA_Disclosure_Data_FY2025_Q3.xlsx --apply

Without --apply this reports what it would change and writes nothing.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

import pandas as pd
import yaml


# Corporate suffixes and filler that differ between DOL's legal names and the
# short names used in the catalog ("STRIPE, INC." vs "Stripe").
SUFFIX = re.compile(
    r"\b(inc|incorporated|llc|l\.l\.c|corp|corporation|ltd|limited|co|"
    r"company|plc|lp|llp|holdings|group|technologies|technology|labs|"
    r"software|systems|solutions|services|usa|us|america)\b\.?",
    re.IGNORECASE,
)

# PERM filings per employer needed before calling them a green-card sponsor.
STRONG_PERM_THRESHOLD = 10
WEAK_PERM_THRESHOLD = 2


def normalize(name: object) -> str:
    text = str(name).lower()
    text = re.sub(r"[.,&']", " ", text)
    text = SUFFIX.sub(" ", text)
    return re.sub(r"[^a-z0-9]", "", text)


def name_variants(name: object) -> list[str]:
    """Forms a catalog entry might match on.

    Harvested entries are named after their ATS board token, which sometimes
    carries a disambiguating digit ("addepar1") that the DOL legal name has no
    equivalent for.
    """
    base = normalize(name)
    return [variant for variant in dict.fromkeys([base, re.sub(r"\d+$", "", base)]) if variant]


# The employer column is named differently across years: FY2024 and earlier use
# EMPLOYER_NAME, the FY2025 layout uses EMP_BUSINESS_NAME.
EMPLOYER_COLUMNS = ("EMPLOYER_NAME", "EMP_BUSINESS_NAME", "EMPLOYER_LEGAL_BUSINESS_NAME")


def read_counts(paths: list[str], label: str) -> Counter:
    counts: Counter = Counter()
    for path in paths:
        print(f"reading {label}: {path} ...", file=sys.stderr)
        frame = pd.read_excel(
            path,
            usecols=lambda column: str(column).upper() in EMPLOYER_COLUMNS
            or "CASE_STATUS" in str(column).upper(),
        )
        columns = {str(column).upper(): column for column in frame.columns}
        employer = next((columns[name] for name in EMPLOYER_COLUMNS if name in columns), None)
        if not employer:
            print(f"  no employer column in {path} (looked for {', '.join(EMPLOYER_COLUMNS)}), skipping", file=sys.stderr)
            continue
        status = columns.get("CASE_STATUS")
        if status:
            # Denied and withdrawn filings say nothing about willingness to sponsor.
            frame = frame[frame[status].astype(str).str.upper().str.contains("CERTIFIED", na=False)]
        counts.update(normalize(value) for value in frame[employer].dropna())
    counts.pop("", None)
    return counts


def classify(perm_count: int, lca_count: int) -> tuple[str, str]:
    """Map filing volumes onto a sponsorship_policy value, with a reason."""
    if perm_count >= STRONG_PERM_THRESHOLD:
        return "yes", f"{perm_count} certified PERM filings"
    if perm_count >= WEAK_PERM_THRESHOLD:
        return "yes", f"{perm_count} certified PERM filings (light volume)"
    if lca_count >= STRONG_PERM_THRESHOLD and perm_count == 0:
        # Files H-1Bs in volume but moves nobody toward a green card.
        return "no", f"{lca_count} LCA filings but no certified PERM filings"
    return "unknown", "insufficient filing history"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--perm", nargs="+", required=True, help="PERM disclosure spreadsheet(s)")
    parser.add_argument("--lca", nargs="*", default=[], help="LCA disclosure spreadsheet(s), for context")
    parser.add_argument("--catalog", default="config/discovery_sources.yml")
    parser.add_argument("--sponsors-out", default="config/sponsors.json")
    parser.add_argument("--apply", action="store_true", help="write sponsorship_policy back into the catalog")
    args = parser.parse_args()

    perm_counts = read_counts(args.perm, "PERM")
    lca_counts = read_counts(args.lca, "LCA") if args.lca else Counter()

    sponsors = {
        key: {"perm": perm_counts.get(key, 0), "lca": lca_counts.get(key, 0)}
        for key in set(perm_counts) | set(lca_counts)
        if perm_counts.get(key, 0) >= WEAK_PERM_THRESHOLD or lca_counts.get(key, 0) >= STRONG_PERM_THRESHOLD
    }
    sponsors_path = Path(args.sponsors_out)
    sponsors_path.parent.mkdir(parents=True, exist_ok=True)
    sponsors_path.write_text(json.dumps(sponsors, indent=0, sort_keys=True), encoding="utf-8")
    print(f"wrote {sponsors_path} — {len(sponsors)} employers", file=sys.stderr)

    catalog_path = Path(args.catalog)
    if not catalog_path.exists():
        print(f"catalog {catalog_path} not found; sponsors file written, nothing to apply", file=sys.stderr)
        return 0

    raw = catalog_path.read_text(encoding="utf-8")
    catalog = yaml.safe_load(raw) or {}
    companies = catalog.get("tracked_companies") or []

    changes: list[tuple[str, str, str, str]] = []
    for company in companies:
        variants = name_variants(company.get("name"))
        perm = max((perm_counts.get(variant, 0) for variant in variants), default=0)
        lca = max((lca_counts.get(variant, 0) for variant in variants), default=0)
        policy, reason = classify(perm, lca)
        current = str(company.get("sponsorship_policy") or "unknown")
        # Only fill genuine gaps; a hand-set value reflects knowledge this
        # dataset does not have (a recruiter conversation, a past application).
        if policy != "unknown" and current == "unknown":
            changes.append((str(company.get("name")), current, policy, reason))
            company["sponsorship_policy"] = policy

    if not changes:
        print("no catalog companies matched with enough filing history", file=sys.stderr)
        return 0

    width = max(len(name) for name, _, _, _ in changes)
    for name, current, policy, reason in changes:
        print(f"  {name:{width}}  {current} -> {policy:7}  ({reason})", file=sys.stderr)

    if not args.apply:
        print(f"\n{len(changes)} companies would change. Re-run with --apply to write them.", file=sys.stderr)
        return 0

    catalog_path.write_text(
        yaml.safe_dump(catalog, sort_keys=False, allow_unicode=True, width=1000),
        encoding="utf-8",
    )
    print(f"\nupdated {len(changes)} companies in {catalog_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
