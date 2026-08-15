"""SBIR.gov JSON API adapter.

STATUS (verified 2026-08-15): the endpoint returns ``{"message": "Forbidden"}``
for every request -- SBIR.gov's public APIs are under maintenance following
their site rebuild. This adapter is written and tested against the documented
contract so it works the day the API returns; until then use
:class:`~grantsearch.ingest.sbir_csv.SbirCsvAdapter`, which reads the bulk
export and produces identical records.

Call :meth:`SbirApiAdapter.probe` to check whether the API is back.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator, Sequence
from typing import Any

from ..filters import RecordFilter
from ..models import Record
from . import http
from .base import SourceAdapter
from .sbir_common import build_record

log = logging.getLogger(__name__)

AWARDS_ENDPOINT = "https://api.www.sbir.gov/public/api/awards"

# Agencies the SBIR.gov API accepts. The API filters by agency, so a full
# year is fetched as one request series per agency.
API_AGENCIES = (
    "DOD", "HHS", "NASA", "DOE", "NSF", "USDA",
    "DHS", "DOC", "ED", "DOT", "EPA",
)

# API key -> neutral field name.
FIELD_MAP = {
    "firm": "company",
    "award_title": "title",
    "abstract": "abstract",
    "agency": "agency",
    "branch": "branch",
    "phase": "phase",
    "program": "program",
    "agency_tracking_number": "tracking_number",
    "contract": "contract",
    "proposal_award_date": "start_date",
    "contract_end_date": "end_date",
    "award_year": "fiscal_year",
    "award_amount": "award_amount",
    "address1": "address1",
    "address2": "address2",
    "city": "city",
    "state": "state",
    "zip": "zip",
    "company_url": "company_website",
    "number_employees": "num_employees",
    "ri_name": "ri_name",
    "award_link": "url",
}

# Alternate spellings seen across API versions; checked as fallbacks.
FIELD_ALIASES = {
    "company": ("firm_name", "company_name"),
    "title": ("title", "award_title_text"),
    "abstract": ("abstract_text", "award_abstract"),
    "start_date": ("award_date", "proposal_award_date"),
    "end_date": ("contract_end_date", "end_date"),
    "fiscal_year": ("fiscal_year", "year"),
    "company_website": ("company_website", "firm_url"),
    "num_employees": ("number_employees", "employee_count"),
    "url": ("award_link", "url"),
}


#: How long a probe result stays valid, in seconds. Availability changes on
#: the scale of a maintenance window, not a request.
PROBE_TTL = 300.0

#: endpoint -> (checked_at, is_up)
_PROBE_CACHE: dict[str, tuple[float, bool]] = {}


def reset_probe_cache() -> None:
    """Forget cached availability. Mostly useful in tests."""
    _PROBE_CACHE.clear()


class SbirApiUnavailable(RuntimeError):
    """Raised when the SBIR.gov API is unreachable or under maintenance."""


class SbirApiAdapter(SourceAdapter):
    """Ingests SBIR/STTR awards from the SBIR.gov public JSON API."""

    name = "sbir_api"
    source = "sbir"

    def __init__(
        self,
        *,
        agencies: Sequence[str] | None = None,
        rows_per_page: int = 100,
        max_pages: int = 500,
        delay: float = 0.5,
        min_abstract_chars: int = 100,
        endpoint: str = AWARDS_ENDPOINT,
    ) -> None:
        """
        Args:
            agencies: agencies to pull; ``None`` means all of them.
            rows_per_page: page size (the API caps this near 100).
            max_pages: safety stop, guards against a non-terminating cursor.
            delay: seconds between requests.
            min_abstract_chars: rows with shorter abstracts are dropped.
        """
        self.endpoint = endpoint
        self.agencies = tuple(agencies) if agencies else API_AGENCIES
        self.rows_per_page = rows_per_page
        self.max_pages = max_pages
        self.delay = delay
        self.min_abstract_chars = min_abstract_chars

    # -- availability ------------------------------------------------------

    def probe(self, *, force: bool = False) -> bool:
        """Return True if the API answers with usable data.

        The result is cached per endpoint for :data:`PROBE_TTL` seconds, so
        a script making several ``download()`` calls pays for one round trip
        rather than one per call. Pass ``force=True`` to re-check now.
        """
        now = time.monotonic()
        if not force and (cached := _PROBE_CACHE.get(self.endpoint)):
            checked_at, result = cached
            if now - checked_at < PROBE_TTL:
                return result

        try:
            payload = http.get_json(
                self.endpoint,
                params={"agency": "NASA", "year": 2023, "rows": 1, "start": 0},
                retries=0,
            )
            result = bool(self._extract_rows(payload))
        except (http.HttpError, SbirApiUnavailable) as exc:
            log.info("SBIR.gov API probe failed: %s", exc)
            result = False

        _PROBE_CACHE[self.endpoint] = (now, result)
        return result

    def prepare(self) -> None:
        if not self.probe():
            raise SbirApiUnavailable(
                "The SBIR.gov JSON API is not serving data (it returns "
                "'Forbidden' while under maintenance). Use SbirCsvAdapter "
                "against the bulk export instead -- it yields identical "
                "records. Re-run probe() later to check if it is back."
            )

    # -- SourceAdapter -----------------------------------------------------

    def fetch_year(
        self, year: int, record_filter: RecordFilter | None = None
    ) -> Iterator[dict]:
        """Yield every award for ``year``, one paged sweep per agency.

        The API filters by agency server-side, so an agency constraint is
        pushed down -- asking for NASA sweeps one agency instead of eleven.
        """
        for agency in self._agencies_for(record_filter):
            yield from self._fetch_agency_year(agency, year)

    def _agencies_for(self, record_filter: RecordFilter | None) -> tuple[str, ...]:
        """Narrow the agency sweep to what the filter actually asks for."""
        if not record_filter or not record_filter.agencies:
            return self.agencies
        wanted = {a for a in self.agencies if a in record_filter.agencies}
        # An agency the API does not serve means an empty sweep, not a full
        # one -- silently widening would return records nobody asked for.
        return tuple(a for a in self.agencies if a in wanted)

    def _fetch_agency_year(self, agency: str, year: int) -> Iterator[dict]:
        start = 0
        for page in range(self.max_pages):
            payload = http.get_json(
                self.endpoint,
                params={
                    "agency": agency,
                    "year": year,
                    "start": start,
                    "rows": self.rows_per_page,
                    "format": "json",
                },
            )
            rows = self._extract_rows(payload)
            if not rows:
                return

            yield from rows

            # A short page means the last page; the API exposes no total.
            if len(rows) < self.rows_per_page:
                return
            start += len(rows)
            if self.delay:
                time.sleep(self.delay)
        else:
            log.warning(
                "%s %s: stopped at max_pages=%d -- results may be truncated",
                agency, year, self.max_pages,
            )

    def normalize(self, raw: dict) -> Record | None:
        fields: dict[str, Any] = {}
        for api_key, target in FIELD_MAP.items():
            value = raw.get(api_key)
            if value not in (None, ""):
                fields[target] = value
        # Fill anything still missing from known alternate spellings.
        for target, aliases in FIELD_ALIASES.items():
            if fields.get(target) in (None, ""):
                for alias in aliases:
                    if raw.get(alias) not in (None, ""):
                        fields[target] = raw[alias]
                        break
        return build_record(
            fields,
            source=self.source,
            raw=raw,
            min_abstract_chars=self.min_abstract_chars,
        )

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _extract_rows(payload: Any) -> list[dict]:
        """Pull the award list out of the response.

        The API has returned both a bare list and an envelope across
        versions, so both shapes are accepted.
        """
        if isinstance(payload, list):
            return [r for r in payload if isinstance(r, dict)]
        if isinstance(payload, dict):
            if "message" in payload and len(payload) == 1:
                raise SbirApiUnavailable(f"API returned: {payload['message']}")
            for key in ("results", "data", "awards", "response", "docs"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [r for r in value if isinstance(r, dict)]
        return []
