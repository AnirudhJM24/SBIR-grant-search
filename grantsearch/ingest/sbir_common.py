"""Shared normalization for SBIR.gov sources.

The bulk CSV and the JSON API expose the same awards under different key
spellings (``"Award Title"`` vs ``award_title``). Both adapters map their
rows into the neutral dict below, then call :func:`build_record` so the two
paths can never drift apart.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Any

from ..clean import (
    clean_abstract,
    clean_text,
    join_address,
    normalize_agency,
    normalize_company,
    normalize_state,
    normalize_zip,
    parse_amount,
    parse_date,
    parse_int,
    parse_year,
)
from ..models import Record

# Rows shorter than this are boilerplate, not retrievable text.
MIN_ABSTRACT_CHARS = 100


def first(row: dict[str, Any], *keys: str) -> Any:
    """Return the first present, non-empty value among ``keys``."""
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def make_record_id(
    source: str,
    *,
    agency: str | None,
    strong_keys: Sequence[Any],
    fallback_keys: Sequence[Any],
) -> str:
    """Build a globally unique, stable record id.

    SBIR.gov has no single reliable primary key: contract numbers repeat
    across agencies and are occasionally blank. Use the first available
    strong key (contract, then agency tracking number), namespaced by
    agency; if a row has neither, hash the identity fields instead.

    The id deliberately excludes the title, so it survives the source
    rewording a project name between exports.
    """
    for key in strong_keys:
        text = str(key).strip() if key not in (None, "") else ""
        if text:
            prefix = f"{source}:{agency}" if agency else source
            return f"{prefix}:{text}"

    digest = hashlib.sha1(
        "|".join(str(k).strip().casefold() for k in fallback_keys).encode("utf-8")
    ).hexdigest()[:16]
    return f"{source}:h:{digest}"


def build_record(
    fields: dict[str, Any],
    *,
    source: str,
    raw: dict[str, Any],
    min_abstract_chars: int = MIN_ABSTRACT_CHARS,
    drop_without_abstract: bool = True,
) -> Record | None:
    """Normalize a neutral SBIR field dict into a :class:`Record`.

    Returns ``None`` when the row has no usable abstract, since a project
    with no text cannot be retrieved and only distorts IDF weights.
    """
    abstract_raw = clean_text(fields.get("abstract")) or ""
    abstract_clean = clean_abstract(abstract_raw, min_chars=min_abstract_chars)
    if drop_without_abstract and not abstract_clean:
        return None

    title = clean_text(fields.get("title")) or ""
    recipient = clean_text(fields.get("company")) or ""
    if not title and not abstract_clean:
        return None

    fiscal_year = parse_year(fields.get("fiscal_year"))
    start_date = parse_date(fields.get("start_date"))
    end_date = parse_date(fields.get("end_date"))

    # Award year is missing often enough to be worth backfilling.
    if fiscal_year is None and start_date:
        fiscal_year = int(start_date[:4])

    # Guard against transposed dates in the source data.
    if start_date and end_date and end_date < start_date:
        start_date, end_date = end_date, start_date

    agency = normalize_agency(fields.get("agency")) or ""
    record_id = make_record_id(
        source,
        agency=agency or None,
        strong_keys=(fields.get("contract"), fields.get("tracking_number")),
        fallback_keys=(recipient, title, fiscal_year, fields.get("phase")),
    )

    return Record(
        record_id=record_id,
        source=source,
        url=clean_text(fields.get("url")),
        recipient=recipient,
        recipient_norm=normalize_company(recipient),
        recipient_type="company",  # every SBIR/STTR awardee is a small business
        title=title,
        abstract=abstract_raw,
        abstract_clean=abstract_clean,
        agency=agency,
        branch=clean_text(fields.get("branch")),
        program=(clean_text(fields.get("program")) or "").upper() or None,
        phase=clean_text(fields.get("phase")),
        award_amount=parse_amount(fields.get("award_amount")),
        fiscal_year=fiscal_year,
        start_date=start_date,
        end_date=end_date,
        address=join_address(fields.get("address1"), fields.get("address2")),
        city=clean_text(fields.get("city")),
        state=normalize_state(fields.get("state")),
        zip_code=normalize_zip(fields.get("zip")),
        ri_name=clean_text(fields.get("ri_name")),
        company_website=clean_text(fields.get("company_website")),
        num_employees=parse_int(fields.get("num_employees")),
        raw=raw,
    )
