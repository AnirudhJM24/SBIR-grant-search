"""Public Innovation Search Engine.

A Python library for downloading US government innovation-funding records
into one common schema, filtered the way you ask for them.

    import grantsearch as gs

    awards = gs.download(agency="NASA", state="CA", years=2023)
    print(awards.summary())

The transport is chosen for you: the JSON API when it is up, the bulk CSV
export when it is not. Records are identical either way.
"""

from .api import Download, download, make_filter, stream
from .clean import AGENCY_NAMES, BRANCH_NAMES, agency_display, agency_label
from .filters import POTENTIAL_SPINOUTS, RETRIEVABLE, STARTUPS_ONLY, RecordFilter, any_of
from .ingest import load_records
from .models import Record

__version__ = "0.0.1"

__all__ = [
    "AGENCY_NAMES",
    "BRANCH_NAMES",
    "POTENTIAL_SPINOUTS",
    "RETRIEVABLE",
    "STARTUPS_ONLY",
    "Download",
    "Record",
    "RecordFilter",
    "agency_display",
    "agency_label",
    "any_of",
    "download",
    "load_records",
    "make_filter",
    "stream",
]
