"""The library's front door.

    import sbirgrantsearch as gs

    awards = gs.download(agency="NASA", state="CA", years=2023)
    print(len(awards), "via", awards.transport)
    awards.to_jsonl("nasa_ca.jsonl")

One call states *what* you want; the library works out how to get it,
falling back from the JSON API to the bulk CSV export without changing the
records you get back.
"""

from __future__ import annotations

import csv as _csv
import json
import logging
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field, fields as dataclass_fields
from pathlib import Path
from typing import Any

from .filters import RecordFilter
from .ingest import load_records
from .ingest.base import IngestResult, run_ingest
from .ingest.sbir import SbirSource, Transport
from .models import Record

log = logging.getLogger(__name__)

DEFAULT_YEARS = range(2015, 2026)

# download() kwarg -> RecordFilter field. Singular and plural both accepted,
# since `agency="NASA"` and `agencies=["NASA","DOE"]` are equally natural.
_FILTER_ALIASES = {
    "agency": "agencies",
    "branch": "branches",
    "program": "programs",
    "phase": "phases",
    "state": "states",
    "source": "sources",
    "recipient_type": "recipient_types",
    "fiscal_year": "fiscal_years",
    "year_min": "fiscal_year_min",
    "year_max": "fiscal_year_max",
    "contains": "text_contains",
}
_SET_FIELDS = {
    "agencies", "branches", "programs", "phases", "states", "sources",
    "recipient_types", "fiscal_years",
}


def _as_set(value: Any) -> set | None:
    """Accept a scalar, a sequence or a set for set-valued options."""
    if value is None:
        return None
    if isinstance(value, (str, bytes, int)):
        return {value}
    return set(value)


def make_filter(**options: Any) -> RecordFilter:
    """Build a :class:`RecordFilter` from friendly keyword arguments.

    Accepts singular or plural names, and scalars where a set is expected::

        make_filter(agency="NASA", state=["CA", "MA"], min_amount=500_000)

    Raises on unknown keywords rather than ignoring them -- a silently
    dropped filter would return too many records, which is the failure mode
    hardest to notice.
    """
    known = {f.name for f in dataclass_fields(RecordFilter)}
    resolved: dict[str, Any] = {}

    for key, value in options.items():
        if value is None:
            continue
        name = _FILTER_ALIASES.get(key, key)
        if name not in known:
            suggestions = sorted(known | set(_FILTER_ALIASES))
            raise TypeError(
                f"Unknown filter {key!r}. Available: {', '.join(suggestions)}"
            )
        resolved[name] = _as_set(value) if name in _SET_FIELDS else value

    return RecordFilter(**resolved)


def normalize_years(years: Any) -> list[int]:
    """Coerce a year spec into a sorted list of years.

    Accepts an int, a range, an iterable of ints, a ``"2015-2025"`` string,
    or ``None`` for the default range.
    """
    if years is None:
        return list(DEFAULT_YEARS)
    if isinstance(years, int):
        return [years]
    if isinstance(years, str):
        from .cli import parse_years

        return parse_years(years)
    out = sorted({int(y) for y in years})
    if not out:
        raise ValueError("years is empty -- nothing to download")
    return out


@dataclass(slots=True)
class Download:
    """The result of a :func:`download` call.

    Behaves like a sequence of :class:`~sbirgrantsearch.models.Record`, and
    carries the provenance needed to know how the data arrived.
    """

    records: list[Record]
    transport: str
    years: list[int]
    filter: RecordFilter
    stats: IngestResult | None = field(default=None, repr=False)
    out_dir: Path | None = None

    def __len__(self) -> int:
        return len(self.records)

    def __iter__(self) -> Iterator[Record]:
        return iter(self.records)

    def __getitem__(self, index):
        # Still an IndexError, because that is what indexing an empty
        # sequence means -- returning None here would hide the fact that a
        # query matched nothing until something downstream broke oddly.
        # Only the message improves: an empty result is usually a filter
        # that is too narrow, so it says which filters were in play.
        if not self.records and isinstance(index, int):
            raise IndexError(
                f"no records matched, so index {index} is out of range. "
                f"Query: {self.query}. Check the filters, or widen the "
                f"year range."
            )
        return self.records[index]

    def __bool__(self) -> bool:
        return bool(self.records)

    @property
    def first(self) -> Record | None:
        """The first record, or ``None`` when nothing matched.

        The safe way to peek at a result you have not checked::

            if (r := d.first):
                print(r.title)
        """
        return self.records[0] if self.records else None

    @property
    def query(self) -> str:
        """One-line description of what was asked for."""
        years = (
            f"years={min(self.years)}-{max(self.years)}"
            if len(self.years) > 1 else
            f"years={self.years[0]}" if self.years else "years=none"
        )
        return f"{self.filter.describe()}, {years}, via {self.transport}"

    def filter_by(self, **options: Any) -> Download:
        """Narrow an already-downloaded result without refetching."""
        narrowed = make_filter(**options)
        return Download(
            records=[r for r in self.records if narrowed.matches(r)],
            transport=self.transport,
            years=self.years,
            filter=self.filter & narrowed,
            out_dir=self.out_dir,
        )

    def to_dicts(self, *, include_raw: bool = False) -> list[dict[str, Any]]:
        """Records as plain dicts, for pandas or JSON."""
        rows = [r.to_dict() for r in self.records]
        if not include_raw:
            for row in rows:
                row.pop("raw", None)
        return rows

    def to_jsonl(self, path: Path | str) -> Path:
        """Write one JSON object per line."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n") as fh:
            for record in self.records:
                fh.write(record.to_json() + "\n")
        return path

    def to_csv(self, path: Path | str, *, columns: Sequence[str] | None = None) -> Path:
        """Write a flat CSV in the common schema.

        ``raw`` is excluded by default -- it is a nested dict that does not
        belong in a flat file, and it is what makes JSONL the better archive
        format.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        columns = list(columns) if columns else [
            f.name for f in dataclass_fields(Record) if f.name != "raw"
        ]
        with path.open("w", encoding="utf-8", newline="") as fh:
            writer = _csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for record in self.records:
                row = record.to_dict()
                writer.writerow({
                    c: json.dumps(row.get(c)) if isinstance(row.get(c), (dict, list))
                    else row.get(c)
                    for c in columns
                })
        return path

    def to_pandas(self):
        """Return a ``pandas.DataFrame``. Requires pandas to be installed."""
        try:
            import pandas as pd
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise ImportError(
                "to_pandas() needs pandas: pip install pandas"
            ) from exc
        return pd.DataFrame(self.to_dicts())

    def by_company(self) -> dict[str, list[Record]]:
        """Group records by normalized company name.

        Keyed on ``recipient_norm`` because the bulk export carries no UEI,
        so the cleaned name is the only join key available.
        """
        groups: dict[str, list[Record]] = {}
        for record in self.records:
            groups.setdefault(record.recipient_norm or record.recipient, []).append(record)
        return dict(
            sorted(groups.items(), key=lambda kv: len(kv[1]), reverse=True)
        )

    def summary(self) -> str:
        if not self.records:
            return f"No awards matched. Query: {self.query}"
        total = sum(r.award_amount or 0 for r in self.records)
        companies = len({r.recipient_norm for r in self.records})
        span = f"{min(self.years)}-{max(self.years)}" if self.years else "-"
        return (
            f"{len(self.records):,} awards | {companies:,} companies | "
            f"${total:,.0f} | {span} | via {self.transport}"
        )


def download(
    *,
    years: Any = None,
    transport: Transport = "auto",
    out_dir: Path | str | None = None,
    cache_dir: Path | str = "data/cache",
    csv_path: Path | str | None = None,
    refresh: bool = False,
    limit: int | None = None,
    overwrite: bool = False,
    **filters: Any,
) -> Download:
    """Download SBIR/STTR awards matching ``filters``.

    Args:
        years: int, range, iterable, or ``"2015-2025"``. Defaults to 2015-2025.
        transport: ``"auto"`` tries the JSON API, then the filtered search
            export, then the bulk CSV; ``"api"``, ``"search"`` or ``"csv"``
            pins one and fails if it is unavailable.
        out_dir: also persist to one JSONL file per year here. Existing
            years are reused rather than refetched unless ``overwrite``.
        cache_dir: where the bulk CSV is cached between runs.
        csv_path: read a local CSV instead of downloading one.
        refresh: re-download the bulk CSV even if cached.
        limit: stop after this many records per year.
        **filters: any :func:`make_filter` keyword -- ``agency``, ``state``,
            ``program``, ``phase``, ``branch``, ``min_amount``,
            ``max_amount``, ``start_after``, ``contains``, ``require_ri``...

    Returns:
        A :class:`Download`: a sequence of records plus the transport used.

    Example::

        gs.download(agency="DOE", branch="ARPA-E", min_amount=1_000_000)
    """
    record_filter = make_filter(**filters)
    year_list = normalize_years(years)

    source = SbirSource(
        transport=transport,
        cache_dir=cache_dir,
        csv_path=csv_path,
        refresh=refresh,
    )
    source.prepare()

    if out_dir is not None:
        # Persisted mode: write per-year files, then read them back. Costs
        # one extra pass but makes the run resumable and reproducible.
        out_dir = Path(out_dir)
        stats = run_ingest(
            source,
            years=year_list,
            out_dir=out_dir,
            record_filter=record_filter,
            overwrite=overwrite,
            limit=limit,
        )
        records = list(load_records(out_dir, record_filter=record_filter))
        return Download(
            records=records,
            transport=source.transport_used or transport,
            years=year_list,
            filter=record_filter,
            stats=stats,
            out_dir=out_dir,
        )

    records = list(
        _stream(source, year_list, record_filter, limit=limit)
    )
    return Download(
        records=records,
        transport=source.transport_used or transport,
        years=year_list,
        filter=record_filter,
    )


def stream(
    *,
    years: Any = None,
    transport: Transport = "auto",
    cache_dir: Path | str = "data/cache",
    csv_path: Path | str | None = None,
    limit: int | None = None,
    **filters: Any,
) -> Iterator[Record]:
    """Like :func:`download`, but yields records lazily.

    Use this when the result set is large enough that holding it in memory
    is wasteful -- counting, aggregating, or writing straight to a store.
    """
    record_filter = make_filter(**filters)
    source = SbirSource(transport=transport, cache_dir=cache_dir, csv_path=csv_path)
    source.prepare()
    yield from _stream(source, normalize_years(years), record_filter, limit=limit)


def _stream(
    source: SbirSource,
    years: Iterable[int],
    record_filter: RecordFilter,
    *,
    limit: int | None = None,
) -> Iterator[Record]:
    """Shared record generator, deduped across years."""
    seen: set[str] = set()
    for year in years:
        count = 0
        for record in source.records_for_year(year, record_filter):
            if not record_filter.matches(record):
                continue
            if record.record_id in seen:
                continue
            seen.add(record.record_id)
            yield record
            count += 1
            if limit is not None and count >= limit:
                break
