"""SBIR.gov bulk-CSV adapter -- the working ingestion path.

SBIR.gov publishes every SBIR/STTR award, all agencies and all years, as a
single CSV with abstracts. This adapter downloads it once, caches it, and
streams it per year.

Verified 2026-08-15: 41 columns, HTTP 200, ~367 MB.
"""

from __future__ import annotations

import csv
import logging
import sys
from collections.abc import Iterator
from pathlib import Path

from ..clean import normalize_agency, parse_year
from ..filters import RecordFilter
from ..models import Record
from . import http
from .base import SourceAdapter
from .sbir_common import build_record

log = logging.getLogger(__name__)

AWARD_CSV_URL = "https://data.www.sbir.gov/awarddatapublic/award_data.csv"
AWARD_CSV_NO_ABSTRACT_URL = (
    "https://data.www.sbir.gov/mod_awarddatapublic_no_abstract/"
    "award_data_no_abstract.csv"
)

# CSV header -> neutral field name. Update here if SBIR.gov reshapes the export.
COLUMN_MAP = {
    "Company": "company",
    "Award Title": "title",
    "Abstract": "abstract",
    "Agency": "agency",
    "Branch": "branch",
    "Phase": "phase",
    "Program": "program",
    "Agency Tracking Number": "tracking_number",
    "Contract": "contract",
    "Proposal Award Date": "start_date",
    "Contract End Date": "end_date",
    "Award Year": "fiscal_year",
    "Award Amount": "award_amount",
    "Address1": "address1",
    "Address2": "address2",
    "City": "city",
    "State": "state",
    "Zip": "zip",
    "Company Website": "company_website",
    "Number Employees": "num_employees",
    "RI Name": "ri_name",
}


class SbirCsvAdapter(SourceAdapter):
    """Ingests SBIR/STTR awards from the SBIR.gov bulk CSV export."""

    name = "sbir_csv"
    source = "sbir"

    def __init__(
        self,
        cache_dir: Path | str = "data/cache",
        *,
        url: str = AWARD_CSV_URL,
        csv_path: Path | str | None = None,
        min_abstract_chars: int = 100,
        refresh: bool = False,
    ) -> None:
        """
        Args:
            cache_dir: where the downloaded CSV lives.
            url: bulk export URL; swap for the no-abstract export if wanted.
            csv_path: use a local file instead of downloading.
            min_abstract_chars: rows with shorter abstracts are dropped.
            refresh: re-download even if the cached file exists.
        """
        self.url = url
        self.cache_dir = Path(cache_dir)
        self.csv_path = Path(csv_path) if csv_path else self.cache_dir / "award_data.csv"
        self.min_abstract_chars = min_abstract_chars
        self.refresh = refresh
        self._explicit_path = csv_path is not None

        # Abstracts run past the default 128 KB field cap.
        _raise_csv_field_limit()

    # -- SourceAdapter -----------------------------------------------------

    def prepare(self) -> None:
        """Download the bulk CSV unless it is already cached."""
        if self.csv_path.exists() and not self.refresh:
            size_mb = self.csv_path.stat().st_size / 1e6
            log.info("using cached %s (%.0f MB)", self.csv_path, size_mb)
            return
        if self._explicit_path:
            raise FileNotFoundError(f"CSV not found: {self.csv_path}")
        http.download(self.url, self.csv_path)

    def available_years(self) -> list[int] | None:
        """Scan the cached file for the distinct award years present."""
        if not self.csv_path.exists():
            return None
        years = {
            year
            for row in self._rows()
            if (year := parse_year(row.get("Award Year"))) is not None
        }
        return sorted(years)

    def fetch_year(
        self, year: int, record_filter: RecordFilter | None = None
    ) -> Iterator[dict]:
        """Yield raw CSV rows whose ``Award Year`` matches ``year``.

        One pass over the file per year. That is a full rescan each time,
        but it is local, it keeps the interface identical to network-backed
        adapters, and it avoids holding the corpus in memory. Use
        :meth:`fetch_all` when you want a single pass over every year.
        """
        agencies = record_filter.agencies if record_filter else None
        for row in self._rows():
            if parse_year(row.get("Award Year")) != year:
                continue
            # Cheap pushdown: skip the agencies we were never asked for
            # before paying for normalization.
            if agencies and normalize_agency(row.get("Agency")) not in agencies:
                continue
            yield row

    def fetch_all(self) -> Iterator[dict]:
        """Yield every row, in file order, in one pass."""
        yield from self._rows()

    def normalize(self, raw: dict) -> Record | None:
        fields = {
            target: raw.get(column)
            for column, target in COLUMN_MAP.items()
        }
        return build_record(
            fields,
            source=self.source,
            raw=raw,
            min_abstract_chars=self.min_abstract_chars,
        )

    # -- internals ---------------------------------------------------------

    def _rows(self) -> Iterator[dict[str, str]]:
        """Stream the CSV as dicts, tolerating encoding damage."""
        if not self.csv_path.exists():
            raise FileNotFoundError(
                f"{self.csv_path} not found -- call prepare() first "
                f"or pass csv_path= to an existing file."
            )
        with self.csv_path.open(encoding="utf-8", errors="replace", newline="") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames:
                missing = set(COLUMN_MAP) - set(reader.fieldnames)
                if missing:
                    log.warning(
                        "SBIR.gov CSV is missing expected columns: %s "
                        "-- the export layout may have changed",
                        ", ".join(sorted(missing)),
                    )
            for row in reader:
                yield row


def _raise_csv_field_limit() -> None:
    """Raise csv's field-size cap; abstracts routinely exceed the default."""
    limit = sys.maxsize
    while True:
        try:
            csv.field_size_limit(limit)
            return
        except OverflowError:  # 32-bit C long on Windows
            limit //= 2
