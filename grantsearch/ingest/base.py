"""Source adapter interface and the shared ingestion driver.

Adding a source means writing one :class:`SourceAdapter` subclass. The
driver, the on-disk layout, the filtering and the resume logic are shared.
"""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from ..filters import RecordFilter
from ..models import Record

log = logging.getLogger(__name__)


class SourceAdapter(ABC):
    """Fetches and normalizes one data source.

    Implementations own their transport (HTTP, bulk file, local dump) and
    their field mapping, and nothing else.
    """

    #: Transport slug -- identifies *how* records were obtained. Used in
    #: filenames and the adapter registry.
    name: str = "base"

    #: Logical source slug -- identifies *what* the records are, and is what
    #: lands in ``Record.source``. Two transports serving the same dataset
    #: share one ``source``, so a record keeps the same id whichever path
    #: produced it. That is what makes transport fallback transparent.
    source: str = "base"

    def prepare(self) -> None:
        """Optional one-time setup (download a bulk file, warm a session)."""

    def available_years(self) -> list[int] | None:
        """Years this source can serve, or ``None`` if unknown up front."""
        return None

    @abstractmethod
    def fetch_year(
        self, year: int, record_filter: RecordFilter | None = None
    ) -> Iterator[dict]:
        """Yield raw payloads for one year.

        Implementations handle their own pagination, retries and rate
        limiting, and must stream rather than accumulate.

        ``record_filter`` is a *hint*: an implementation may use it to avoid
        fetching rows that will be discarded anyway (pushdown), but the
        driver re-applies the filter regardless, so ignoring it is always
        correct and never changes the result.
        """

    @abstractmethod
    def normalize(self, raw: dict) -> Record | None:
        """Map one raw payload to a :class:`Record`.

        Return ``None`` to drop the row (unusable, malformed, no abstract).
        Must not raise on bad data.
        """

    # -- shared -----------------------------------------------------------

    def records_for_year(
        self, year: int, record_filter: RecordFilter | None = None
    ) -> Iterator[Record]:
        """Fetch and normalize one year, skipping rows that fail to parse."""
        for raw in self.fetch_year(year, record_filter):
            try:
                record = self.normalize(raw)
            except Exception:  # a single bad row must not kill the run
                log.exception("%s: normalize failed, skipping row", self.name)
                continue
            if record is not None:
                yield record


@dataclass(slots=True)
class IngestStats:
    """Per-year counters, printed as the run's summary."""

    source: str
    year: int
    fetched: int = 0
    written: int = 0
    dropped_normalize: int = 0
    dropped_filter: int = 0
    dropped_duplicate: int = 0
    seconds: float = 0.0
    skipped: bool = False

    def __str__(self) -> str:
        if self.skipped:
            return f"{self.source} {self.year}: skipped (already present)"
        return (
            f"{self.source} {self.year}: {self.written} written "
            f"from {self.fetched} fetched "
            f"(-{self.dropped_normalize} unusable, "
            f"-{self.dropped_filter} filtered, "
            f"-{self.dropped_duplicate} dupes) in {self.seconds:.1f}s"
        )


@dataclass(slots=True)
class IngestResult:
    """Aggregate result of a whole run."""

    stats: list[IngestStats] = field(default_factory=list)

    @property
    def written(self) -> int:
        return sum(s.written for s in self.stats)

    @property
    def fetched(self) -> int:
        return sum(s.fetched for s in self.stats)

    def summary(self) -> str:
        lines = [str(s) for s in self.stats]
        lines.append(f"TOTAL: {self.written} records written from {self.fetched} fetched")
        return "\n".join(lines)


def year_path(out_dir: Path, source: str, year: int) -> Path:
    return Path(out_dir) / f"{source}_{year}.jsonl"


def run_ingest(
    adapter: SourceAdapter,
    years: Iterable[int],
    out_dir: Path | str = "data/raw",
    *,
    record_filter: RecordFilter | None = None,
    overwrite: bool = False,
    dedupe: bool = True,
    limit: int | None = None,
) -> IngestResult:
    """Ingest ``years`` from ``adapter`` into one JSONL file per year.

    One file per source-year is what makes a run resumable: an interrupted
    run is restarted by re-invoking it, and completed years are skipped
    unless ``overwrite`` is set. Each year is written to a ``.tmp`` file and
    renamed on success, so a partial file is never mistaken for a done one.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    adapter.prepare()

    result = IngestResult()
    seen: set[str] = set()

    for year in years:
        final = year_path(out_dir, adapter.name, year)
        if final.exists() and not overwrite:
            log.info("%s: %s exists, skipping", adapter.name, final.name)
            result.stats.append(IngestStats(adapter.name, year, skipped=True))
            continue

        stats = IngestStats(adapter.name, year)
        started = time.monotonic()
        tmp = final.with_suffix(".jsonl.tmp")

        try:
            with tmp.open("w", encoding="utf-8", newline="\n") as fh:
                for raw in adapter.fetch_year(year, record_filter):
                    stats.fetched += 1
                    try:
                        record = adapter.normalize(raw)
                    except Exception:
                        log.exception("%s: normalize failed", adapter.name)
                        record = None
                    if record is None:
                        stats.dropped_normalize += 1
                        continue
                    if record_filter is not None and not record_filter.matches(record):
                        stats.dropped_filter += 1
                        continue
                    if dedupe:
                        if record.record_id in seen:
                            stats.dropped_duplicate += 1
                            continue
                        seen.add(record.record_id)

                    fh.write(record.to_json() + "\n")
                    stats.written += 1
                    if limit is not None and stats.written >= limit:
                        break
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

        tmp.replace(final)
        stats.seconds = time.monotonic() - started
        log.info("%s", stats)
        result.stats.append(stats)

    return result


def load_records(
    path: Path | str,
    *,
    record_filter: RecordFilter | None = None,
) -> Iterator[Record]:
    """Stream records from one JSONL file or a directory of them."""
    path = Path(path)
    files = sorted(path.glob("*.jsonl")) if path.is_dir() else [path]
    for file in files:
        with file.open(encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = Record.from_dict(json.loads(line))
                except (json.JSONDecodeError, TypeError):
                    log.warning("%s:%d: bad JSON line, skipping", file.name, line_no)
                    continue
                if record_filter is None or record_filter.matches(record):
                    yield record
