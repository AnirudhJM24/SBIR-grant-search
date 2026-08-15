"""Data ingestion: source adapters and the shared ingest driver."""

from .base import (
    IngestResult,
    IngestStats,
    SourceAdapter,
    load_records,
    run_ingest,
    year_path,
)
from .sbir import SbirSource, Transport
from .sbir_api import SbirApiAdapter, SbirApiUnavailable
from .sbir_csv import SbirCsvAdapter
from .sbir_search import SbirSearchAdapter, SbirSearchUnavailable

#: Adapter registry -- the CLI and tests resolve sources by name through here.
ADAPTERS: dict[str, type[SourceAdapter]] = {
    SbirSource.name: SbirSource,
    SbirCsvAdapter.name: SbirCsvAdapter,
    SbirSearchAdapter.name: SbirSearchAdapter,
    SbirApiAdapter.name: SbirApiAdapter,
}


def get_adapter(name: str, **kwargs) -> SourceAdapter:
    """Instantiate a registered adapter by name."""
    try:
        cls = ADAPTERS[name]
    except KeyError:
        raise ValueError(
            f"Unknown source {name!r}. Available: {', '.join(sorted(ADAPTERS))}"
        ) from None
    return cls(**kwargs)


__all__ = [
    "ADAPTERS",
    "IngestResult",
    "IngestStats",
    "SbirApiAdapter",
    "SbirSource",
    "Transport",
    "SbirApiUnavailable",
    "SbirCsvAdapter",
    "SbirSearchAdapter",
    "SbirSearchUnavailable",
    "SourceAdapter",
    "get_adapter",
    "load_records",
    "run_ingest",
    "year_path",
]
