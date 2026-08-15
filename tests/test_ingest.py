"""Adapter and ingest-driver tests, run against a fixture CSV."""

import csv
import json

import pytest

from sbirgrantsearch.filters import RecordFilter
from sbirgrantsearch.ingest import load_records, run_ingest, year_path
from sbirgrantsearch.ingest.base import SourceAdapter
from sbirgrantsearch.ingest.sbir_api import SbirApiAdapter, SbirApiUnavailable
from sbirgrantsearch.ingest.sbir_csv import COLUMN_MAP, SbirCsvAdapter
from sbirgrantsearch.models import Record

ABSTRACT = (
    "The Department of Homeland Security grapples with vast and diverse "
    "datasets collected daily, ranging from personal records to sensor "
    "telemetry, and this project develops machine learning methods to label "
    "and curate them at scale for downstream analysis."
)

ROWS = [
    {
        "Company": "AGILE DATA DECISIONS, INC.",
        "Award Title": "AI-DLCS: Artificial Intelligence for Data Labeling",
        "Agency": "Department of Homeland Security",
        "Branch": "",
        "Phase": "Phase I",
        "Program": "SBIR",
        "Agency Tracking Number": "24.1 DHS241-002-0076-I",
        "Contract": "70RSAT24C00000033",
        "Proposal Award Date": "05/07/2024",
        "Contract End Date": "10/06/2024",
        "Award Year": "2024",
        "Award Amount": "171,433",
        "Address1": "8866 Gulf Fwy",
        "Address2": "STE 250F FL 2",
        "City": "Houston",
        "State": "TX",
        "Zip": "77017-6559",
        "Company Website": "http://agiledd.ai",
        "Number Employees": "6",
        "RI Name": "",
        "Abstract": ABSTRACT,
    },
    {
        "Company": "Luminar Technologies LLC",
        "Award Title": "Long-duration energy storage",
        "Agency": "National Aeronautics and Space Administration",
        "Branch": "",
        "Phase": "Phase II",
        "Program": "STTR",
        "Agency Tracking Number": "T2-9999",
        "Contract": "80NSSC24C1234",
        "Proposal Award Date": "01/15/2023",
        "Contract End Date": "01/14/2025",
        "Award Year": "2023",
        "Award Amount": "$850,000",
        "Address1": "1 Main St",
        "Address2": "",
        "City": "Palo Alto",
        "State": "California",
        "Zip": "94301",
        "Company Website": "",
        "Number Employees": "42",
        "RI Name": "Massachusetts Institute of Technology",
        "Abstract": ABSTRACT,
    },
    {   # dropped: abstract too short to retrieve
        "Company": "No Abstract Co",
        "Award Title": "Mystery project",
        "Agency": "Department of Energy",
        "Award Year": "2023",
        "Award Amount": "100000",
        "Abstract": "N/A",
    },
]


@pytest.fixture
def csv_file(tmp_path):
    path = tmp_path / "award_data.csv"
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(COLUMN_MAP))
        writer.writeheader()
        for row in ROWS:
            writer.writerow({k: row.get(k, "") for k in COLUMN_MAP})
    return path


@pytest.fixture
def adapter(csv_file):
    a = SbirCsvAdapter(csv_path=csv_file)
    a.prepare()
    return a


def test_prepare_rejects_missing_local_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        SbirCsvAdapter(csv_path=tmp_path / "nope.csv").prepare()


def test_fetch_year_filters_by_award_year(adapter):
    assert len(list(adapter.fetch_year(2024))) == 1
    assert len(list(adapter.fetch_year(2023))) == 2
    assert list(adapter.fetch_year(1999)) == []


def test_available_years(adapter):
    assert adapter.available_years() == [2023, 2024]


def test_normalize_maps_every_requested_field(adapter):
    record = adapter.normalize(next(iter(adapter.fetch_year(2024))))
    assert record is not None
    assert record.recipient == "AGILE DATA DECISIONS, INC."
    assert record.recipient_norm == "agile data decisions"
    assert record.title.startswith("AI-DLCS")
    assert record.abstract_clean == ABSTRACT
    assert record.agency == "DHS"
    assert record.fiscal_year == 2024
    assert record.start_date == "2024-05-07"
    assert record.end_date == "2024-10-06"
    assert record.award_amount == 171_433.0
    assert record.city == "Houston"
    assert record.state == "TX"
    assert record.zip_code == "77017"
    assert record.address == "8866 Gulf Fwy, STE 250F FL 2"
    assert record.num_employees == 6
    assert record.recipient_type == "company"
    assert record.raw  # original payload preserved


def test_normalize_handles_agency_and_state_variants(adapter):
    rows = {r["Company"]: r for r in adapter.fetch_all()}
    record = adapter.normalize(rows["Luminar Technologies LLC"])
    assert record.agency == "NASA"
    assert record.state == "CA"
    assert record.award_amount == 850_000.0
    assert record.ri_name == "Massachusetts Institute of Technology"
    assert record.recipient_norm == "luminar technologies"


def test_normalize_drops_rows_without_usable_abstract(adapter):
    rows = {r["Company"]: r for r in adapter.fetch_all()}
    assert adapter.normalize(rows["No Abstract Co"]) is None


def test_record_ids_are_unique_and_namespaced(adapter):
    ids = [
        r.record_id
        for row in adapter.fetch_all()
        if (r := adapter.normalize(row))
    ]
    assert len(ids) == len(set(ids))
    # Namespaced by logical source, not by transport: the same award keeps
    # this id whether the API or the CSV served it.
    assert all(i.startswith("sbir:") for i in ids)


def test_search_text_combines_fields(adapter):
    record = adapter.normalize(next(iter(adapter.fetch_year(2024))))
    assert "AI-DLCS" in record.search_text
    assert "AGILE DATA DECISIONS" in record.search_text


def test_record_round_trips_through_json(adapter):
    record = adapter.normalize(next(iter(adapter.fetch_year(2024))))
    assert Record.from_dict(json.loads(record.to_json())) == record


# -- driver ---------------------------------------------------------------


def test_run_ingest_writes_one_file_per_year(adapter, tmp_path):
    out = tmp_path / "raw"
    result = run_ingest(adapter, years=[2023, 2024], out_dir=out)
    assert year_path(out, "sbir_csv", 2023).exists()
    assert year_path(out, "sbir_csv", 2024).exists()
    assert result.written == 2  # the N/A-abstract row was dropped


def test_run_ingest_skips_existing_years(adapter, tmp_path):
    out = tmp_path / "raw"
    run_ingest(adapter, years=[2024], out_dir=out)
    second = run_ingest(adapter, years=[2024], out_dir=out)
    assert second.stats[0].skipped
    assert second.written == 0


def test_run_ingest_overwrite_rewrites(adapter, tmp_path):
    out = tmp_path / "raw"
    run_ingest(adapter, years=[2024], out_dir=out)
    second = run_ingest(adapter, years=[2024], out_dir=out, overwrite=True)
    assert not second.stats[0].skipped
    assert second.written == 1


def test_run_ingest_applies_filter(adapter, tmp_path):
    result = run_ingest(
        adapter,
        years=[2023, 2024],
        out_dir=tmp_path / "raw",
        record_filter=RecordFilter(agencies={"NASA"}),
    )
    assert result.written == 1
    # The CSV adapter pushes the agency constraint into its row scan, so the
    # non-matching row is skipped before normalization rather than counted
    # as filtered out afterwards.
    assert sum(s.dropped_filter for s in result.stats) == 0
    assert sum(s.fetched for s in result.stats) == 1


def test_run_ingest_dedupes_across_years(adapter, tmp_path):
    """The same record id must not be written twice in one run."""
    result = run_ingest(adapter, years=[2023, 2023], out_dir=tmp_path / "raw",
                        overwrite=True)
    assert sum(s.dropped_duplicate for s in result.stats) == 1


def test_run_ingest_leaves_no_tmp_file_on_failure(tmp_path):
    class Exploding(SourceAdapter):
        name = "boom"

        def fetch_year(self, year, record_filter=None):
            yield {"x": 1}
            raise RuntimeError("network died")

        def normalize(self, raw):
            return None

    out = tmp_path / "raw"
    with pytest.raises(RuntimeError):
        run_ingest(Exploding(), years=[2024], out_dir=out)
    assert not list(out.glob("*.tmp"))
    assert not year_path(out, "boom", 2024).exists()


def test_load_records_round_trip(adapter, tmp_path):
    out = tmp_path / "raw"
    run_ingest(adapter, years=[2023, 2024], out_dir=out)
    records = list(load_records(out))
    assert len(records) == 2
    assert {r.agency for r in records} == {"DHS", "NASA"}


def test_load_records_applies_filter(adapter, tmp_path):
    out = tmp_path / "raw"
    run_ingest(adapter, years=[2023, 2024], out_dir=out)
    hits = list(load_records(out, record_filter=RecordFilter(states={"CA"})))
    assert len(hits) == 1


def test_load_records_skips_corrupt_lines(adapter, tmp_path):
    out = tmp_path / "raw"
    run_ingest(adapter, years=[2024], out_dir=out)
    path = year_path(out, "sbir_csv", 2024)
    path.write_text(path.read_text(encoding="utf-8") + "{not json\n\n", encoding="utf-8")
    assert len(list(load_records(out))) == 1


# -- API adapter ----------------------------------------------------------


def test_api_extract_rows_handles_both_shapes():
    extract = SbirApiAdapter._extract_rows
    assert extract([{"a": 1}]) == [{"a": 1}]
    assert extract({"results": [{"a": 1}]}) == [{"a": 1}]
    assert extract({"data": [{"a": 1}]}) == [{"a": 1}]
    assert extract({}) == []


def test_api_extract_rows_raises_on_maintenance_envelope():
    """The live response today is exactly {"message": "Forbidden"}."""
    with pytest.raises(SbirApiUnavailable):
        SbirApiAdapter._extract_rows({"message": "Forbidden"})


def test_api_normalize_produces_same_shape_as_csv():
    """Both adapters must yield equivalent records for the same award."""
    record = SbirApiAdapter().normalize({
        "firm": "AGILE DATA DECISIONS, INC.",
        "award_title": "AI-DLCS",
        "abstract": ABSTRACT,
        "agency": "Department of Homeland Security",
        "phase": "Phase I",
        "program": "SBIR",
        "contract": "70RSAT24C00000033",
        "proposal_award_date": "2024-05-07",
        "award_year": 2024,
        "award_amount": 171433,
        "city": "Houston",
        "state": "TX",
        "zip": "77017-6559",
    })
    assert record.source == "sbir"
    assert record.agency == "DHS"
    assert record.award_amount == 171_433.0
    assert record.start_date == "2024-05-07"
    assert record.recipient_norm == "agile data decisions"


def test_api_normalize_uses_field_aliases():
    record = SbirApiAdapter().normalize({
        "firm_name": "Acme Inc",
        "award_title": "Widgets",
        "abstract_text": ABSTRACT,
        "agency": "NASA",
        "year": 2022,
    })
    assert record.recipient == "Acme Inc"
    assert record.fiscal_year == 2022


def test_record_id_is_stable_when_title_changes(adapter):
    """The id keys off the contract number, not the wording of the title."""
    rows = list(adapter.fetch_all())
    original = adapter.normalize(rows[0])
    reworded = adapter.normalize({**rows[0], "Award Title": "Completely new name"})
    assert original.record_id == reworded.record_id
    assert "70RSAT24C00000033" in original.record_id


def test_record_id_falls_back_to_hash_without_contract(adapter):
    rows = list(adapter.fetch_all())
    record = adapter.normalize(
        {**rows[0], "Contract": "", "Agency Tracking Number": ""}
    )
    assert record.record_id.startswith("sbir:h:")


def test_record_id_namespaces_by_agency(adapter):
    """Contract numbers repeat across agencies; the id must not collide."""
    rows = list(adapter.fetch_all())
    a = adapter.normalize(rows[0])
    b = adapter.normalize({**rows[0], "Agency": "Department of Energy"})
    assert a.record_id != b.record_id


def test_record_exposes_readable_agency(adapter):
    rows = {r["Company"]: r for r in adapter.fetch_all()}
    dhs = adapter.normalize(rows["AGILE DATA DECISIONS, INC."])
    assert dhs.agency == "DHS"                     # stored: the filter key
    assert dhs.agency_name == "Homeland Security"  # derived: for display


def test_agency_name_is_derived_not_stored(adapter):
    """Display names must not bloat the JSONL or require re-ingesting."""
    record = adapter.normalize(next(iter(adapter.fetch_year(2024))))
    assert "agency_name" not in record.to_dict()
    assert Record.from_dict(json.loads(record.to_json())).agency_name == (
        "Homeland Security"
    )


def test_agency_label_uses_branch_when_present(adapter):
    rows = list(adapter.fetch_all())
    record = adapter.normalize({**rows[0], "Agency": "HHS", "Branch": "NIH"})
    assert record.agency_label == (
        "Health and Human Services - National Institutes of Health"
    )
