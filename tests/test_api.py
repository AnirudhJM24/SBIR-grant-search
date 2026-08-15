"""Library-facing API: filter kwargs, transport fallback, exports."""

import csv
import json

import pytest

import sbirgrantsearch as gs
from sbirgrantsearch.api import Download, make_filter, normalize_years
from sbirgrantsearch.filters import RecordFilter
from sbirgrantsearch.ingest.sbir import SbirSource
from sbirgrantsearch.ingest.sbir_api import SbirApiUnavailable
from sbirgrantsearch.models import Record

from .test_ingest import ROWS  # noqa: F401  (shared fixture data)


# -- filter keywords ------------------------------------------------------


def test_make_filter_accepts_singular_and_plural():
    assert make_filter(agency="NASA").agencies == {"NASA"}
    assert make_filter(agencies=["NASA", "DOE"]).agencies == {"NASA", "DOE"}
    assert make_filter(state="CA").states == {"CA"}


def test_make_filter_accepts_scalars_where_sets_are_expected():
    """`agency="NASA"` must not become {"N","A","S","A"}."""
    assert make_filter(agency="NASA").agencies == {"NASA"}
    assert make_filter(fiscal_year=2023).fiscal_years == {2023}


def test_make_filter_normalizes_like_recordfilter():
    f = make_filter(agency="Department of Energy", state="California")
    assert f.agencies == {"DOE"}
    assert f.states == {"CA"}


def test_make_filter_rejects_unknown_keyword():
    """A silently dropped filter would quietly return too many records."""
    with pytest.raises(TypeError, match="Unknown filter 'agncy'"):
        make_filter(agncy="NASA")


def test_make_filter_ignores_none_values():
    assert make_filter(agency=None, state=None) == RecordFilter()


def test_make_filter_passes_through_scalar_options():
    f = make_filter(min_amount=500_000, contains="laser", require_ri=True)
    assert f.min_amount == 500_000
    assert f.text_contains == "laser"
    assert f.require_ri


@pytest.mark.parametrize(
    "spec,expected",
    [
        (2023, [2023]),
        ([2023, 2021], [2021, 2023]),
        (range(2020, 2023), [2020, 2021, 2022]),
        ("2020-2022", [2020, 2021, 2022]),
        ("2020,2023", [2020, 2023]),
    ],
)
def test_normalize_years(spec, expected):
    assert normalize_years(spec) == expected


def test_normalize_years_defaults_and_rejects_empty():
    assert normalize_years(None) == list(range(2015, 2026))
    with pytest.raises(ValueError):
        normalize_years([])


# -- transport fallback ---------------------------------------------------


@pytest.fixture
def local_csv(tmp_path):
    from sbirgrantsearch.ingest.sbir_csv import COLUMN_MAP

    path = tmp_path / "award_data.csv"
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(COLUMN_MAP))
        writer.writeheader()
        for row in ROWS:
            writer.writerow({k: row.get(k, "") for k in COLUMN_MAP})
    return path


def test_auto_falls_back_to_csv_when_api_is_down(local_csv, monkeypatch):
    monkeypatch.setattr(
        "sbirgrantsearch.ingest.sbir_api.SbirApiAdapter.probe", lambda self: False
    )
    source = SbirSource(transport="auto", csv_path=local_csv)
    source.prepare()
    assert source.transport_used == "csv"


def test_auto_prefers_api_when_it_is_up(local_csv, monkeypatch):
    monkeypatch.setattr(
        "sbirgrantsearch.ingest.sbir_api.SbirApiAdapter.probe", lambda self: True
    )
    source = SbirSource(transport="auto", csv_path=local_csv)
    source.prepare()
    assert source.transport_used == "api"


def test_pinned_api_transport_raises_instead_of_falling_back(local_csv, monkeypatch):
    """A pinned transport must fail loudly -- pipelines need determinism."""
    monkeypatch.setattr(
        "sbirgrantsearch.ingest.sbir_api.SbirApiAdapter.probe", lambda self: False
    )
    source = SbirSource(transport="api", csv_path=local_csv)
    with pytest.raises(SbirApiUnavailable):
        source.prepare()


def test_pinned_csv_transport_never_touches_the_api(local_csv, monkeypatch):
    def explode(self):
        raise AssertionError("API must not be probed when csv is pinned")

    monkeypatch.setattr("sbirgrantsearch.ingest.sbir_api.SbirApiAdapter.probe", explode)
    source = SbirSource(transport="csv", csv_path=local_csv)
    source.prepare()
    assert source.transport_used == "csv"


def test_invalid_transport_rejected():
    with pytest.raises(ValueError, match="transport must be"):
        SbirSource(transport="magic")


def test_both_transports_share_one_logical_source(local_csv, monkeypatch):
    """The fallback is only transparent if record ids match across paths."""
    from sbirgrantsearch.ingest.sbir_api import SbirApiAdapter
    from sbirgrantsearch.ingest.sbir_csv import SbirCsvAdapter

    csv_adapter = SbirCsvAdapter(csv_path=local_csv)
    csv_adapter.prepare()
    csv_record = csv_adapter.normalize(next(iter(csv_adapter.fetch_year(2024))))

    api_record = SbirApiAdapter().normalize({
        "firm": "AGILE DATA DECISIONS, INC.",
        "award_title": "AI-DLCS: Artificial Intelligence for Data Labeling",
        "abstract": ROWS[0]["Abstract"],
        "agency": "Department of Homeland Security",
        "contract": "70RSAT24C00000033",
        "award_year": 2024,
    })

    assert csv_record.source == api_record.source == "sbir"
    assert csv_record.record_id == api_record.record_id


# -- download() -----------------------------------------------------------


def test_download_returns_records_and_transport(local_csv):
    result = gs.download(csv_path=local_csv, years=[2023, 2024], transport="csv")
    assert isinstance(result, Download)
    assert len(result) == 2
    assert result.transport == "csv"
    assert all(isinstance(r, Record) for r in result)


def test_download_applies_filters(local_csv):
    result = gs.download(csv_path=local_csv, years=[2023, 2024],
                         transport="csv", agency="NASA")
    assert len(result) == 1
    assert result[0].agency == "NASA"


def test_download_rejects_unknown_filter(local_csv):
    with pytest.raises(TypeError):
        gs.download(csv_path=local_csv, years=2023, transport="csv", stat="CA")


def test_download_dedupes_across_years(local_csv):
    result = gs.download(csv_path=local_csv, years=[2023, 2023], transport="csv")
    ids = [r.record_id for r in result]
    assert len(ids) == len(set(ids))


def test_download_persists_when_out_dir_given(local_csv, tmp_path):
    out = tmp_path / "raw"
    result = gs.download(csv_path=local_csv, years=[2024], transport="csv",
                         out_dir=out)
    assert (out / "sbir_2024.jsonl").exists()
    assert result.stats is not None
    assert len(result) == 1


def test_download_reuses_persisted_years(local_csv, tmp_path):
    out = tmp_path / "raw"
    gs.download(csv_path=local_csv, years=[2024], transport="csv", out_dir=out)
    second = gs.download(csv_path=local_csv, years=[2024], transport="csv",
                         out_dir=out)
    assert second.stats.stats[0].skipped
    assert len(second) == 1  # still returns the records, from disk


def test_stream_is_lazy(local_csv):
    stream = gs.stream(csv_path=local_csv, years=[2023, 2024], transport="csv")
    assert isinstance(next(stream), Record)


def test_download_limit(local_csv):
    result = gs.download(csv_path=local_csv, years=[2023], transport="csv", limit=1)
    assert len(result) == 1


# -- Download result object ----------------------------------------------


@pytest.fixture
def result(local_csv):
    return gs.download(csv_path=local_csv, years=[2023, 2024], transport="csv")


def test_result_behaves_like_a_sequence(result):
    assert len(result) == 2
    assert result[0] in list(result)
    assert bool(result)


def test_filter_by_narrows_without_refetching(result):
    narrowed = result.filter_by(agency="NASA")
    assert len(narrowed) == 1
    assert len(result) == 2  # original untouched


def test_to_dicts_excludes_raw_by_default(result):
    assert "raw" not in result.to_dicts()[0]
    assert "raw" in result.to_dicts(include_raw=True)[0]


def test_to_jsonl_round_trips(result, tmp_path):
    path = result.to_jsonl(tmp_path / "out.jsonl")
    loaded = [Record.from_dict(json.loads(line)) for line in path.read_text(
        encoding="utf-8").splitlines()]
    assert loaded == result.records


def test_to_csv_writes_flat_schema(result, tmp_path):
    path = result.to_csv(tmp_path / "out.csv")
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    assert len(rows) == 2
    assert "raw" not in rows[0]           # nested dict excluded from flat CSV
    assert rows[0]["record_id"]
    assert rows[0]["abstract_clean"]


def test_to_csv_accepts_explicit_columns(result, tmp_path):
    path = result.to_csv(tmp_path / "out.csv", columns=["recipient", "agency"])
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    assert list(rows[0]) == ["recipient", "agency"]


def test_by_company_groups_on_normalized_name(result):
    groups = result.by_company()
    assert "agile data decisions" in groups
    assert "luminar technologies" in groups


def test_summary_mentions_transport(result):
    assert "via csv" in result.summary()
    assert "2 awards" in result.summary()


# -- probe caching --------------------------------------------------------


def test_probe_result_is_cached_across_calls(monkeypatch):
    """Several download() calls must not each pay for a network probe."""
    from sbirgrantsearch.ingest import sbir_api

    sbir_api.reset_probe_cache()
    calls = []

    def fake_get_json(url, **kwargs):
        calls.append(url)
        raise sbir_api.http.HttpError("HTTP 403", status=403)

    monkeypatch.setattr(sbir_api.http, "get_json", fake_get_json)
    adapter = sbir_api.SbirApiAdapter()
    assert adapter.probe() is False
    assert adapter.probe() is False
    assert adapter.probe() is False
    assert len(calls) == 1


def test_probe_force_bypasses_cache(monkeypatch):
    from sbirgrantsearch.ingest import sbir_api

    sbir_api.reset_probe_cache()
    calls = []

    def fake_get_json(url, **kwargs):
        calls.append(url)
        raise sbir_api.http.HttpError("HTTP 403", status=403)

    monkeypatch.setattr(sbir_api.http, "get_json", fake_get_json)
    adapter = sbir_api.SbirApiAdapter()
    adapter.probe()
    adapter.probe(force=True)
    assert len(calls) == 2


def test_probe_cache_expires(monkeypatch):
    from sbirgrantsearch.ingest import sbir_api

    sbir_api.reset_probe_cache()
    monkeypatch.setattr(sbir_api, "PROBE_TTL", 0.0)
    calls = []

    def fake_get_json(url, **kwargs):
        calls.append(url)
        raise sbir_api.http.HttpError("HTTP 403", status=403)

    monkeypatch.setattr(sbir_api.http, "get_json", fake_get_json)
    adapter = sbir_api.SbirApiAdapter()
    adapter.probe()
    adapter.probe()
    assert len(calls) == 2


def test_probe_survives_maintenance_envelope(monkeypatch):
    """A {"message": "Forbidden"} body must return False, not propagate."""
    from sbirgrantsearch.ingest import sbir_api

    sbir_api.reset_probe_cache()
    monkeypatch.setattr(
        sbir_api.http, "get_json", lambda url, **kw: {"message": "Forbidden"}
    )
    assert sbir_api.SbirApiAdapter().probe() is False
