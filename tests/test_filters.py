"""Filter composition and edge-case tests."""

import pytest

from sbirgrantsearch.filters import RETRIEVABLE, STARTUPS_ONLY, RecordFilter, any_of
from sbirgrantsearch.models import Record


def make(**overrides) -> Record:
    defaults = dict(
        record_id="sbir_csv:1",
        source="sbir_csv",
        recipient="Acme Inc",
        recipient_norm="acme",
        recipient_type="company",
        title="Laser widgets",
        abstract="x" * 200,
        abstract_clean="x" * 200,
        agency="NASA",
        program="SBIR",
        phase="Phase I",
        award_amount=150_000.0,
        fiscal_year=2023,
        start_date="2023-05-07",
        end_date="2024-05-07",
        state="CA",
    )
    return Record(**{**defaults, **overrides})


def test_empty_filter_matches_everything():
    assert RecordFilter().matches(make())


def test_agency_normalized_on_both_sides():
    f = RecordFilter(agencies={"NASA"})
    assert f.matches(make(agency="NASA"))
    assert f.matches(make(agency="National Aeronautics and Space Administration"))
    assert not f.matches(make(agency="DOE"))


def test_state_accepts_full_name_in_filter_or_record():
    assert RecordFilter(states={"California"}).matches(make(state="CA"))
    assert RecordFilter(states={"CA"}).matches(make(state="California"))


def test_amount_range():
    f = RecordFilter(min_amount=100_000, max_amount=200_000)
    assert f.matches(make(award_amount=150_000))
    assert not f.matches(make(award_amount=50_000))
    assert not f.matches(make(award_amount=250_000))


def test_amount_filter_accepts_formatted_strings():
    """Filter bounds go through the same parser as the data."""
    assert RecordFilter(min_amount="100,000").matches(make(award_amount=150_000))


def test_missing_value_fails_a_range_constraint():
    """An unknown amount is not evidence the record is in range."""
    assert not RecordFilter(min_amount=1).matches(make(award_amount=None))
    assert not RecordFilter(fiscal_year_min=2020).matches(make(fiscal_year=None))
    assert not RecordFilter(start_after="2020-01-01").matches(make(start_date=None))
    # ...but with no constraint, a missing value is fine.
    assert RecordFilter().matches(make(award_amount=None, fiscal_year=None))


def test_fiscal_year_set_and_range():
    assert RecordFilter(fiscal_years={2023}).matches(make())
    assert not RecordFilter(fiscal_years={2021}).matches(make())
    assert RecordFilter(fiscal_year_min=2020, fiscal_year_max=2025).matches(make())
    assert not RecordFilter(fiscal_year_max=2022).matches(make())


def test_date_windows():
    assert RecordFilter(start_after="2023-01-01").matches(make())
    assert not RecordFilter(start_before="2023-01-01").matches(make())
    assert RecordFilter(end_after="2024-01-01").matches(make())


def test_date_filter_accepts_us_format():
    """Filter dates are parsed, so CLI users can type 05/07/2023."""
    assert RecordFilter(start_after="05/01/2023").matches(make())


def test_abstract_gates():
    assert not RecordFilter(require_abstract=True).matches(make(abstract_clean=""))
    assert not RecordFilter(min_abstract_chars=500).matches(make())
    assert RecordFilter(min_abstract_chars=100).matches(make())


def test_require_ri():
    f = RecordFilter(require_ri=True)
    assert not f.matches(make())
    assert f.matches(make(ri_name="MIT"))


def test_text_contains_is_case_insensitive():
    assert RecordFilter(text_contains="LASER").matches(make())
    assert not RecordFilter(text_contains="turbine").matches(make())


def test_and_composition_requires_both():
    f = RecordFilter(agencies={"NASA"}) & RecordFilter(states={"CA"})
    assert f.matches(make())
    assert not f.matches(make(state="TX"))
    assert not f.matches(make(agency="DOE"))


def test_any_of_requires_either():
    f = any_of(RecordFilter(states={"CA"}), RecordFilter(states={"TX"}))
    assert f.matches(make(state="CA"))
    assert f.matches(make(state="TX"))
    assert not f.matches(make(state="NY"))


def test_where_adds_custom_predicate():
    f = RecordFilter(agencies={"NASA"}).where(lambda r: r.recipient_norm == "acme")
    assert f.matches(make())
    assert not f.matches(make(recipient_norm="other"))


def test_replace_returns_new_filter():
    base = RecordFilter(agencies={"NASA"})
    narrowed = base.replace(states={"CA"})
    assert narrowed.matches(make())
    assert not narrowed.matches(make(state="TX"))
    assert base.matches(make(state="TX"))  # original untouched


def test_from_dict_ignores_unknown_keys():
    f = RecordFilter.from_dict({"agencies": {"NASA"}, "nonsense": 1, "states": None})
    assert f.matches(make())


def test_apply_is_lazy_and_filters():
    records = [make(state="CA"), make(state="TX"), make(state="CA")]
    assert len(list(RecordFilter(states={"CA"}).apply(records))) == 2


def test_prebuilt_filters():
    assert STARTUPS_ONLY.matches(make())
    assert RETRIEVABLE.matches(make())
    assert not RETRIEVABLE.matches(make(abstract_clean="short"))


def test_filter_is_hashable_and_frozen():
    with pytest.raises(Exception):
        RecordFilter().agencies = {"NASA"}


def test_branch_filter_accepts_acronym_or_full_name():
    """Sources spell branches out; users type acronyms. Both must match."""
    nih = make(agency="HHS", branch="National Institutes of Health")
    assert RecordFilter(branches={"NIH"}).matches(nih)
    assert RecordFilter(branches={"National Institutes of Health"}).matches(nih)
    assert not RecordFilter(branches={"ARPA-E"}).matches(nih)


def test_branch_filter_matches_unmapped_branches():
    assert RecordFilter(branches={"Air Force"}).matches(make(branch="Air Force"))
    assert RecordFilter(branches={"air force"}).matches(make(branch="Air Force"))


def test_branch_filter_rejects_missing_branch():
    assert not RecordFilter(branches={"NIH"}).matches(make(branch=None))
