"""Solicitation topics: payload building, parsing, and the result object."""

import csv
import io
import json

import pytest

import sbirgrantsearch as gs
from sbirgrantsearch.topics import (
    COLUMN_MAP,
    SbirTopicsClient,
    Topic,
    TopicResults,
    _form_agencies,
    download_topics,
)

DESCRIPTION = (
    "A network of radar-equipped UAS or sensors can be designed as a "
    "collaborative distributed radar team to improve detection of low "
    "observable targets in contested environments."
)


def make_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(COLUMN_MAP))
    writer.writeheader()
    for r in rows:
        writer.writerow({k: r.get(k, "") for k in COLUMN_MAP})
    return buf.getvalue()


def row(**overrides) -> dict:
    base = {
        "Topic Title": "Collaborative Distributed Swarm Radar",
        "Topic Description": DESCRIPTION,
        "Topic Number": "OSW26BZ05-DV019",
        "Phase": "BOTH",
        "Program": "SBIR",
        "Agency": "DOD",
        "Branch": "OSD",
        "Open Date": "August 26, 2026",
        "Close Date": "September 23, 2026",
        "Release Date": "August 5, 2026",
        "Solicitation Agency URL": "https://example.gov/sol",
        "Solicitation Status": "Open",
        "Solicitation Year": "2026",
        "SBIRTopicLink": "https://www.sbir.gov/topics/12846",
    }
    base.update(overrides)
    return base


class FakeResponse:
    def __init__(self, body, content_type):
        self._body = body.encode("utf-8")
        self.headers = {"Content-Type": content_type}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def client(monkeypatch):
    c = SbirTopicsClient(delay=0)
    monkeypatch.setattr(c, "_fresh_build_id", lambda: "form-TEST")
    return c


def drive(client, monkeypatch, responder):
    calls = []

    class FakeOpener:
        addheaders = []

        def open(self, request, timeout=None):
            import urllib.parse

            fields = urllib.parse.parse_qsl(request.data.decode())
            calls.append(dict(fields))
            return responder(dict(fields))

    monkeypatch.setattr(client, "_session", lambda: FakeOpener())
    return calls


# -- payload --------------------------------------------------------------


def test_payload_defaults_to_a_bare_download(client):
    fields = dict(client._payload())
    assert fields["form_id"] == "topics_search"
    assert fields["op"] == "Download"


def test_payload_pushes_filters(client):
    fields = dict(client._payload(
        agencies=["NSF"], years=[2026], status="open",
        program="sbir", phase="Phase I", keywords="quantum",
    ))
    assert fields["agency[NSF]"] == "NSF"
    assert fields["year[2026]"] == "2026"
    assert fields["status"] == "Open"      # the form wants it capitalized
    assert fields["program"] == "SBIR"
    assert fields["phase"] == "1"
    assert fields["keywords"] == "quantum"


def test_payload_normalizes_date_windows(client):
    fields = dict(client._payload(close_date_from="09/01/2026"))
    assert fields["close_date_from"] == "2026-09-01"


def test_form_agencies_accepts_codes_and_sub_agencies():
    assert _form_agencies("NSF") == ["NSF"]
    assert _form_agencies("NIH") == ["NIH"]
    assert _form_agencies("DARPA") == ["DARPA"]
    # A parent box covers its children; sending both narrows to the child.
    assert _form_agencies("DOE") == ["DOE"]
    assert _form_agencies("Department of Energy") == ["DOE"]
    assert _form_agencies(None) == []


# -- parsing --------------------------------------------------------------


def test_normalize_maps_every_column(client):
    t = client.normalize(row())
    assert t.title == "Collaborative Distributed Swarm Radar"
    assert t.topic_number == "OSW26BZ05-DV019"
    assert t.agency == "DOD"
    assert t.branch == "OSD"
    assert t.program == "SBIR"
    assert t.status == "Open"
    assert t.is_open
    assert t.solicitation_year == 2026
    assert t.url == "https://www.sbir.gov/topics/12846"


def test_normalize_parses_spelled_out_dates(client):
    """The topics export writes 'September 23, 2026', not 09/23/2026."""
    t = client.normalize(row())
    assert t.open_date == "2026-08-26"
    assert t.close_date == "2026-09-23"
    assert t.release_date == "2026-08-05"


def test_topic_id_is_namespaced_and_stable(client):
    t = client.normalize(row())
    assert t.topic_id == "sbir_topics:DOD:OSW26BZ05-DV019"
    # Rewording the title must not change the id.
    assert client.normalize(row(**{"Topic Title": "Renamed"})).topic_id == t.topic_id


def test_normalize_drops_empty_rows(client):
    assert client.normalize(row(**{"Topic Title": "", "Topic Description": ""})) is None


def test_topic_round_trips_through_json(client):
    t = client.normalize(row())
    assert Topic.from_dict(json.loads(t.to_json())) == t


def test_search_text_combines_title_and_description(client):
    t = client.normalize(row())
    assert "Swarm Radar" in t.search_text
    assert "contested environments" in t.search_text


# -- download -------------------------------------------------------------


def test_download_topics_parses_results(client, monkeypatch):
    drive(client, monkeypatch,
          lambda f: FakeResponse(make_csv([row(), row(**{"Topic Number": "X2"})]),
                                 "text/csv"))
    results = download_topics(client=client)
    assert len(results) == 2
    assert isinstance(results[0], Topic)


def test_download_topics_dedupes_on_topic_id(client, monkeypatch):
    drive(client, monkeypatch,
          lambda f: FakeResponse(make_csv([row(), row()]), "text/csv"))
    assert len(download_topics(client=client)) == 1


def test_no_matches_returns_empty_not_an_error(client, monkeypatch):
    """An empty result set redirects to HTML, exactly like the award form."""
    drive(client, monkeypatch, lambda f: FakeResponse("<html>", "text/html"))
    results = download_topics(agency="NASA", client=client)
    assert len(results) == 0
    assert not results


def test_contains_is_applied_locally(client, monkeypatch):
    drive(client, monkeypatch, lambda f: FakeResponse(make_csv([row()]), "text/csv"))
    assert len(download_topics(contains="radar", client=client)) == 1
    assert len(download_topics(contains="zebrafish", client=client)) == 0


def test_agency_filter_reaches_the_form(client, monkeypatch):
    calls = drive(client, monkeypatch,
                  lambda f: FakeResponse(make_csv([row()]), "text/csv"))
    download_topics(agency="NIH", client=client)
    assert "agency[NIH]" in calls[0]


# -- results object -------------------------------------------------------


@pytest.fixture
def results(client, monkeypatch):
    rows = [
        row(),
        row(**{"Topic Number": "NSF-QT1", "Agency": "NSF", "Branch": "",
               "Topic Title": "Quantum Algorithms",
               "Close Date": "October 15, 2026"}),
        row(**{"Topic Number": "OLD-1", "Solicitation Status": "Closed",
               "Close Date": "January 5, 2024"}),
    ]
    drive(client, monkeypatch, lambda f: FakeResponse(make_csv(rows), "text/csv"))
    return download_topics(client=client)


def test_results_behave_like_a_sequence(results):
    assert len(results) == 3
    assert results[0] in list(results)
    assert bool(results)


def test_open_only(results):
    assert len(results.open_only()) == 2


def test_filter_by_agency_and_contains(results):
    assert len(results.filter_by(agency="NSF")) == 1
    assert len(results.filter_by(contains="quantum")) == 1


def test_filter_by_close_window(results):
    assert len(results.filter_by(closes_after="2026-01-01")) == 2
    assert len(results.filter_by(closes_before="2026-09-30")) == 2


def test_closing_soon_is_ordered_and_open_only(results):
    soon = results.closing_soon()
    assert [t.close_date for t in soon] == ["2026-09-23", "2026-10-15"]


def test_by_agency_groups(results):
    assert set(results.by_agency()) == {"DOD", "NSF"}


def test_exports(results, tmp_path):
    jsonl = results.to_jsonl(tmp_path / "t.jsonl")
    assert len(jsonl.read_text(encoding="utf-8").strip().splitlines()) == 3
    csv_path = results.to_csv(tmp_path / "t.csv")
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
    assert len(rows) == 3
    assert "raw" not in rows[0]
    assert "raw" not in results.to_dicts()[0]


def test_summary(results):
    assert "3 topics" in results.summary()
    assert "2 open" in results.summary()


def test_public_api_exports_topics():
    for name in ("Topic", "TopicResults", "download_topics"):
        assert name in gs.__all__
        assert hasattr(gs, name)


def test_empty_topics_report_clearly(client, monkeypatch):
    drive(client, monkeypatch, lambda f: FakeResponse("<html>", "text/html"))
    results = download_topics(agency="NASA", client=client)

    assert not results
    assert results.first is None
    assert "No topics matched" in results.summary()
    with pytest.raises(IndexError, match="no topics matched"):
        results[0]


def test_topic_results_first_returns_a_topic(results):
    assert results.first is results[0]
