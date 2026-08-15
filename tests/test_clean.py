"""Parser tests. These encode the real junk seen in federal exports."""

import pytest

from sbirgrantsearch.clean import (
    AGENCY_NAMES,
    agency_display,
    agency_label,
    branch_display,
    clean_abstract,
    normalize_agency,
    normalize_company,
    normalize_state,
    normalize_zip,
    parse_amount,
    parse_date,
    parse_year,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("171,433", 171433.0),          # the SBIR.gov CSV format
        ("$1,000.50", 1000.50),
        ("1000", 1000.0),
        (1000, 1000.0),
        ("0", 0.0),                     # a real zero, not a missing value
        ("(500)", -500.0),              # accounting negative
        ("-500", -500.0),
        ("", None),
        ("N/A", None),
        (None, None),
        ("not a number", None),
    ],
)
def test_parse_amount(raw, expected):
    assert parse_amount(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("05/07/2024", "2024-05-07"),   # SBIR.gov
        ("2024-05-07", "2024-05-07"),   # NIH
        ("2024-05-07T00:00:00", "2024-05-07"),
        ("2024-05-07T00:00:00.000Z", "2024-05-07"),
        ("07-May-2024", "2024-05-07"),
        ("May 7, 2024", "2024-05-07"),
        ("September 23, 2026", "2026-09-23"),   # SBIR.gov topics export
        ("August 5, 2026", "2026-08-05"),
        ("23 September 2026", "2026-09-23"),
        ("", None),
        ("N/A", None),
        (None, None),
        ("01/01/1900", None),           # sentinel outside sane range
        ("garbage", None),
    ],
)
def test_parse_date(raw, expected):
    assert parse_date(raw) == expected


def test_parse_year():
    assert parse_year("2024") == 2024
    assert parse_year(2024) == 2024
    assert parse_year("FY2024") == 2024
    assert parse_year("") is None
    assert parse_year("99") is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("AGILE DATA DECISIONS, INC.", "agile data decisions"),
        ("Agile Data Decisions Inc", "agile data decisions"),
        ("Agile Data Decisions, LLC", "agile data decisions"),
        ("The Boeing Company", "boeing"),
        ("Acme Corp.", "acme"),
        ("Smith & Wesson", "smith and wesson"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_company(raw, expected):
    assert normalize_company(raw) == expected


def test_normalize_company_collapses_variants():
    """The point of the function: variants must collide on one key."""
    variants = [
        "LUMINAR TECHNOLOGIES, INC.",
        "Luminar Technologies Inc",
        "luminar technologies, llc",
        "Luminar  Technologies",
    ]
    assert len({normalize_company(v) for v in variants}) == 1


def test_clean_abstract_strips_boilerplate():
    assert clean_abstract("ABSTRACT: We build lasers.") == "We build lasers."
    assert clean_abstract(
        "DESCRIPTION (provided by applicant): We build lasers."
    ) == "We build lasers."
    assert clean_abstract(
        "PROJECT SUMMARY: ABSTRACT: We build lasers."
    ) == "We build lasers."


def test_clean_abstract_strips_html_and_entities():
    assert clean_abstract("<p>We build &amp; sell lasers.</p>") == "We build & sell lasers."
    assert clean_abstract("We build &amp;amp; sell.") == "We build & sell."


def test_clean_abstract_collapses_whitespace():
    assert clean_abstract("We   build\n\nlasers.\t") == "We build lasers."


@pytest.mark.parametrize("raw", ["", "N/A", "none", None, "  ", "TBD"])
def test_clean_abstract_nullish(raw):
    assert clean_abstract(raw) == ""


def test_clean_abstract_min_chars():
    assert clean_abstract("Too short.", min_chars=100) == ""
    assert clean_abstract("x" * 150, min_chars=100) == "x" * 150


def test_normalize_state():
    assert normalize_state("CA") == "CA"
    assert normalize_state("ca") == "CA"
    assert normalize_state("California") == "CA"
    assert normalize_state("") is None
    assert normalize_state("Ontario") is None


def test_normalize_zip():
    assert normalize_zip("77017-6559") == "77017"
    assert normalize_zip("77017") == "77017"
    assert normalize_zip("770") is None
    assert normalize_zip(None) is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("National Aeronautics and Space Administration", "NASA"),
        ("NASA", "NASA"),
        ("Department of Health and Human Services", "HHS"),
        ("National Institutes of Health", "HHS"),
        ("Department of Energy", "DOE"),
        ("ARPA-E", "DOE"),
        ("National Science Foundation", "NSF"),
        ("Department of Agriculture", "USDA"),
        ("Department of Homeland Security", "DHS"),
        ("", None),
        (None, None),
    ],
)
def test_normalize_agency(raw, expected):
    assert normalize_agency(raw) == expected


def test_normalize_agency_passes_through_unknown():
    """An unrecognized agency is kept, never silently dropped."""
    assert normalize_agency("Department of Whatever") == "DEPARTMENT OF WHATEVER"


# -- agency display names -------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("NASA", "NASA"),
        ("DOE", "Department of Energy"),
        ("HHS", "Health and Human Services"),
        ("NSF", "National Science Foundation"),
        ("USDA", "Department of Agriculture"),
        # Raw source strings work too, not just canonical codes.
        ("Department of Homeland Security", "Homeland Security"),
        ("National Aeronautics and Space Administration", "NASA"),
        ("ARPA-E", "Department of Energy"),
        ("", ""),
        (None, ""),
    ],
)
def test_agency_display(raw, expected):
    assert agency_display(raw) == expected


def test_agency_display_round_trips_every_code():
    """Every canonical code normalize_agency can emit has a readable name."""
    for code in AGENCY_NAMES:
        assert agency_display(code) == AGENCY_NAMES[code]
        assert normalize_agency(AGENCY_NAMES[code]) == code


def test_agency_display_keeps_unmapped_acronyms_intact():
    """An unknown acronym must not be title-cased into 'Nasa'."""
    assert agency_display("XYZ") == "XYZ"
    assert agency_display("Department of Whatever") == "Department Of Whatever"


def test_branch_display():
    assert branch_display("NIH") == "National Institutes of Health"
    assert branch_display("nifa") == "National Institute of Food and Agriculture"
    assert branch_display("Army") == "Army"  # unmapped passes through
    assert branch_display("") == ""
    assert branch_display(None) == ""


def test_agency_label_combines_agency_and_branch():
    assert agency_label("HHS", "NIH") == (
        "Health and Human Services - National Institutes of Health"
    )
    assert agency_label("DOE", "ARPA-E") == "Department of Energy - ARPA-E"


def test_agency_label_omits_redundant_branch():
    assert agency_label("NASA", "") == "NASA"
    assert agency_label("NASA", None) == "NASA"
    assert agency_label("NASA", "NASA") == "NASA"
