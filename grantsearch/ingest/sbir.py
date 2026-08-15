"""SBIR/STTR awards with automatic transport fallback.

One source, two ways in. :class:`SbirSource` prefers the JSON API (targeted
requests, no big download) and falls back to the bulk CSV export when the
API is unavailable -- which, as of 2026-08-15, it always is.

Both transports produce byte-identical :class:`~grantsearch.models.Record`
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

log = logging.getLogger(__name__)

Transport = Literal["auto", "api", "csv"]


class SbirSource(SourceAdapter):
    """SBIR/STTR awards, served by whichever transport is working.

        >>> source = SbirSource()
        >>> source.prepare()
        >>> source.transport_used
        'csv'

    ``transport="auto"`` (the default) probes the API once and falls back;
    ``"api"`` and ``"csv"`` pin a transport and fail loudly if it is down,
    which is what you want in a reproducible pipeline.
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
        if transport not in ("auto", "api", "csv"):
            raise ValueError(
                f"transport must be 'auto', 'api' or 'csv', got {transport!r}"
            )
        self.transport = transport
        self.min_abstract_chars = min_abstract_chars
        self._api = SbirApiAdapter(
            min_abstract_chars=min_abstract_chars, **(api_kwargs or {})
        )
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

        if self.transport == "api":
            self._api.prepare()  # raises SbirApiUnavailable if down
            self._active = self._api
            return

        if self.transport == "csv":
            self._csv.prepare()
            self._active = self._csv
            return

        try:
            self._api.prepare()
        except (SbirApiUnavailable, OSError) as exc:
            log.info(
                "SBIR.gov API unavailable (%s); falling back to the bulk CSV "
                "export. Records are identical either way.",
                exc.__class__.__name__,
            )
            self._csv.prepare()
            self._active = self._csv
        else:
            log.info("SBIR.gov API is available; using targeted requests.")
            self._active = self._api

    # -- SourceAdapter -----------------------------------------------------

    def available_years(self) -> list[int] | None:
        return self.active.available_years()

    def fetch_year(
        self, year: int, record_filter: RecordFilter | None = None
    ) -> Iterator[dict]:
        return self.active.fetch_year(year, record_filter)

    def normalize(self, raw: dict) -> Record | None:
        return self.active.normalize(raw)
