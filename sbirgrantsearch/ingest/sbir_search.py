"""SBIR.gov filtered-search adapter.

The award search at https://www.sbir.gov/awards exports the current result
set as CSV. It is a plain Drupal form POST, so it drives headlessly: fetch
the page for a ``form_build_id`` and session cookie, then POST the filters
with ``op=Download`` and read ``text/csv`` straight off the response.

Compared with the bulk export this is strictly richer -- it carries **UEI**
(the bulk file has none), plus country, topic code and research keywords --
and it moves agency, year, state, phase and program filtering server-side.

Two caveats shape the implementation:

* Results are capped near 10,000 rows. Past that the endpoint answers with
  a redirect to the HTML page instead of an error -- and it answers an
  *empty* query exactly the same way, with no distinguishing message. A
  non-CSV response is therefore ambiguous, and :meth:`_fetch` resolves it
  by halving the agency set: a truly oversized slice yields CSV once split,
  an empty one stays empty.
* This is an undocumented form endpoint, not a public API. It can change
  without notice, which is why the bulk CSV remains the guaranteed floor.

Verified 2026-08-15: NASA + FY2023 returned 455 rows with UEI on every one.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import time
import urllib.parse
import urllib.request
from collections.abc import Iterator, Sequence
from http.cookiejar import CookieJar

from ..clean import normalize_branch
from ..filters import RecordFilter
from ..models import Record
from .base import SourceAdapter
from .http import USER_AGENT, HttpError
from .sbir_common import build_record
from .sbir_csv import _raise_csv_field_limit

log = logging.getLogger(__name__)

SEARCH_URL = "https://www.sbir.gov/awards"

#: The endpoint stops returning CSV somewhere near this many rows.
RESULT_CAP = 10_000

#: Agency checkbox values accepted by the form, scraped from the live page.
#: Note the granularity: NIH, ARPA-E and ARPA-H are selectable directly,
#: where the bulk export only exposes them as free-text branch strings.
FORM_AGENCIES: tuple[str, ...] = (
    "USDA", "NIFA", "DOC", "NIST", "NOAA",
    "DOD", "USAF", "ARMY", "DARPA", "DHA", "DLA", "DMEA", "DTRA", "MDA",
    "NGA", "NAVY", "CBD", "OSD", "SDA", "SOCOM", "SCO",
    "ED", "IES", "OSERS",
    "DOE", "ARPA-E",
    "HHS", "ACL", "ARPA-H", "CDC", "FDA", "NIH",
    "DHS", "CWMD", "DNDO", "DNDO-SBIR", "DHS-S&T",
    "DOI", "DOT", "EPA", "NASA", "NSF", "NRC", "SBA",
)

#: Parent department -> its sub-agency checkboxes.
#:
#: A parent box already covers its children server-side: FY2023 returns
#: 3,177 rows for DOD and 1,393 for HHS, matching the bulk corpus totals.
#: Checking a parent *and* a child together does not union them -- it
#: narrows to the child (DOE alone returns 583 rows, DOE+ARPA-E returns
#: 36) -- so a filter must send the parent alone. These children are only
#: for splitting a parent that is too large to download in one request.
AGENCY_CHILDREN: dict[str, tuple[str, ...]] = {
    "USDA": ("NIFA",),
    "DOC": ("NIST", "NOAA"),
    "DOD": ("USAF", "ARMY", "DARPA", "DHA", "DLA", "DMEA", "DTRA",
            "MDA", "NGA", "NAVY", "CBD", "OSD", "SDA", "SOCOM", "SCO"),
    "ED": ("IES", "OSERS"),
    "DOE": ("ARPA-E",),
    "HHS": ("ACL", "ARPA-H", "CDC", "FDA", "NIH"),
    "DHS": ("CWMD", "DNDO", "DNDO-SBIR", "DHS-S&T"),
}

#: The department-level checkboxes. Together these cover every award, so
#: they are the split set for an unfiltered query -- 14 requests at worst,
#: not 44.
TOP_LEVEL_AGENCIES: tuple[str, ...] = (
    "USDA", "DOC", "DOD", "ED", "DOE", "HHS", "DHS",
    "DOI", "DOT", "EPA", "NASA", "NSF", "NRC", "SBA",
)

#: Canonical agency code -> the checkbox that covers it. One box, never a
#: parent plus its children.
AGENCY_TO_FORM: dict[str, tuple[str, ...]] = {
    code: (code,) for code in TOP_LEVEL_AGENCIES
}

# CSV header -> neutral field name. Deliberately separate from the bulk
# export's map: this endpoint names the same columns differently
# ("Company Name" vs "Company", "DUNs" vs "Duns").
COLUMN_MAP = {
    "Company Name": "company",
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
    "Topic Code": "topic_code",
    "Address 1": "address1",
    "Address 2": "address2",
    "City": "city",
    "State": "state",
    "ZIP": "zip",
    "Country": "country",
    "Company URL": "company_website",
    "Number Employees": "num_employees",
    "UEI": "uei",
    "DUNs": "duns",
    "RI Name": "ri_name",
    "Research Area Keywords": "research_keywords",
}

#: Every spelling that should resolve to a form checkbox: the acronym
#: itself, and the expanded name RecordFilter normalizes branches into.
_FORM_BY_NAME: dict[str, str] = {a.upper(): a for a in FORM_AGENCIES}
_FORM_BY_NAME.update({
    (normalize_branch(a) or a): a for a in FORM_AGENCIES
})

_PHASE_VALUES = {"PHASE I": "1", "PHASE II": "2", "I": "1", "II": "2",
                 "1": "1", "2": "2"}
_BUILD_ID_RE = re.compile(r'name="form_build_id"\s+value="([^"]+)"')


class SbirSearchUnavailable(RuntimeError):
    """The search form did not behave as expected."""


class SbirSearchAdapter(SourceAdapter):
    """Ingests SBIR/STTR awards from the SBIR.gov award-search CSV export."""

    name = "sbir_search"
    source = "sbir"

    def __init__(
        self,
        *,
        url: str = SEARCH_URL,
        timeout: int = 300,
        delay: float = 1.0,
        min_abstract_chars: int = 100,
        max_split_depth: int = 2,
    ) -> None:
        """
        Args:
            url: the award-search form URL.
            timeout: seconds to allow per download (large years are slow).
            delay: seconds between requests, to stay polite.
            min_abstract_chars: rows with shorter abstracts are dropped.
            max_split_depth: how many times a query may be halved. Any
                real cap resolves at the first split -- the largest whole
                year is 7,634 rows against a 10,000 limit -- so this
                mainly bounds the cost of confirming an empty slice.
        """
        self.url = url
        self.timeout = timeout
        self.delay = delay
        self.min_abstract_chars = min_abstract_chars
        self.max_split_depth = max_split_depth
        self._opener: urllib.request.OpenerDirector | None = None
        _raise_csv_field_limit()

    # -- availability ------------------------------------------------------

    def probe(self) -> bool:
        """Return True if the form is reachable and exposes a build id."""
        try:
            return bool(self._fresh_build_id())
        except (HttpError, OSError) as exc:
            log.info("SBIR.gov search probe failed: %s", exc)
            return False

    def prepare(self) -> None:
        if not self.probe():
            raise SbirSearchUnavailable(
                "The SBIR.gov award-search form did not return a usable "
                "form_build_id. Fall back to the bulk CSV export."
            )

    # -- SourceAdapter -----------------------------------------------------

    def fetch_year(
        self, year: int, record_filter: RecordFilter | None = None
    ) -> Iterator[dict]:
        """Yield rows for ``year``, splitting the query if it is too large.

        The endpoint answers a too-large query and an empty one identically
        -- a 303 back to the HTML page, with no distinguishing message --
        so the two are told apart structurally rather than from the
        response. See :meth:`_fetch` for how.
        """
        agencies = self._form_agencies(record_filter)
        yield from self._fetch(year, agencies, record_filter, depth=0)

    def _fetch(
        self,
        year: int,
        agencies: Sequence[str],
        record_filter: RecordFilter | None,
        *,
        depth: int,
    ) -> Iterator[dict]:
        """Fetch one slice, halving the agency set when the answer is unclear.

        A non-CSV response means either "no results" or "too many results".
        Splitting distinguishes them: if the slice was genuinely too large,
        the halves come back as CSV; if it was empty, the halves are empty
        too and the recursion bottoms out.

        The base case is safe because a single agency-year is far below the
        cap -- the largest in the corpus is DoD 2020 at 3,852 rows against
        a 10,000 limit -- so a single-agency slice that does not return CSV
        is empty, not truncated.
        """
        rows = self._download(year, agencies, record_filter)
        if rows is not None:
            yield from rows
            return

        candidates = list(agencies) if agencies else list(TOP_LEVEL_AGENCIES)
        # A single oversized department splits into its sub-agencies; the
        # parent box already covers them, so this partitions rather than
        # duplicates.
        if len(candidates) == 1 and (kids := AGENCY_CHILDREN.get(candidates[0])):
            log.debug("FY%d: splitting %s into %d sub-agencies",
                      year, candidates[0], len(kids))
            for child in kids:
                yield from self._fetch(year, [child], record_filter,
                                       depth=depth + 1)
            return
        if len(candidates) <= 1:
            log.debug(
                "FY%d agencies=%s returned no CSV at the narrowest slice; "
                "treating as empty", year, candidates or "all",
            )
            return

        if depth >= self.max_split_depth:
            # Exhausted the split budget without ever seeing CSV. Every
            # realistic over-cap slice resolves at the first split -- the
            # largest whole year in the corpus is 7,634 rows against a
            # 10,000 cap -- so reaching here means the slice is empty, not
            # oversized. Warn rather than raise: refusing to return an
            # empty result would break ordinary queries for quiet years.
            log.warning(
                "FY%d: %d agencies never returned CSV within "
                "max_split_depth=%d; treating the slice as empty. If this "
                "year should have awards, cross-check with transport='csv'.",
                year, len(candidates), self.max_split_depth,
            )
            return

        mid = len(candidates) // 2
        log.debug(
            "FY%d: splitting %d agencies into %d + %d",
            year, len(candidates), mid, len(candidates) - mid,
        )
        for half in (candidates[:mid], candidates[mid:]):
            yield from self._fetch(year, half, record_filter, depth=depth + 1)

    def normalize(self, raw: dict) -> Record | None:
        fields = {target: raw.get(column) for column, target in COLUMN_MAP.items()}
        return build_record(
            fields,
            source=self.source,
            raw=raw,
            min_abstract_chars=self.min_abstract_chars,
        )

    # -- form plumbing -----------------------------------------------------

    def _session(self) -> urllib.request.OpenerDirector:
        """An opener with a cookie jar; the form requires session state."""
        if self._opener is None:
            self._opener = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(CookieJar())
            )
            self._opener.addheaders = [("User-Agent", USER_AGENT)]
        return self._opener

    def _fresh_build_id(self) -> str:
        """GET the form for a build id, seeding the session cookie."""
        try:
            with self._session().open(self.url, timeout=60) as response:
                html = response.read().decode("utf-8", "replace")
        except OSError as exc:
            raise HttpError(f"Could not load {self.url}: {exc}") from exc

        match = _BUILD_ID_RE.search(html)
        if not match:
            raise SbirSearchUnavailable(
                f"No form_build_id at {self.url} -- the form has changed."
            )
        return match.group(1)

    def _form_agencies(self, record_filter: RecordFilter | None) -> list[str]:
        """Translate a filter's agency/branch constraints into form values.

        A branch constraint is preferred when present: asking the form for
        ``NIH`` is narrower, and cheaper, than asking for all of HHS.
        """
        if record_filter is None:
            return []

        values: list[str] = []
        for branch in record_filter.branches or ():
            # RecordFilter normalizes branches to the long form
            # ("NATIONAL INSTITUTES OF HEALTH"), while the form wants the
            # acronym, so both spellings are accepted here.
            hit = _FORM_BY_NAME.get(str(branch).upper())
            if hit:
                values.append(hit)
        if values:
            return sorted(set(values))

        if record_filter.agencies:
            for code in record_filter.agencies:
                values.extend(AGENCY_TO_FORM.get(code, (code,)))
            return sorted({v for v in values if v in FORM_AGENCIES})


        return []

    def _payload(
        self,
        year: int,
        agencies: Sequence[str],
        record_filter: RecordFilter | None,
    ) -> list[tuple[str, str]]:
        """Build the form fields for one download.

        Only constraints whose server-side meaning certainly matches ours
        are pushed down. Free-text search is deliberately not pushed: the
        form's keyword matching is not guaranteed to be a superset of our
        substring filter, and a pushdown that drops a matching record is
        worse than no pushdown at all.
        """
        fields: list[tuple[str, str]] = [
            ("form_id", "awards_search"),
            ("year[%d]" % year, str(year)),
        ]
        for agency in agencies:
            fields.append((f"agency[{agency}]", agency))

        if record_filter is not None:
            for state in sorted(record_filter.states or ()):
                fields.append((f"state[{state}]", state))
            phases = {
                _PHASE_VALUES[p] for p in (record_filter.phases or ())
                if p in _PHASE_VALUES
            }
            if len(phases) == 1:  # the form takes a single radio value
                fields.append(("phase", phases.pop()))
            programs = record_filter.programs or set()
            if len(programs) == 1:
                fields.append(("program", next(iter(programs))))

        fields.append(("op", "Download"))
        return fields

    def _download(
        self,
        year: int,
        agencies: Sequence[str],
        record_filter: RecordFilter | None,
    ) -> list[dict] | None:
        """POST one query. Returns rows, or ``None`` if the cap was hit."""
        build_id = self._fresh_build_id()
        fields = self._payload(year, agencies, record_filter)
        fields.append(("form_build_id", build_id))
        data = urllib.parse.urlencode(fields).encode("utf-8")

        request = urllib.request.Request(
            self.url,
            data=data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "text/csv,*/*",
                "Referer": self.url,
            },
        )

        try:
            with self._session().open(request, timeout=self.timeout) as response:
                content_type = response.headers.get("Content-Type", "")
                body = response.read()
        except OSError as exc:
            raise HttpError(f"Search download failed for FY{year}: {exc}") from exc

        if self.delay:
            time.sleep(self.delay)

        # Over the cap the endpoint redirects back to the HTML page rather
        # than erroring, so the content type is the only reliable signal.
        if "text/csv" not in content_type.lower():
            log.debug(
                "FY%d agencies=%s returned %s, not CSV -- treating as cap hit",
                year, list(agencies) or "all", content_type or "no content-type",
            )
            return None

        text = body.decode("utf-8-sig", "replace")
        rows = list(csv.DictReader(io.StringIO(text)))

        if rows and (missing := set(COLUMN_MAP) - set(rows[0])):
            log.warning(
                "search export is missing expected columns: %s",
                ", ".join(sorted(missing)),
            )
        if len(rows) >= RESULT_CAP:
            log.warning(
                "FY%d returned %d rows, at or above the %d cap -- results "
                "may be truncated", year, len(rows), RESULT_CAP,
            )
        return rows
