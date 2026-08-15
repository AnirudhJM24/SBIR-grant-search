"""Common record schema shared by every ingestion source."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from typing import Any

# Values allowed in Record.recipient_type.
RECIPIENT_TYPES = frozenset(
    {"company", "university", "nonprofit", "government", "hospital", "other"}
)


@dataclass(frozen=True, slots=True)
class Record:
    """One funded project, normalized across sources.

    ``abstract`` keeps the source text as-is; ``abstract_clean`` is the
    search-facing version (boilerplate stripped, whitespace collapsed).
    Retrieval indexes ``search_text``; display uses ``abstract``.
    """

    # Identity
    record_id: str
    source: str
    url: str | None = None

    # Recipient
    recipient: str = ""
    recipient_norm: str = ""
    recipient_type: str = "company"

    # Content
    title: str = ""
    abstract: str = ""
    abstract_clean: str = ""

    # Funding program
    agency: str = ""
    branch: str | None = None
    program: str | None = None
    phase: str | None = None
    award_amount: float | None = None

    # Time
    fiscal_year: int | None = None
    start_date: str | None = None  # ISO YYYY-MM-DD
    end_date: str | None = None  # ISO YYYY-MM-DD

    # Geography
    address: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None

    # Extras that feed later stages
    ri_name: str | None = None  # STTR research institution -> spinout signal
    company_website: str | None = None
    num_employees: int | None = None

    # Original payload, kept so a missed field never means a refetch.
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def search_text(self) -> str:
        """Concatenated field used to build retrieval indexes."""
        parts = [self.title, self.abstract_clean, self.recipient]
        return "\n".join(p for p in parts if p)

    @property
    def agency_name(self) -> str:
        """Human-readable agency, e.g. ``"Department of Energy"``.

        Derived rather than stored: ``agency`` keeps the short code, which
        is the stable filter key, so renaming an agency for display never
        requires re-ingesting the corpus.
        """
        from .clean import agency_display

        return agency_display(self.agency)

    @property
    def agency_label(self) -> str:
        """Agency with sub-agency when it adds information.

        ``"Health and Human Services - National Institutes of Health"``.
        """
        from .clean import agency_label

        return agency_label(self.agency, self.branch)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Record:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})
