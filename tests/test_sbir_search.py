"""SBIR.gov filtered-search adapter: payload building, cap sharding, parsing."""

import csv
import io

import pytest

from sbirgrantsearch.filters import RecordFilter
from sbirgrantsearch.ingest.sbir_search import (
    COLUMN_MAP,
    FORM_AGENCIES,
    RESULT_CAP,
    SbirSearchAdapter,
    SbirSearchUnavailable,
)

ABSTRACT = (
    "NASA is seeking instruments for monitoring in-situ resource utilization "
    "and this project develops a compact spectrometer capable of operating "
    "in the lunar environment for extended durations without recalibration."
)


def make_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(COLUMN_MAP))
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k, "") for k in COLUMN_MAP})
    return buf.getvalue()


def row(**overrides) -> dict:
    base = {
        "Company Name": "INNOSENSE CORPORATION",
        "Award Title": "Compact lunar spectrometer",
        "Abstract": ABSTRACT,
        "Agency": "NASA",
        "Branch": "",
        "Phase": "Phase I",
        "Program": "SBIR",
        "Contract": "80NSSC23C0123",
        "Agency Tracking Number": "T1-1234",
        "Proposal Award Date": "05/07/2023",
        "Contract End Date": "05/06/2024",
        "Award Year": "2023",
        "Award Amount": "181498",
        "Topic Code": "Z1.05",
        "Address 1": "2531 W 237th St",
        "City": "Torrance",
        "State": "CA",
        "ZIP": "90505",
        "Country": "United States",
        "UEI": "Y45MWN6YRAB1",
        "DUNs": "",
        "RI Name": "",
        "Research Area Keywords": "spectrometer; lunar; ISRU",
        "Number Employees": "25",
        "Company URL": "http://innosense.us",
    }
    base.update(overrides)
    return base


class FakeResponse:
    def __init__(self, body: str, content_type: str):
        self._body = body.encode("utf-8")
        self.headers = {"Content-Type": content_type}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def adapter(monkeypatch):
    a = SbirSearchAdapter(delay=0)
    monkeypatch.setattr(a, "_fresh_build_id", lambda: "form-TEST")
    return a


def drive(adapter, monkeypatch, responder):
    """Route the adapter's POSTs through `responder(fields) -> FakeResponse`."""
    calls = []

    class FakeOpener:
        addheaders = []

        def open(self, request, timeout=None):
            import urllib.parse

            fields = urllib.parse.parse_qsl(request.data.decode())
            calls.append(dict(fields))
            return responder(dict(fields))

    monkeypatch.setattr(adapter, "_session", lambda: FakeOpener())
    return calls


# -- payload --------------------------------------------------------------


def test_payload_includes_year_and_download_op(adapter):
    fields = dict(adapter._payload(2023, [], None))
    assert fields["year[2023]"] == "2023"
    assert fields["op"] == "Download"
    assert fields["form_id"] == "awards_search"


def test_payload_pushes_agency_state_phase_program(adapter):
    f = RecordFilter(states={"CA"}, phases={"Phase I"}, programs={"SBIR"})
    fields = dict(adapter._payload(2023, ["NASA"], f))
    assert fields["agency[NASA]"] == "NASA"
    assert fields["state[CA]"] == "CA"
    assert fields["phase"] == "1"
    assert fields["program"] == "SBIR"


def test_payload_omits_phase_when_ambiguous(adapter):
    """The form takes a single radio value; two phases cannot be pushed."""
    f = RecordFilter(phases={"Phase I", "Phase II"})
    assert "phase" not in dict(adapter._payload(2023, [], f))


def test_payload_does_not_push_free_text(adapter):
    """Keyword search is not provably a superset of our substring filter."""
    f = RecordFilter(text_contains="spectrometer")
    fields = dict(adapter._payload(2023, [], f))
    assert "keywords" not in fields


# -- agency translation ---------------------------------------------------


def test_agency_filter_expands_to_form_values(adapter):
    assert adapter._form_agencies(RecordFilter(agencies={"NASA"})) == ["NASA"]
    doe = adapter._form_agencies(RecordFilter(agencies={"DOE"}))
    assert set(doe) == {"DOE", "ARPA-E"}


def test_branch_filter_narrows_to_the_sub_agency(adapter):
    """Asking for NIH must query NIH, not the whole of HHS."""
    assert adapter._form_agencies(RecordFilter(branches={"NIH"})) == ["NIH"]
    assert adapter._form_agencies(RecordFilter(branches={"ARPA-E"})) == ["ARPA-E"]


def test_branch_beats_agency_when_both_given(adapter):
    f = RecordFilter(agencies={"HHS"}, branches={"NIH"})
    assert adapter._form_agencies(f) == ["NIH"]


def test_no_filter_means_no_agency_boxes(adapter):
    assert adapter._form_agencies(None) == []
    assert adapter._form_agencies(RecordFilter()) == []


def test_every_mapped_form_value_is_a_real_checkbox():
    from sbirgrantsearch.ingest.sbir_search import AGENCY_TO_FORM

    for values in AGENCY_TO_FORM.values():
        for v in values:
            assert v in FORM_AGENCIES, f"{v} is not a form checkbox"


# -- downloading ----------------------------------------------------------


def test_fetch_year_parses_csv(adapter, monkeypatch):
    drive(adapter, monkeypatch,
          lambda f: FakeResponse(make_csv([row(), row()]), "text/csv; charset=UTF-8"))
    rows = list(adapter.fetch_year(2023))
    assert len(rows) == 2
    assert rows[0]["Company Name"] == "INNOSENSE CORPORATION"


def test_non_csv_response_is_treated_as_a_cap_hit(adapter, monkeypatch):
    """Over the cap the endpoint returns HTML, not an error."""
    assert adapter._download(2023, ["NASA"], None) is None if False else True
    drive(adapter, monkeypatch, lambda f: FakeResponse("<html>", "text/html"))
    assert adapter._download(2023, ["NASA"], None) is None


def test_cap_hit_shards_by_agency(adapter, monkeypatch):
    """An unfiltered year over the cap is re-run agency by agency."""
    def responder(fields):
        agencies = [k for k in fields if k.startswith("agency[")]
        if not agencies:  # the initial unfiltered attempt
            return FakeResponse("<html>redirect</html>", "text/html")
        return FakeResponse(make_csv([row()]), "text/csv")

    calls = drive(adapter, monkeypatch, responder)
    rows = list(adapter.fetch_year(2023))

    assert len(rows) == len(FORM_AGENCIES)          # one row per shard
    assert len(calls) == len(FORM_AGENCIES) + 1     # plus the failed attempt


def test_cap_hit_shards_only_within_the_requested_agencies(adapter, monkeypatch):
    def responder(fields):
        agencies = [k for k in fields if k.startswith("agency[")]
        if len(agencies) > 1:
            return FakeResponse("<html>", "text/html")
        return FakeResponse(make_csv([row()]), "text/csv")

    drive(adapter, monkeypatch, responder)
    rows = list(adapter.fetch_year(2023, RecordFilter(agencies={"DOE"})))
    assert len(rows) == 2  # DOE and ARPA-E, not all 44 form values


def test_shard_that_still_exceeds_the_cap_raises(adapter, monkeypatch):
    drive(adapter, monkeypatch, lambda f: FakeResponse("<html>", "text/html"))
    with pytest.raises(SbirSearchUnavailable, match="exceeds the row cap"):
        list(adapter.fetch_year(2023, RecordFilter(agencies={"NASA"})))


def test_too_many_shards_raises_rather_than_hammering(monkeypatch):
    a = SbirSearchAdapter(delay=0, max_shards=3)
    monkeypatch.setattr(a, "_fresh_build_id", lambda: "form-TEST")
    drive(a, monkeypatch, lambda f: FakeResponse("<html>", "text/html"))
    with pytest.raises(SbirSearchUnavailable, match="max_shards"):
        list(a.fetch_year(2023))


def test_warns_when_rows_reach_the_cap(adapter, monkeypatch, caplog):
    drive(adapter, monkeypatch,
          lambda f: FakeResponse(make_csv([row()] * RESULT_CAP), "text/csv"))
    with caplog.at_level("WARNING"):
        list(adapter.fetch_year(2023))
    assert "may be truncated" in caplog.text


# -- normalization --------------------------------------------------------


def test_normalize_captures_uei_and_the_extra_fields(adapter):
    record = adapter.normalize(row())
    assert record is not None
    assert record.uei == "Y45MWN6YRAB1"      # absent from the bulk export
    assert record.country == "United States"
    assert record.topic_code == "Z1.05"
    assert record.research_keywords == "spectrometer; lunar; ISRU"
    assert record.agency == "NASA"
    assert record.state == "CA"
    assert record.award_amount == 181_498.0
    assert record.start_date == "2023-05-07"
    assert record.source == "sbir"


def test_normalize_drops_rows_without_a_usable_abstract(adapter):
    assert adapter.normalize(row(Abstract="N/A")) is None


def test_record_id_matches_the_bulk_csv_path_for_the_same_award(adapter, tmp_path):
    """Fallback is only transparent if all transports agree on record_id."""
    from sbirgrantsearch.ingest.sbir_csv import COLUMN_MAP as BULK_MAP
    from sbirgrantsearch.ingest.sbir_csv import SbirCsvAdapter

    bulk_row = {
        "Company": "INNOSENSE CORPORATION",
        "Award Title": "Compact lunar spectrometer",
        "Abstract": ABSTRACT,
        "Agency": "NASA",
        "Contract": "80NSSC23C0123",
        "Award Year": "2023",
    }
    path = tmp_path / "bulk.csv"
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(BULK_MAP))
        w.writeheader()
        w.writerow({k: bulk_row.get(k, "") for k in BULK_MAP})

    bulk = SbirCsvAdapter(csv_path=path)
    bulk.prepare()
    bulk_record = bulk.normalize(next(iter(bulk.fetch_all())))
    search_record = adapter.normalize(row())

    assert bulk_record.record_id == search_record.record_id
    # ...and the search path adds what the bulk path cannot supply.
    assert bulk_record.uei is None
    assert search_record.uei == "Y45MWN6YRAB1"
