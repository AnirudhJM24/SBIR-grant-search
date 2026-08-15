"""Solicitation topics from https://www.sbir.gov/topics.

Topics are the other half of the picture: awards are what *was* funded,
topics are what an agency is asking for now. Same mechanism as the award
search -- a Drupal form POST whose Download button returns CSV -- but a
different entity, so it gets its own schema rather than being forced into
:class:`~sbirgrantsearch.models.Record`.

    import sbirgrantsearch as gs

    open_topics = gs.download_topics(status="open")
    ai = open_topics.filter_by(contains="machine learning")

Verified 2026-08-15: 337 open topics across NSF, DoD and HHS.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import time
import urllib.parse
import urllib.request
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import asdict, dataclass, field, fields as dataclass_fields
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any

from .clean import (
    clean_abstract,
    clean_text,
    normalize_agency,
    parse_date,
    parse_year,
)
from .ingest.http import USER_AGENT, HttpError
from .ingest.sbir_search import FORM_AGENCIES, _FORM_BY_NAME, AGENCY_TO_FORM

log = logging.getLogger(__name__)

TOPICS_URL = "https://www.sbir.gov/topics"

# CSV header -> Topic field.
COLUMN_MAP = {
    "Topic Title": "title",
    "Topic Description": "description",
    "Topic Number": "topic_number",
    "Phase": "phase",
    "Program": "program",
    "Agency": "agency",
    "Branch": "branch",
    "Open Date": "open_date",
    "Close Date": "close_date",
    "Release Date": "release_date",
    "Solicitation Agency URL": "solicitation_url",
    "Solicitation Status": "status",
    "Solicitation Year": "solicitation_year",
    "SBIRTopicLink": "url",
}

_BUILD_ID_RE = re.compile(r'name="form_build_id"\s+value="([^"]+)"')
_PHASE_VALUES = {"PHASE I": "1", "PHASE II": "2", "I": "1", "II": "2",
                 "1": "1", "2": "2"}


class SbirTopicsUnavailable(RuntimeError):
    """The topics form did not behave as expected."""


@dataclass(frozen=True, slots=True)
class Topic:
    """One solicitation topic.

    Deliberately not a :class:`~sbirgrantsearch.models.Record`: a topic is
    an open call with no recipient and no award amount, so squeezing it
    into the award schema would leave half the fields meaningless.
    """

    topic_id: str
    source: str = "sbir_topics"
    url: str | None = None

    title: str = ""
    description: str = ""
    description_clean: str = ""
    topic_number: str | None = None

    agency: str = ""
    branch: str | None = None
    program: str | None = None
    phase: str | None = None

    status: str | None = None
    open_date: str | None = None
    close_date: str | None = None
    release_date: str | None = None
    solicitation_year: int | None = None
    solicitation_url: str | None = None

    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def search_text(self) -> str:
        """Concatenated field used to build retrieval indexes."""
        return "\n".join(p for p in (self.title, self.description_clean) if p)

    @property
    def agency_name(self) -> str:
        from .clean import agency_display

        return agency_display(self.agency)

    @property
    def is_open(self) -> bool:
        return (self.status or "").strip().lower() == "open"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Topic:
        known = {f.name for f in dataclass_fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass(slots=True)
class TopicResults:
    """Result of :func:`download_topics`; behaves like a sequence."""

    topics: list[Topic]
    filters: dict[str, Any] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.topics)

    def __iter__(self) -> Iterator[Topic]:
        return iter(self.topics)

    def __getitem__(self, index):
        return self.topics[index]

    def __bool__(self) -> bool:
        return bool(self.topics)

    def filter_by(
        self,
        *,
        agency: str | Iterable[str] | None = None,
        program: str | None = None,
        phase: str | None = None,
        status: str | None = None,
        contains: str | None = None,
        closes_after: str | None = None,
        closes_before: str | None = None,
    ) -> TopicResults:
        """Narrow an already-downloaded result without refetching."""
        agencies = (
            {normalize_agency(a) for a in _as_iter(agency)} if agency else None
        )
        needle = contains.casefold() if contains else None
        after, before = parse_date(closes_after), parse_date(closes_before)

        kept = []
        for t in self.topics:
            if agencies and normalize_agency(t.agency) not in agencies:
                continue
            if program and (t.program or "").upper() != program.upper():
                continue
            if phase and (t.phase or "").upper() != phase.upper():
                continue
            if status and (t.status or "").lower() != status.lower():
                continue
            if needle and needle not in t.search_text.casefold():
                continue
            # A topic with no close date fails a close-date window rather
            # than passing it, matching how RecordFilter treats dates.
            if after is not None and (t.close_date or "") < after:
                continue
            if before is not None and (not t.close_date or t.close_date > before):
                continue
            kept.append(t)
        return TopicResults(kept, {**self.filters, "refined": True})

    def open_only(self) -> TopicResults:
        return TopicResults([t for t in self.topics if t.is_open], self.filters)

    def to_dicts(self, *, include_raw: bool = False) -> list[dict[str, Any]]:
        rows = [t.to_dict() for t in self.topics]
        if not include_raw:
            for row in rows:
                row.pop("raw", None)
        return rows

    def to_jsonl(self, path: Path | str) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n") as fh:
            for topic in self.topics:
                fh.write(topic.to_json() + "\n")
        return path

    def to_csv(self, path: Path | str) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        columns = [f.name for f in dataclass_fields(Topic) if f.name != "raw"]
        with path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for topic in self.topics:
                writer.writerow({c: topic.to_dict().get(c) for c in columns})
        return path

    def to_pandas(self):
        try:
            import pandas as pd
        except ImportError as exc:  # pragma: no cover
            raise ImportError("to_pandas() needs pandas: pip install pandas") from exc
        return pd.DataFrame(self.to_dicts())

    def by_agency(self) -> dict[str, list[Topic]]:
        groups: dict[str, list[Topic]] = {}
        for topic in self.topics:
            groups.setdefault(topic.agency or "?", []).append(topic)
        return dict(sorted(groups.items(), key=lambda kv: len(kv[1]), reverse=True))

    def closing_soon(self, limit: int = 10) -> list[Topic]:
        """Open topics with the nearest close dates first."""
        dated = [t for t in self.topics if t.close_date and t.is_open]
        return sorted(dated, key=lambda t: t.close_date or "")[:limit]

    def summary(self) -> str:
        agencies = len({t.agency for t in self.topics})
        open_count = sum(1 for t in self.topics if t.is_open)
        return (
            f"{len(self.topics):,} topics | {open_count:,} open | "
            f"{agencies} agencies"
        )


def _as_iter(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        return [value]
    return value


class SbirTopicsClient:
    """Drives the SBIR.gov topics search form."""

    def __init__(
        self,
        *,
        url: str = TOPICS_URL,
        timeout: int = 180,
        delay: float = 1.0,
        min_description_chars: int = 0,
    ) -> None:
        self.url = url
        self.timeout = timeout
        self.delay = delay
        self.min_description_chars = min_description_chars
        self._opener: urllib.request.OpenerDirector | None = None

    # -- transport ---------------------------------------------------------

    def _session(self) -> urllib.request.OpenerDirector:
        if self._opener is None:
            self._opener = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(CookieJar())
            )
            self._opener.addheaders = [("User-Agent", USER_AGENT)]
        return self._opener

    def _fresh_build_id(self) -> str:
        try:
            with self._session().open(self.url, timeout=60) as response:
                html = response.read().decode("utf-8", "replace")
        except OSError as exc:
            raise HttpError(f"Could not load {self.url}: {exc}") from exc
        match = _BUILD_ID_RE.search(html)
        if not match:
            raise SbirTopicsUnavailable(
                f"No form_build_id at {self.url} -- the form has changed."
            )
        return match.group(1)

    def probe(self) -> bool:
        try:
            return bool(self._fresh_build_id())
        except (HttpError, OSError, SbirTopicsUnavailable) as exc:
            log.info("SBIR.gov topics probe failed: %s", exc)
            return False

    # -- querying ----------------------------------------------------------

    def _payload(
        self,
        *,
        agencies: Sequence[str] = (),
        years: Sequence[int] = (),
        status: str | None = None,
        program: str | None = None,
        phase: str | None = None,
        keywords: str | None = None,
        close_date_from: str | None = None,
        close_date_to: str | None = None,
        open_date_from: str | None = None,
        open_date_to: str | None = None,
    ) -> list[tuple[str, str]]:
        fields: list[tuple[str, str]] = [("form_id", "topics_search")]
        for agency in agencies:
            fields.append((f"agency[{agency}]", agency))
        for year in years:
            fields.append((f"year[{year}]", str(year)))
        if status:
            fields.append(("status", status.capitalize()))
        if program:
            fields.append(("program", program.upper()))
        if phase and (value := _PHASE_VALUES.get(phase.upper())):
            fields.append(("phase", value))
        # Unlike the award form, keyword search here is the documented way
        # to narrow topics, and the result is re-filtered client-side
        # anyway, so pushing it down is safe.
        if keywords:
            fields.append(("keywords", keywords))
        for name, value in (
            ("close_date_from", close_date_from),
            ("close_date_to", close_date_to),
            ("open_date_from", open_date_from),
            ("open_date_to", open_date_to),
        ):
            if value and (iso := parse_date(value)):
                fields.append((name, iso))
        fields.append(("op", "Download"))
        return fields

    def download_csv(self, **query: Any) -> list[dict] | None:
        """Run one query. Returns raw rows, or ``None`` if nothing matched.

        Like the award form, an empty result set comes back as a redirect
        to the HTML page rather than an empty CSV, so the content type is
        the only reliable signal.
        """
        fields = self._payload(**query)
        fields.append(("form_build_id", self._fresh_build_id()))
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
            raise HttpError(f"Topics download failed: {exc}") from exc

        if self.delay:
            time.sleep(self.delay)

        if "text/csv" not in content_type.lower():
            log.debug("topics query returned %s, not CSV -- no matches",
                      content_type or "no content-type")
            return None

        rows = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig", "replace"))))
        if rows and (missing := set(COLUMN_MAP) - set(rows[0])):
            log.warning("topics export is missing expected columns: %s",
                        ", ".join(sorted(missing)))
        return rows

    def normalize(self, raw: dict) -> Topic | None:
        fields = {target: raw.get(column) for column, target in COLUMN_MAP.items()}

        title = clean_text(fields.get("title")) or ""
        description = clean_text(fields.get("description")) or ""
        description_clean = clean_abstract(
            description, min_chars=self.min_description_chars
        )
        if not title and not description_clean:
            return None

        topic_number = clean_text(fields.get("topic_number"))
        agency = normalize_agency(fields.get("agency")) or ""
        # Topic numbers are unique per agency and stable across the
        # solicitation's life, so they make a better id than the title.
        key = topic_number or f"{title[:60]}|{fields.get('open_date')}"

        return Topic(
            topic_id=f"sbir_topics:{agency}:{key}" if agency else f"sbir_topics:{key}",
            url=clean_text(fields.get("url")),
            title=title,
            description=description,
            description_clean=description_clean,
            topic_number=topic_number,
            agency=agency,
            branch=clean_text(fields.get("branch")),
            program=(clean_text(fields.get("program")) or "").upper() or None,
            phase=clean_text(fields.get("phase")),
            status=clean_text(fields.get("status")),
            open_date=parse_date(fields.get("open_date")),
            close_date=parse_date(fields.get("close_date")),
            release_date=parse_date(fields.get("release_date")),
            solicitation_year=parse_year(fields.get("solicitation_year")),
            solicitation_url=clean_text(fields.get("solicitation_url")),
            raw=raw,
        )


def _form_agencies(agency: Any) -> list[str]:
    """Translate agency/branch names into topic-form checkbox values."""
    if not agency:
        return []
    values: list[str] = []
    for name in _as_iter(agency):
        text = str(name).strip()
        key = text.upper()
        # One checkbox per request: a parent box already covers its
        # sub-agencies, and checking both narrows to the child instead of
        # unioning them.
        if direct := _FORM_BY_NAME.get(key):
            values.append(direct)
            continue
        # A full department name resolves to its canonical checkbox.
        values.extend(AGENCY_TO_FORM.get(normalize_agency(text) or "", ()))
    return sorted({v for v in values if v in FORM_AGENCIES})


def download_topics(
    *,
    agency: str | Iterable[str] | None = None,
    years: int | Iterable[int] | None = None,
    status: str | None = None,
    program: str | None = None,
    phase: str | None = None,
    keywords: str | None = None,
    contains: str | None = None,
    closes_after: str | None = None,
    closes_before: str | None = None,
    client: SbirTopicsClient | None = None,
) -> TopicResults:
    """Download solicitation topics matching the given filters.

    Args:
        agency: agency or sub-agency, e.g. ``"NSF"``, ``"NIH"``, ``"DARPA"``.
        years: solicitation year(s).
        status: ``"open"`` or ``"closed"``.
        program: ``"SBIR"`` or ``"STTR"``.
        phase: ``"Phase I"`` or ``"Phase II"``.
        keywords: full-text query, pushed to the form.
        contains: substring filter applied locally after download.
        closes_after / closes_before: close-date window.

    Returns:
        :class:`TopicResults` -- a sequence of :class:`Topic`.

    Note:
        The form defaults to open topics. Pass ``status="closed"`` for the
        archive, which is much larger.

    Example::

        gs.download_topics(agency="NSF", keywords="quantum")
    """
    client = client or SbirTopicsClient()
    year_list = (
        [years] if isinstance(years, int)
        else sorted({int(y) for y in years}) if years
        else []
    )

    rows = client.download_csv(
        agencies=_form_agencies(agency),
        years=year_list,
        status=status,
        program=program,
        phase=phase,
        keywords=keywords,
        close_date_from=closes_after,
        close_date_to=closes_before,
    )

    topics: list[Topic] = []
    seen: set[str] = set()
    for raw in rows or []:
        topic = client.normalize(raw)
        if topic is None or topic.topic_id in seen:
            continue
        seen.add(topic.topic_id)
        topics.append(topic)

    results = TopicResults(
        topics,
        {
            "agency": agency, "years": year_list, "status": status,
            "program": program, "phase": phase, "keywords": keywords,
        },
    )
    # Local narrowing the form cannot express.
    if contains:
        results = results.filter_by(contains=contains)
    return results
