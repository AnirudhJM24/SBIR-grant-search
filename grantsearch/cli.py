"""Command-line entry point.

    python -m grantsearch.cli ingest --years 2015-2025 --agency NASA --state CA
    python -m grantsearch.cli profile data/raw
    python -m grantsearch.cli probe-api
"""

from __future__ import annotations

import argparse
import collections
import logging
import sys
from pathlib import Path

from .clean import AGENCY_NAMES, agency_display, branch_display
from .filters import RecordFilter
from .ingest import get_adapter, load_records, run_ingest
from .ingest.sbir_api import SbirApiAdapter


def parse_years(spec: str) -> list[int]:
    """Parse ``"2015-2025"``, ``"2020,2022"`` or ``"2021"`` into a year list."""
    years: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part.lstrip("-"):
            lo, _, hi = part.partition("-")
            start, end = int(lo), int(hi)
            if start > end:
                raise argparse.ArgumentTypeError(f"Empty year range: {part}")
            years.update(range(start, end + 1))
        else:
            years.add(int(part))
    if not years:
        raise argparse.ArgumentTypeError(f"No years parsed from {spec!r}")
    return sorted(years)


def build_filter(args: argparse.Namespace) -> RecordFilter:
    """Assemble a RecordFilter from parsed CLI flags."""
    return RecordFilter(
        agencies=set(args.agency) if args.agency else None,
        branches=set(args.branch) if args.branch else None,
        programs=set(args.program) if args.program else None,
        phases=set(args.phase) if args.phase else None,
        states=set(args.state) if args.state else None,
        fiscal_year_min=args.year_min,
        fiscal_year_max=args.year_max,
        start_after=args.start_after,
        start_before=args.start_before,
        end_after=args.end_after,
        end_before=args.end_before,
        min_amount=args.min_amount,
        max_amount=args.max_amount,
        require_abstract=not args.allow_empty_abstract,
        min_abstract_chars=args.min_abstract_chars,
        require_ri=args.require_ri,
        text_contains=args.contains,
    )


def add_filter_args(parser: argparse.ArgumentParser) -> None:
    """Attach the shared filter flags to a subcommand."""
    group = parser.add_argument_group("filters")
    group.add_argument(
        "--agency", action="append", metavar="AGENCY",
        help="Code or full name, repeatable. One of: "
             + ", ".join(
                 c if c == n else f"{c} ({n})" for c, n in AGENCY_NAMES.items()
             ),
    )
    group.add_argument("--branch", action="append", metavar="NAME",
                       help="Sub-agency, e.g. NIH, ARPA-E, NIFA (repeatable)")
    group.add_argument("--program", action="append", choices=["SBIR", "STTR"],
                       help="Funding program (repeatable)")
    group.add_argument("--phase", action="append", metavar="PHASE",
                       help='e.g. "Phase I" (repeatable)')
    group.add_argument("--state", action="append", metavar="ST",
                       help="Two-letter state code (repeatable)")
    group.add_argument("--year-min", type=int, help="Min fiscal year")
    group.add_argument("--year-max", type=int, help="Max fiscal year")
    group.add_argument("--start-after", metavar="DATE", help="Project start >= DATE")
    group.add_argument("--start-before", metavar="DATE", help="Project start <= DATE")
    group.add_argument("--end-after", metavar="DATE", help="Project end >= DATE")
    group.add_argument("--end-before", metavar="DATE", help="Project end <= DATE")
    group.add_argument("--min-amount", type=float, help="Min award amount")
    group.add_argument("--max-amount", type=float, help="Max award amount")
    group.add_argument("--min-abstract-chars", type=int, default=0,
                       help="Drop records with shorter abstracts")
    group.add_argument("--allow-empty-abstract", action="store_true",
                       help="Keep records with no abstract")
    group.add_argument("--require-ri", action="store_true",
                       help="Only awards with an STTR research-institution partner")
    group.add_argument("--contains", metavar="TEXT",
                       help="Substring match over title + abstract + company")


def cmd_ingest(args: argparse.Namespace) -> int:
    kwargs: dict = {}
    if args.source in ("sbir", "sbir_csv"):
        kwargs = {"cache_dir": args.cache_dir, "refresh": args.refresh}
        if args.csv_path:
            kwargs["csv_path"] = args.csv_path
    if args.source == "sbir":
        kwargs["transport"] = args.transport

    adapter = get_adapter(args.source, **kwargs)
    result = run_ingest(
        adapter,
        years=args.years,
        out_dir=args.out_dir,
        record_filter=build_filter(args),
        overwrite=args.overwrite,
        limit=args.limit,
    )
    print(result.summary())
    if (used := getattr(adapter, "transport_used", None)):
        print(f"transport: {used}")
    return 0 if result.written else 1


def cmd_profile(args: argparse.Namespace) -> int:
    """Corpus profile: counts, coverage, distributions."""
    counters = {k: collections.Counter() for k in
                ("agency", "branch", "program", "phase", "state", "year", "source")}
    total = with_abstract = with_amount = with_ri = with_dates = 0
    abstract_chars = 0
    amounts: list[float] = []
    ids: set[str] = set()
    duplicates = 0

    for record in load_records(args.path, record_filter=build_filter(args)):
        total += 1
        if record.record_id in ids:
            duplicates += 1
        ids.add(record.record_id)
        counters["agency"][record.agency or "?"] += 1
        counters["branch"][record.branch or "?"] += 1
        counters["program"][record.program or "?"] += 1
        counters["phase"][record.phase or "?"] += 1
        counters["state"][record.state or "?"] += 1
        counters["year"][record.fiscal_year or 0] += 1
        counters["source"][record.source] += 1
        if record.abstract_clean:
            with_abstract += 1
            abstract_chars += len(record.abstract_clean)
        if record.award_amount is not None:
            with_amount += 1
            amounts.append(record.award_amount)
        if record.ri_name:
            with_ri += 1
        if record.start_date and record.end_date:
            with_dates += 1

    if not total:
        print(f"No records found in {args.path}")
        return 1

    def pct(n: int) -> str:
        return f"{n:,} ({n / total:.1%})"

    print(f"records                {total:,}")
    print(f"unique record_ids      {len(ids):,}  (duplicates: {duplicates:,})")
    print(f"with abstract          {pct(with_abstract)}")
    print(f"with award amount      {pct(with_amount)}")
    print(f"with start+end dates   {pct(with_dates)}")
    print(f"with RI (spinout sig.) {pct(with_ri)}")
    if with_abstract:
        print(f"mean abstract chars    {abstract_chars / with_abstract:,.0f}")
    if amounts:
        amounts.sort()
        print(f"award amount median    ${amounts[len(amounts) // 2]:,.0f}")
        print(f"award amount total     ${sum(amounts):,.0f}")

    # Agencies print as "CODE  Readable Name" so the value you pass to
    # --agency is visible next to the name you actually read.
    print("\nagency:")
    for code, count in counters["agency"].most_common(15):
        print(f"  {code:<6} {agency_display(code):<33} {count:>8,}")

    if len(counters["branch"]) > 1:
        print("\nbranch (sub-agency):")
        for value, count in counters["branch"].most_common(10):
            print(f"  {branch_display(value):<40} {count:>8,}")

    for key in ("source", "program", "phase"):
        print(f"\n{key}:")
        for value, count in counters[key].most_common(15):
            print(f"  {str(value):<40} {count:>8,}")
    print("\nyear:")
    for value, count in sorted(counters["year"].items()):
        print(f"  {value:<40} {count:>8,}")
    print("\ntop states:")
    for value, count in counters["state"].most_common(10):
        print(f"  {str(value):<40} {count:>8,}")
    return 0


def cmd_probe_api(args: argparse.Namespace) -> int:
    """Check whether the SBIR.gov JSON API has come back online."""
    if SbirApiAdapter().probe():
        print("SBIR.gov JSON API is UP -- `--source sbir_api` will work.")
        return 0
    print("SBIR.gov JSON API is DOWN (maintenance). Use `--source sbir_csv`.")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="grantsearch",
        description="Ingest US government innovation funding records.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="Fetch and normalize a source")
    p_ingest.add_argument("--source", default="sbir",
                          choices=["sbir", "sbir_csv", "sbir_api"],
                          help="'sbir' auto-selects a transport (default)")
    p_ingest.add_argument("--transport", default="auto",
                          choices=["auto", "api", "csv"],
                          help="For --source sbir: which transport to use")
    p_ingest.add_argument("--years", type=parse_years, default="2015-2025",
                          help='e.g. "2015-2025" or "2020,2022" (default: 2015-2025)')
    p_ingest.add_argument("--out-dir", type=Path, default=Path("data/raw"))
    p_ingest.add_argument("--cache-dir", type=Path, default=Path("data/cache"))
    p_ingest.add_argument("--csv-path", type=Path,
                          help="Use a local CSV instead of downloading")
    p_ingest.add_argument("--refresh", action="store_true",
                          help="Re-download the bulk CSV")
    p_ingest.add_argument("--overwrite", action="store_true",
                          help="Rewrite years that already exist")
    p_ingest.add_argument("--limit", type=int, help="Max records per year (smoke tests)")
    add_filter_args(p_ingest)
    p_ingest.set_defaults(func=cmd_ingest)

    p_profile = sub.add_parser("profile", help="Summarize an ingested corpus")
    p_profile.add_argument("path", type=Path, nargs="?", default=Path("data/raw"))
    add_filter_args(p_profile)
    p_profile.set_defaults(func=cmd_profile)

    p_probe = sub.add_parser("probe-api", help="Check if the SBIR.gov API is up")
    p_probe.set_defaults(func=cmd_probe_api)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    if isinstance(getattr(args, "years", None), str):
        args.years = parse_years(args.years)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
