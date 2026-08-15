"""Composable record filters.

``RecordFilter`` is a declarative, immutable predicate over :class:`Record`.
Build one from CLI flags, from a config dict, or in code; combine two with
``&``; extend it with arbitrary callables via ``predicates``. The same
object filters an ingest stream today and a search result set later.

    >>> f = RecordFilter(agencies={"NASA"}, states={"CA"}, min_amount=500_000)
    >>> f = f & RecordFilter(fiscal_year_min=2020)
    >>> hits = list(f.apply(records))
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from .clean import (
    normalize_agency,
    normalize_branch,
    normalize_state,
    parse_amount,
    parse_date,
)
from .models import Record

Predicate = Callable[[Record], bool]


def _norm_set(values: Iterable[str] | None, fn: Callable[[object], Any] | None = None):
    """Normalize a set-valued filter option; ``None`` means 'no constraint'."""
    if values is None:
        return None
    out = set()
    for v in values:
        if v is None:
            continue
        normalized = fn(v) if fn else str(v).strip().upper()
        if normalized:
            out.add(normalized)
    return out or None


def _upper(value: object) -> str:
    return str(value).strip().upper()


@dataclass(frozen=True, slots=True)
class RecordFilter:
    """Declarative filter over records. All constraints are AND-ed.

    Every field defaults to ``None`` meaning "unconstrained", so an empty
    ``RecordFilter()`` matches everything.
    """

    # Categorical (case-insensitive; agencies/states are normalized)
    agencies: frozenset[str] | set[str] | None = None
    branches: frozenset[str] | set[str] | None = None
    programs: frozenset[str] | set[str] | None = None  # SBIR / STTR
    phases: frozenset[str] | set[str] | None = None  # PHASE I / PHASE II
    states: frozenset[str] | set[str] | None = None
    sources: frozenset[str] | set[str] | None = None
    recipient_types: frozenset[str] | set[str] | None = None

    # Fiscal year
    fiscal_years: frozenset[int] | set[int] | None = None
    fiscal_year_min: int | None = None
    fiscal_year_max: int | None = None

    # Project dates (ISO strings; comparison is lexicographic and correct)
    start_after: str | None = None
    start_before: str | None = None
    end_after: str | None = None
    end_before: str | None = None

    # Award amount
    min_amount: float | None = None
    max_amount: float | None = None

    # Quality gates
    require_abstract: bool = False
    min_abstract_chars: int = 0
    require_ri: bool = False  # STTR research-institution partner present

    # Free-form substring match over title + abstract + company
    text_contains: str | None = None

    # Escape hatch for anything not expressible above.
    predicates: tuple[Predicate, ...] = field(default=())

    def __post_init__(self) -> None:
        # Normalize option values once, at construction, so ``matches`` stays
        # a cheap set lookup on the hot path.
        object.__setattr__(self, "agencies", _norm_set(self.agencies, normalize_agency))
        object.__setattr__(self, "branches", _norm_set(self.branches, normalize_branch))
        object.__setattr__(self, "programs", _norm_set(self.programs))
        object.__setattr__(self, "phases", _norm_set(self.phases))
        object.__setattr__(self, "states", _norm_set(self.states, normalize_state))
        object.__setattr__(self, "sources", _norm_set(self.sources))
        object.__setattr__(
            self, "recipient_types", _norm_set(self.recipient_types)
        )
        if self.fiscal_years is not None:
            object.__setattr__(self, "fiscal_years", {int(y) for y in self.fiscal_years})
        for name in ("start_after", "start_before", "end_after", "end_before"):
            if (value := getattr(self, name)) is not None:
                object.__setattr__(self, name, parse_date(value))
        for name in ("min_amount", "max_amount"):
            if (value := getattr(self, name)) is not None:
                object.__setattr__(self, name, parse_amount(value))
        if self.text_contains:
            object.__setattr__(self, "text_contains", self.text_contains.casefold())

    # -- evaluation --------------------------------------------------------

    def matches(self, record: Record) -> bool:
        """True if ``record`` satisfies every constraint."""
        if self.sources and record.source.upper() not in self.sources:
            return False
        if self.agencies and normalize_agency(record.agency) not in self.agencies:
            return False
        if self.branches and normalize_branch(record.branch) not in self.branches:
            return False
        if self.programs and _upper(record.program or "") not in self.programs:
            return False
        if self.phases and _upper(record.phase or "") not in self.phases:
            return False
        if self.states and normalize_state(record.state) not in self.states:
            return False
        if self.recipient_types and _upper(record.recipient_type) not in self.recipient_types:
            return False

        # Missing values fail a constraint rather than passing it: an
        # undated record is not evidence that it falls in the window.
        if self.fiscal_years is not None or self.fiscal_year_min is not None \
                or self.fiscal_year_max is not None:
            year = record.fiscal_year
            if year is None:
                return False
            if self.fiscal_years is not None and year not in self.fiscal_years:
                return False
            if self.fiscal_year_min is not None and year < self.fiscal_year_min:
                return False
            if self.fiscal_year_max is not None and year > self.fiscal_year_max:
                return False

        if not _in_window(record.start_date, self.start_after, self.start_before):
            return False
        if not _in_window(record.end_date, self.end_after, self.end_before):
            return False

        if self.min_amount is not None or self.max_amount is not None:
            amount = record.award_amount
            if amount is None:
                return False
            if self.min_amount is not None and amount < self.min_amount:
                return False
            if self.max_amount is not None and amount > self.max_amount:
                return False

        if self.require_abstract and not record.abstract_clean:
            return False
        if self.min_abstract_chars and len(record.abstract_clean) < self.min_abstract_chars:
            return False
        if self.require_ri and not record.ri_name:
            return False

        if self.text_contains and self.text_contains not in record.search_text.casefold():
            return False

        return all(p(record) for p in self.predicates)

    __call__ = matches

    def apply(self, records: Iterable[Record]) -> Iterator[Record]:
        """Lazily filter an iterable of records."""
        return (r for r in records if self.matches(r))

    def describe(self) -> str:
        """Render the active constraints, e.g. ``"agency=NASA, state=NY"``.

        Used to explain an empty result, where knowing what was asked for
        matters more than knowing that nothing came back.
        """
        parts: list[str] = []
        for name, label in (
            ("sources", "source"), ("agencies", "agency"),
            ("branches", "branch"), ("programs", "program"),
            ("phases", "phase"), ("states", "state"),
            ("recipient_types", "recipient_type"), ("fiscal_years", "year"),
        ):
            if values := getattr(self, name):
                parts.append(f"{label}={'/'.join(sorted(str(v) for v in values))}")
        for name, label in (
            ("fiscal_year_min", "year>="), ("fiscal_year_max", "year<="),
            ("start_after", "start>="), ("start_before", "start<="),
            ("end_after", "end>="), ("end_before", "end<="),
            ("min_amount", "amount>="), ("max_amount", "amount<="),
            ("text_contains", "contains"),
        ):
            if (value := getattr(self, name)) is not None:
                parts.append(f"{label}{'=' if label[-1].isalpha() else ''}{value}")
        if self.require_ri:
            parts.append("require_ri")
        if self.min_abstract_chars:
            parts.append(f"min_abstract_chars={self.min_abstract_chars}")
        if self.predicates:
            parts.append(f"+{len(self.predicates)} custom predicate(s)")
        return ", ".join(parts) or "no filters"

    # -- composition -------------------------------------------------------

    def where(self, *predicates: Predicate) -> RecordFilter:
        """Return a copy with extra callable predicates attached."""
        return replace(self, predicates=self.predicates + tuple(predicates))

    def replace(self, **changes: Any) -> RecordFilter:
        """Return a copy with fields overridden."""
        return replace(self, **changes)

    def __and__(self, other: RecordFilter) -> RecordFilter:
        """Combine two filters. Both must match.

        Implemented by composition rather than by merging fields, so the
        semantics are obvious and no constraint is silently widened.
        """
        if not isinstance(other, RecordFilter):
            return NotImplemented
        return RecordFilter(predicates=(self.matches, other.matches))

    @classmethod
    def from_dict(cls, options: dict[str, Any]) -> RecordFilter:
        """Build a filter from a config/CLI dict, ignoring unknown keys."""
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in options.items() if k in known and v is not None})


def _in_window(value: str | None, after: str | None, before: str | None) -> bool:
    """Check an ISO date against an optional [after, before] window."""
    if after is None and before is None:
        return True
    if value is None:
        return False
    if after is not None and value < after:
        return False
    if before is not None and value > before:
        return False
    return True


# Convenience filters for the common private-markets questions.
STARTUPS_ONLY = RecordFilter(recipient_types={"company"}, programs={"SBIR", "STTR"})
POTENTIAL_SPINOUTS = RecordFilter(programs={"STTR"}, require_ri=True)
RETRIEVABLE = RecordFilter(require_abstract=True, min_abstract_chars=100)


def any_of(*filters: RecordFilter) -> RecordFilter:
    """OR-combine filters: a record matching any one of them passes."""
    matchers: Sequence[Predicate] = [f.matches for f in filters]
    return RecordFilter(predicates=(lambda r: any(m(r) for m in matchers),))
