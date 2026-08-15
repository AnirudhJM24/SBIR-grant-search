"""SBIR/STTR awards with automatic transport fallback.

One source, three ways in. :class:`SbirSource` tries them in order of how
little they make you download:

1. ``api``    -- the JSON API. Down under maintenance as of 2026-08-15.
2. ``search`` -- the award-search form's filtered CSV export. Richer than
   the bulk file (it carries UEI, which the bulk file lacks), but capped
   near 10,000 rows per query.
3. ``csv``    -- the ~367 MB bulk export. Always works, always complete.

Both transports produce byte-identical :class:`~sbirgrantsearch.models.Record`
objects, including ``record_id``, so which one served a given run is an
operational detail rather than something callers have to handle.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Literal

from ..filters import RecordFilter
from ..models import Record
from .base import SourceAdapter
from .sbir_api import SbirApiAdapter, SbirApiUnavailable
from .sbir_csv import SbirCsvAdapter
from .sbir_search import SbirSearchAdapter, SbirSearchUnavailable

log = logging.getLogger(__name__)

Transport = Literal["auto", "api", "search", "csv"]


class SbirSource(SourceAdapter):
    """SBIR/STTR awards, served by whichever transport is working.

        >>> source = SbirSource()
        >>> source.prepare()
        >>> source.transport_used
        'csv'

    ``transport="auto"`` (the default) probes each in turn and falls back;
    naming one pins it and fails loudly if it is down, which is what you
    want in a reproducible pipeline.
    """

    name = "sbir"
    source = "sbir"

    def __init__(
        self,
        *,
        transport: Transport = "auto",
        cache_dir: Path | str = "data/cache",
        csv_path: Path | str | None = None,
        min_abstract_chars: int = 100,
        refresh: bool = False,
        api_kwargs: dict | None = None,
    ) -> None:
        """
        Args:
            transport: ``"auto"``, ``"api"`` or ``"csv"``.
            cache_dir: where the bulk CSV is cached.
            csv_path: use a local CSV instead of downloading.
            min_abstract_chars: rows with shorter abstracts are dropped.
            refresh: re-download the bulk CSV even if cached.
            api_kwargs: extra options for :class:`SbirApiAdapter`.
        """
        if transport not in ("auto", "api", "search", "csv"):
            raise ValueError(
                "transport must be 'auto', 'api', 'search' or 'csv', "
                f"got {transport!r}"
            )
        self.transport = transport
        self.min_abstract_chars = min_abstract_chars
        self._api = SbirApiAdapter(
            min_abstract_chars=min_abstract_chars, **(api_kwargs or {})
        )
        self._search = SbirSearchAdapter(min_abstract_chars=min_abstract_chars)
        self._csv = SbirCsvAdapter(
            cache_dir=cache_dir,
            csv_path=csv_path,
            min_abstract_chars=min_abstract_chars,
            refresh=refresh,
        )
        self._active: SourceAdapter | None = None

    # -- transport selection ----------------------------------------------

    @property
    def transport_used(self) -> str | None:
        """Which transport was selected, or ``None`` before :meth:`prepare`."""
        return None if self._active is None else self._active.name.removeprefix("sbir_")

    @property
    def active(self) -> SourceAdapter:
        """The selected adapter, preparing on first use."""
        if self._active is None:
            self.prepare()
        assert self._active is not None
        return self._active

    def prepare(self) -> None:
        """Select a transport and get it ready.

        In ``auto`` mode a failing API is expected, not exceptional: the
        bulk CSV is a complete substitute, so the fallback is logged at info
        level rather than raised.
        """
        if self._active is not None:
            return

        pinned = {
            "api": self._api,
            "search": self._search,
            "csv": self._csv,
        }.get(self.transport)
        if pinned is not None:
            pinned.prepare()  # raises if that transport is unavailable
            self._active = pinned
            return

        # auto: cheapest first, bulk download last.
        for adapter, note in (
            (self._api, "targeted JSON requests"),
            (self._search, "filtered CSV exports (includes UEI)"),
        ):
            try:
                adapter.prepare()
            except (SbirApiUnavailable, SbirSearchUnavailable, OSError) as exc:
                log.info(
                    "%s unavailable (%s); trying the next transport.",
                    adapter.name, exc.__class__.__name__,
                )
                continue
            log.info("using %s via %s.", note, adapter.name)
            self._active = adapter
            return

        log.info(
            "falling back to the bulk CSV export. Records are identical "
            "either way, though UEI is absent from the bulk file."
        )
        self._csv.prepare()
        self._active = self._csv

    # -- SourceAdapter -----------------------------------------------------

    def available_years(self) -> list[int] | None:
        return self.active.available_years()

    def fetch_year(
        self, year: int, record_filter: RecordFilter | None = None
    ) -> Iterator[dict]:
        return self.active.fetch_year(year, record_filter)

    def normalize(self, raw: dict) -> Record | None:
        return self.active.normalize(raw)
