"""A tour of sbirgrantsearch.

    pip install sbirgrantsearch
    python examples/demo.py

The first run downloads SBIR.gov's bulk export (~367 MB) into data/cache/
and reuses it afterwards. Pass a year to narrow the scan:

    python examples/demo.py 2022
"""

from __future__ import annotations

import logging
import sys

import sbirgrantsearch as gs

YEAR = int(sys.argv[1]) if len(sys.argv) > 1 else 2023


def rule(title: str) -> None:
    print(f"\n\033[1m{title}\033[0m\n" + "-" * 68)


def main() -> None:
    # The fallback announces itself at info level. Watch for it: the API is
    # tried first, and the bulk export takes over when it is unavailable.
    logging.basicConfig(level=logging.INFO, format="  [%(levelname)s] %(message)s")

    rule(f"1. One call, all agencies, FY{YEAR}")
    everything = gs.download(years=YEAR)
    print(everything.summary())

    rule("2. Filters compose")
    for label, kwargs in [
        ("NASA, California", dict(agency="NASA", state="CA")),
        ("DOE over $1M", dict(agency="DOE", min_amount=1_000_000)),
        ("NIH (a sub-agency of HHS)", dict(branch="NIH")),
        ("Phase II energy storage", dict(phase="Phase II", contains="energy storage")),
    ]:
        hits = everything.filter_by(**kwargs)
        print(f"  {label:<28} {len(hits):>5} awards")

    rule("3. Agency codes are stored; names are derived")
    for record in everything[:3]:
        print(f"  {record.agency:<5} {record.agency_label}")

    rule("4. Potential university spinouts (STTR + research institution)")
    spinouts = everything.filter_by(program="STTR", require_ri=True)
    print(f"  {len(spinouts)} of {len(everything)} awards name a university partner\n")
    for record in spinouts[:5]:
        print(f"  {record.recipient[:34]:<36} <- {record.ri_name[:30]}")

    rule("5. Group by company (no UEI in the data, so names are normalized)")
    for name, awards in list(everything.by_company().items())[:5]:
        total = sum(a.award_amount or 0 for a in awards)
        print(f"  {name[:38]:<40} {len(awards):>3} awards  ${total:>12,.0f}")

    rule("6. Text is cleaned for search")
    record = max(everything, key=lambda r: r.award_amount or 0)
    print(f"  {record.title[:64]}")
    print(f"  {record.recipient} | {record.agency_name} | ${record.award_amount:,.0f}")
    print(f"\n  {record.abstract_clean[:220]}...")

    rule("7. Export")
    top = everything.filter_by(min_amount=1_000_000)
    print(f"  to_csv   -> {top.to_csv('demo_top_awards.csv')} ({len(top)} rows)")
    print(f"  to_jsonl -> {top.to_jsonl('demo_top_awards.jsonl')}")
    print("  to_pandas() and to_dicts() are also available\n")


if __name__ == "__main__":
    main()
