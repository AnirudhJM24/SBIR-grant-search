"""Field-level parsers and normalizers.

Every function here is total: it takes whatever the source produced
(including ``None``, empty strings and junk) and returns either a clean
value or ``None``. Nothing raises. Ingestion of 100k rows must not die on
one malformed cell.
"""

from __future__ import annotations

import html
import re
import unicodedata
from datetime import date, datetime

# --------------------------------------------------------------------------
# Amounts
# --------------------------------------------------------------------------

_AMOUNT_STRIP = re.compile(r"[,$\s]")
_AMOUNT_PARENS = re.compile(r"^\((.*)\)$")


def parse_amount(value: object) -> float | None:
    """Parse a money field into a float.

    Handles ``"171,433"``, ``"$1,000.50"``, ``"(500)"`` (accounting negative),
    numerics, and returns ``None`` for blanks, ``"N/A"`` and unparseable text.
    Zero is preserved as ``0.0`` -- it is a real value, not a missing one.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)

    text = str(value).strip()
    if not text or text.lower() in {"n/a", "na", "none", "null", "-", "--"}:
        return None

    negative = False
    if m := _AMOUNT_PARENS.match(text):
        negative, text = True, m.group(1)
    if text.startswith("-"):
        negative, text = True, text[1:]

    text = _AMOUNT_STRIP.sub("", text)
    if not text:
        return None

    try:
        amount = float(text)
    except ValueError:
        return None
    return -amount if negative else amount


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------

_DATE_FORMATS = (
    "%m/%d/%Y",  # SBIR.gov CSV
    "%Y-%m-%d",  # ISO / NIH RePORTER
    "%Y-%m-%dT%H:%M:%S",
    "%Y/%m/%d",
    "%d-%b-%Y",  # 07-May-2024
    "%b %d, %Y",  # May 7, 2024
    "%B %d, %Y",  # September 23, 2026 -- the topics export spells it out
    "%d %B %Y",
    "%d %b %Y",
    "%m/%d/%y",
    "%Y%m%d",
)

_MIN_YEAR, _MAX_YEAR = 1960, date.today().year + 15


def parse_date(value: object) -> str | None:
    """Parse a date field into an ISO ``YYYY-MM-DD`` string.

    Accepts the formats federal sources actually emit, tolerates trailing
    timestamps and timezone suffixes, and rejects dates outside a sane
    range (bad sentinels like ``01/01/1900`` become ``None``).
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()

    text = str(value).strip()
    if not text or text.lower() in {"n/a", "na", "none", "null", "-"}:
        return None

    # Drop timezone/fractional-second tails: 2024-05-07T00:00:00.000Z
    # Only when a time component is present -- otherwise the offset pattern
    # would eat the year from "07-May-2024".
    if "T" in text or ":" in text:
        text = re.sub(r"[Zz]$|\.\d+$|[+-]\d{2}:?\d{2}$", "", text).strip()

    parsed: date | None = None
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt).date()
            break
        except ValueError:
            continue

    if parsed is None:
        # Last resort: leading ISO date inside a longer string.
        if m := re.match(r"(\d{4})-(\d{2})-(\d{2})", text):
            try:
                parsed = date(int(m[1]), int(m[2]), int(m[3]))
            except ValueError:
                return None
        else:
            return None

    if not (_MIN_YEAR <= parsed.year <= _MAX_YEAR):
        return None
    return parsed.isoformat()


def parse_year(value: object) -> int | None:
    """Parse a fiscal/award year field into an int."""
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return value if _MIN_YEAR <= value <= _MAX_YEAR else None

    text = str(value).strip()
    if not text:
        return None
    if m := re.search(r"(19|20)\d{2}", text):
        year = int(m.group(0))
        return year if _MIN_YEAR <= year <= _MAX_YEAR else None
    return None


def parse_int(value: object) -> int | None:
    """Parse an integer field (employee counts and similar)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = _AMOUNT_STRIP.sub("", str(value).strip())
    try:
        return int(float(text))
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Company names
# --------------------------------------------------------------------------

# Ordered longest-first so "L L C" does not shadow "LLC".
_LEGAL_SUFFIXES = (
    "incorporated", "corporation", "limited liability company", "company",
    "limited", "l l c", "l.l.c.", "llc", "inc", "corp", "ltd", "co",
    "lp", "llp", "plc", "pllc", "pc", "pbc", "gmbh", "sa", "nv", "bv",
    "the",
)
_SUFFIX_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(s) for s in _LEGAL_SUFFIXES) + r")\b",
    re.IGNORECASE,
)
_PUNCT_RE = re.compile(r"[^\w\s&]")
_WS_RE = re.compile(r"\s+")


def normalize_company(value: object) -> str:
    """Collapse a company name to a comparison key.

    ``"AGILE DATA DECISIONS, INC."`` -> ``"agile data decisions"``.
    This is the join key for company-level aggregation, since the SBIR.gov
    bulk export carries no UEI. It is deliberately lossy; keep the original
    in ``Record.recipient`` for display.
    """
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.casefold()
    text = _PUNCT_RE.sub(" ", text)
    text = _SUFFIX_RE.sub(" ", text)
    text = text.replace("&", " and ")
    text = _WS_RE.sub(" ", text).strip()
    return text


# --------------------------------------------------------------------------
# Abstracts
# --------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")
# Boilerplate headers federal abstracts open with, stripped for NL search.
_BOILERPLATE_RE = re.compile(
    r"^\s*(?:"
    r"abstract|summary|project\s+summary(?:/abstract)?|"
    r"description\s*\(provided\s+by\s+applicant\)|"
    r"description|technical\s+abstract|"
    r"benefit|anticipated\s+benefits?|identification\s+and\s+significance"
    r")\s*[:\-–]\s*",
    re.IGNORECASE,
)
_NULLISH = {"", "n/a", "na", "none", "null", "no abstract available", "tbd"}
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_abstract(value: object, *, min_chars: int = 0) -> str:
    """Normalize abstract text for natural-language search.

    Unescapes entities, strips HTML, removes leading section headers,
    normalizes unicode punctuation and collapses whitespace. Returns ``""``
    for nullish text or text shorter than ``min_chars`` -- callers treat an
    empty result as "not retrievable" and drop the record.
    """
    if value is None:
        return ""

    text = str(value)
    text = html.unescape(html.unescape(text))  # double-escaped entities appear
    text = _TAG_RE.sub(" ", text)
    text = _CTRL_RE.sub(" ", text)
    text = unicodedata.normalize("NFKC", text)
    text = (
        text.replace("‘", "'").replace("’", "'")
        .replace("“", '"').replace("”", '"')
        .replace("–", "-").replace("—", "-")
        .replace(" ", " ")
    )

    # Headers can stack: "PROJECT SUMMARY: ABSTRACT: ..."
    for _ in range(3):
        new = _BOILERPLATE_RE.sub("", text, count=1)
        if new == text:
            break
        text = new

    text = _WS_RE.sub(" ", text).strip()

    if text.lower() in _NULLISH or len(text) < min_chars:
        return ""
    return text


# --------------------------------------------------------------------------
# Geography
# --------------------------------------------------------------------------

_STATE_ABBR = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT",
    "delaware": "DE", "district of columbia": "DC", "florida": "FL",
    "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL",
    "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY",
    "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT",
    "nebraska": "NE", "nevada": "NV", "new hampshire": "NH",
    "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH",
    "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "puerto rico": "PR", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}


def normalize_state(value: object) -> str | None:
    """Return a two-letter USPS state code, or ``None``."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) == 2 and text.isalpha():
        return text.upper()
    return _STATE_ABBR.get(text.casefold())


def normalize_zip(value: object) -> str | None:
    """Return a 5-digit ZIP, discarding the +4 extension."""
    if value is None:
        return None
    digits = re.sub(r"\D", "", str(value))
    if len(digits) < 5:
        return None
    return digits[:5]


def join_address(*parts: object) -> str | None:
    """Join address lines, dropping blanks."""
    cleaned = [str(p).strip() for p in parts if p and str(p).strip()]
    return ", ".join(cleaned) or None


def normalize_identifier(value: object) -> str | None:
    """Normalize a UEI or DUNS: upper-cased, stripped, blanks to None.

    Sources emit placeholder junk here more often than real blanks, so
    obvious sentinels are discarded rather than stored as if meaningful.
    """
    if value is None:
        return None
    text = re.sub(r"[^A-Za-z0-9]", "", str(value)).upper()
    if not text or set(text) <= {"0"} or text in {"NA", "NULL", "NONE", "TBD"}:
        return None
    return text


def clean_text(value: object) -> str | None:
    """Trim a short free-text field; blanks become ``None``."""
    if value is None:
        return None
    text = _WS_RE.sub(" ", str(value)).strip()
    return text or None


# --------------------------------------------------------------------------
# Agencies
# --------------------------------------------------------------------------

# Canonical short codes. Sources spell agencies inconsistently ("NASA" vs
# "National Aeronautics and Space Administration"), so filters normalize
# both sides through here.
_AGENCY_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"aeronautics|nasa", "NASA"),
    (r"health and human services|hhs|national institutes of health|\bnih\b", "HHS"),
    (r"national science foundation|\bnsf\b", "NSF"),
    (r"\benergy\b|\bdoe\b|arpa-?e", "DOE"),
    (r"agriculture|usda|nifa", "USDA"),
    (r"defense|\bdod\b|\bdarpa\b|army|navy|air force", "DOD"),
    (r"homeland security|\bdhs\b", "DHS"),
    (r"\bcommerce\b|\bnoaa\b|\bnist\b", "DOC"),
    (r"transportation|\bdot\b", "DOT"),
    (r"environmental protection|\bepa\b", "EPA"),
    (r"\beducation\b", "ED"),
)
_AGENCY_COMPILED = tuple((re.compile(p, re.IGNORECASE), c) for p, c in _AGENCY_PATTERNS)


#: Canonical code -> human-readable name. The code is what gets stored and
#: filtered on (short, stable, unambiguous); the name is what gets shown.
AGENCY_NAMES: dict[str, str] = {
    "NASA": "NASA",
    "HHS": "Health and Human Services",
    "NSF": "National Science Foundation",
    "DOE": "Department of Energy",
    "USDA": "Department of Agriculture",
    "DOD": "Department of Defense",
    "DHS": "Homeland Security",
    "DOC": "Department of Commerce",
    "DOT": "Department of Transportation",
    "EPA": "Environmental Protection Agency",
    "ED": "Department of Education",
}

#: Sub-agencies worth naming, since the parent code hides them. NIH awards
#: land under HHS and ARPA-E under DOE, and both matter for this project.
BRANCH_NAMES: dict[str, str] = {
    "NIH": "National Institutes of Health",
    "ARPA-E": "ARPA-E",
    "ARPAE": "ARPA-E",
    "NIFA": "National Institute of Food and Agriculture",
    "CDC": "Centers for Disease Control",
    "FDA": "Food and Drug Administration",
    "NOAA": "National Oceanic and Atmospheric Administration",
    "NIST": "National Institute of Standards and Technology",
}


def normalize_agency(value: object) -> str | None:
    """Map an agency string to a canonical short code (``"NASA"``, ``"DOE"``).

    Unrecognized agencies are upper-cased and returned as-is rather than
    dropped, so a new source never silently loses records. Use
    :func:`agency_display` to turn the code back into a readable name.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for pattern, code in _AGENCY_COMPILED:
        if pattern.search(text):
            return code
    return text.upper()


def agency_display(value: object) -> str:
    """Return a human-readable agency name.

    Accepts either a canonical code or a raw source string, so it works on
    stored records and on user input alike. Unknown agencies are title-cased
    rather than dropped -- a missing mapping should look untidy, not empty.
    """
    code = normalize_agency(value)
    if not code:
        return ""
    if name := AGENCY_NAMES.get(code):
        return name
    # Unmapped: don't title-case an acronym into "Nasa".
    return code if code.isupper() and len(code) <= 5 else code.title()


def branch_display(value: object) -> str:
    """Return a readable sub-agency name, or the original string."""
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    return BRANCH_NAMES.get(text.upper(), text)


def normalize_branch(value: object) -> str | None:
    """Map a sub-agency to a canonical comparison key.

    Sources spell branches both ways -- SBIR.gov writes "National Institutes
    of Health" while users type "NIH" -- so both sides of a filter are
    expanded to the long form before comparing.
    """
    display = branch_display(value)
    return display.upper() or None


def agency_label(agency: object, branch: object = None) -> str:
    """Readable agency, naming the sub-agency when it adds information.

    ``("HHS", "NIH")`` -> ``"Health and Human Services - National Institutes
    of Health"``. The branch is omitted when it is blank or merely repeats
    the parent.
    """
    agency_name = agency_display(agency)
    branch_name = branch_display(branch)
    if not branch_name or branch_name.upper() == agency_name.upper():
        return agency_name
    if not agency_name:
        return branch_name
    return f"{agency_name} - {branch_name}"
