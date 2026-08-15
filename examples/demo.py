"""A guided tour of sbirgrantsearch, against live SBIR.gov data.

    pip install sbirgrantsearch
    python examples/demo.py            # defaults to FY2023
    python examples/demo.py 2024

Every number below is fetched at runtime -- nothing here is canned. One
request pulls the year, and the rest is local filtering, so the whole tour
costs about two HTTP calls.

Colour is used when stdout is a terminal, and skipped otherwise (or when
NO_COLOR is set), so piping this into a file gives clean text.
"""

from __future__ import annotations

import os
import sys
import textwrap
from collections.abc import Iterable, Sequence

import sbirgrantsearch as gs

WIDTH = 74
YEAR = int(sys.argv[1]) if len(sys.argv) > 1 else 2023

# Windows consoles default to cp1252, which cannot encode the box-drawing
# and block characters used below.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# --------------------------------------------------------------------------
# Presentation helpers
# --------------------------------------------------------------------------

_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def bold(t: str) -> str:
    return _c("1", t)


def dim(t: str) -> str:
    return _c("2", t)


def cyan(t: str) -> str:
    return _c("36", t)


def green(t: str) -> str:
    return _c("32", t)


def yellow(t: str) -> str:
    return _c("33", t)


def banner(title: str, subtitle: str) -> None:
    print(f"\n╭{'─' * (WIDTH - 2)}╮")
    print(f"│ {bold(title):<{WIDTH - 4 + (len(bold(title)) - len(title))}} │")
    print(f"│ {dim(subtitle):<{WIDTH - 4 + (len(dim(subtitle)) - len(subtitle))}} │")
    print(f"╰{'─' * (WIDTH - 2)}╯")


def section(number: int, title: str, note: str = "") -> None:
    print(f"\n{cyan('▌')} {bold(f'{number}. {title}')}")
    for line in textwrap.wrap(note, WIDTH - 2):
        print(f"  {dim(line)}")
    print(dim("─" * WIDTH))


def code(line: str) -> None:
    """Show the call that produced the output beneath it."""
    print(f"  {dim('>>>')} {yellow(line)}")


def money(value: float) -> str:
    """Compact currency: $3.7B, $38.6M, $412K."""
    for cutoff, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= cutoff:
            return f"${value / cutoff:,.1f}{suffix}"
    return f"${value:,.0f}"


def bars(rows: Sequence[tuple[str, float]], *, width: int = 26,
         fmt=lambda v: f"{v:,.0f}", label_width: int = 30) -> None:
    """A horizontal bar chart, scaled to the largest value."""
    if not rows:
        return
    peak = max(v for _, v in rows) or 1
    for label, value in rows:
        filled = round(width * value / peak)
        bar = "█" * filled + dim("░" * (width - filled))
        print(f"  {label[:label_width]:<{label_width}} {bar} {fmt(value):>9}")


def table(headers: Sequence[str], rows: Iterable[Sequence[str]],
          widths: Sequence[int], aligns: str = "") -> None:
    """A simple aligned table with a dim rule under the header."""
    aligns = aligns or "<" * len(headers)
    head = "  ".join(
        f"{h[:w]:{a}{w}}" for h, w, a in zip(headers, widths, aligns)
    )
    print(f"  {dim(head.rstrip())}")
    print(f"  {dim('·' * len(head.rstrip()))}")
    for row in rows:
        line = "  ".join(
            f"{str(c)[:w]:{a}{w}}" for c, w, a in zip(row, widths, aligns)
        )
        print(f"  {line.rstrip()}")


# --------------------------------------------------------------------------
# The tour
# --------------------------------------------------------------------------


def main() -> None:
    banner(
        f"sbirgrantsearch · FY{YEAR}",
        "US innovation funding: who got paid, and what's being asked for next",
    )

    # -- 1. one call -------------------------------------------------------
    section(1, "One call gets the year",
            "The transport picks itself: JSON API, else filtered CSV, else bulk.")
    code(f"awards = gs.download(years={YEAR})")
    awards = gs.download(years=YEAR)
    total = sum(r.award_amount or 0 for r in awards)
    companies = {r.recipient_norm for r in awards}
    print()
    table(
        ["awards", "companies", "total funding", "median award", "transport"],
        [[
            f"{len(awards):,}",
            f"{len(companies):,}",
            money(total),
            money(sorted(r.award_amount or 0 for r in awards)[len(awards) // 2]),
            awards.transport,
        ]],
        [8, 10, 14, 13, 10],
    )

    # -- 2. money by agency ------------------------------------------------
    section(2, "Where the money goes")
    code("awards.by_agency()  # via record.agency_name")
    print()
    by_agency: dict[str, float] = {}
    for record in awards:
        by_agency[record.agency_name] = (
            by_agency.get(record.agency_name, 0) + (record.award_amount or 0)
        )
    bars(sorted(by_agency.items(), key=lambda kv: -kv[1])[:7], fmt=money)

    # -- 3. filters --------------------------------------------------------
    section(3, "Filters compose, and narrow without refetching")
    code('awards.filter_by(agency="NASA", state="CA")')
    print()
    queries = [
        ("NASA, California", dict(agency="NASA", state="CA")),
        ("NIH (sub-agency of HHS)", dict(branch="NIH")),
        ("DARPA", dict(branch="DARPA")),
        ("Phase II over $1M", dict(phase="Phase II", min_amount=1_000_000)),
        ('"quantum" anywhere', dict(contains="quantum")),
        ('"autonomous" anywhere', dict(contains="autonomous")),
    ]
    table(
        ["filter", "awards", "funding"],
        [
            [label, f"{len(hits):,}", money(sum(r.award_amount or 0 for r in hits))]
            for label, kwargs in queries
            for hits in [awards.filter_by(**kwargs)]
        ],
        [30, 8, 12], "<>>",
    )

    # -- 4. companies ------------------------------------------------------
    section(4, "Roll up to companies",
            "Keyed on UEI where present; the bulk export has none, search does.")
    code("awards.by_company()")
    with_uei = sum(1 for r in awards if r.uei)
    print(f"\n  {green('✓')} {with_uei:,}/{len(awards):,} records carry a UEI\n")
    top = list(awards.by_company().items())[:6]
    table(
        ["company", "uei", "awards", "total"],
        [
            [
                next((a.recipient for a in group), name)[:32],
                next((a.uei for a in group if a.uei), "—"),
                len(group),
                money(sum(a.award_amount or 0 for a in group)),
            ]
            for name, group in top
        ],
        [32, 13, 6, 10], "<<>>",
    )

    # -- 5. spinouts -------------------------------------------------------
    section(5, "Potential university spinouts",
            "STTR awards name their research partner: a company "
            "commercialising university work.")
    code('awards.filter_by(program="STTR", require_ri=True)')
    spinouts = awards.filter_by(program="STTR", require_ri=True)
    print(f"\n  {len(spinouts):,} of {len(awards):,} awards name a university\n")
    table(
        ["company", "state", "research partner"],
        [
            [r.recipient[:30], r.state or "—", (r.ri_name or "")[:30]]
            for r in spinouts[:6]
        ],
        [30, 5, 30],
    )

    # -- 6. the text -------------------------------------------------------
    section(6, "Abstracts arrive clean, ready to index")
    code("record.abstract_clean  # boilerplate and markup stripped")
    biggest = max(awards, key=lambda r: r.award_amount or 0)
    print()
    print(f"  {bold(biggest.title[:WIDTH - 4])}")
    print(f"  {dim(f'{biggest.recipient} · {biggest.agency_label} · '
                  f'{money(biggest.award_amount or 0)}')}")
    print()
    excerpt = textwrap.shorten(biggest.abstract_clean, 330, placeholder=" …")
    for line in textwrap.wrap(excerpt, WIDTH - 2):
        print(f"  {line}")

    # -- 7. topics ---------------------------------------------------------
    section(7, "What agencies want funded next",
            "Open solicitations — the forward-looking half of the picture.")
    code('gs.download_topics(status="open")')
    topics = gs.download_topics()
    print(f"\n  {topics.summary()}\n")
    table(
        ["closes", "agency", "topic"],
        [
            [t.close_date, t.agency, t.title]
            for t in topics.closing_soon(6)
        ],
        [10, 7, 48],
    )

    # -- 8. export ---------------------------------------------------------
    section(8, "Take it with you")
    code('awards.to_csv("awards.csv"); topics.to_csv("topics.csv")')
    big = awards.filter_by(min_amount=1_000_000)
    print()
    for label, path, count in (
        ("awards over $1M", big.to_csv("demo_awards.csv"), len(big)),
        ("open topics", topics.to_csv("demo_topics.csv"), len(topics)),
    ):
        print(f"  {green('→')} {label:<20} {str(path):<22} {count:>5,} rows")
    print(f"\n  {dim('also: .to_jsonl() · .to_dicts() · .to_pandas()')}")
    print()


if __name__ == "__main__":
    main()
