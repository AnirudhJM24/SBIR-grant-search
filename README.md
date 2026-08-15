# grantsearch

A Python library for downloading US government innovation-funding records
into one common schema, filtered the way you ask for them.

You say *what* you want. The library works out *how* to get it: the JSON API
when it's up, the bulk CSV export when it isn't. The records are identical
either way — same fields, same `record_id` — so the fallback is an
operational detail, not something your code handles.

```python
import grantsearch as gs

awards = gs.download(agency="NASA", state="CA", years=2023, min_amount=500_000)

print(awards.summary())
# 7 awards | 7 companies | $6,204,891 | 2023-2023 | via csv

awards.to_csv("nasa_ca.csv")
```

Stdlib only — no dependencies.

## Install

```bash
pip install -e .
```

## Downloading

`download()` takes filters as keyword arguments and returns a `Download`:

```python
gs.download(agency="DOE", branch="ARPA-E", min_amount=1_000_000)
gs.download(agency=["NASA", "NSF"], years=range(2020, 2026), state=["CA", "MA"])
gs.download(program="STTR", require_ri=True, contains="battery")
gs.download(years="2015-2025", phase="Phase II", start_after="2023-06-01")
```

Filters: `agency`, `branch`, `program`, `phase`, `state`, `fiscal_year`,
`year_min`/`year_max`, `start_after`/`start_before`, `end_after`/`end_before`,
`min_amount`/`max_amount`, `contains`, `require_ri`, `require_abstract`,
`min_abstract_chars`, `recipient_type`.

Singular and plural both work (`agency=` or `agencies=`), scalars are
accepted where a set is expected, and **an unknown filter name raises** —
a silently dropped filter returns too many records, which is the failure
mode hardest to notice.

`years` takes an int, a range, a list, or `"2015-2025"`. It defaults to
2015–2025.

### Working with results

```python
d = gs.download(agency="DOE", years=2023)

len(d), d.transport            # 156, "csv"
d[0].title                     # indexable and iterable
d.filter_by(state="CA")        # narrow further, no refetch
d.by_company()                 # {normalized name: [records]}, biggest first

d.to_jsonl("out.jsonl")        # archival format (keeps `raw`)
d.to_csv("out.csv")            # flat common schema
d.to_dicts()                   # plain dicts
d.to_pandas()                  # DataFrame (needs pandas)
```

### Streaming and persisting

```python
# Lazy — for large result sets you don't want in memory
for record in gs.stream(agency="DOD", years=range(2015, 2026)):
    ...

# Persist to one JSONL per year; reruns skip years already on disk
d = gs.download(agency="NASA", years="2015-2025", out_dir="data/raw")
d.stats.summary()
```

## Transports

| Transport | Status | How it works |
|---|---|---|
| `api` | **down** | Targeted JSON requests; agency filters push down server-side |
| `csv` | **working** | One ~367 MB bulk export, cached, then filtered locally |

As of 2026-08-15 the SBIR.gov JSON API returns `{"message":"Forbidden"}` for
every request — their public APIs are under maintenance. `transport="auto"`
(the default) handles this: it probes once, logs the fallback, and uses the
bulk CSV.

```python
gs.download(...)                     # auto: API if up, CSV if not
gs.download(..., transport="csv")    # pin the CSV
gs.download(..., transport="api")    # pin the API; raises if it's down
```

Pin a transport when you need determinism — `auto` is for convenience, and a
pipeline that must be reproducible should fail loudly rather than silently
switch. Probe results are cached for 5 minutes, so several `download()`
calls cost one round trip.

## Agency names

Records store a short **code** because it's the stable filter key; the
readable name is derived, so renaming one for display never means
re-downloading.

| Code | Name | | Code | Name |
|---|---|---|---|---|
| `NASA` | NASA | | `DHS` | Homeland Security |
| `HHS` | Health and Human Services | | `DOC` | Department of Commerce |
| `NSF` | National Science Foundation | | `DOT` | Department of Transportation |
| `DOE` | Department of Energy | | `EPA` | Environmental Protection Agency |
| `USDA` | Department of Agriculture | | `ED` | Department of Education |
| `DOD` | Department of Defense | | | |

Both spellings work on both sides: `agency="DOE"`,
`agency="Department of Energy"` and `agency="ARPA-E"` all resolve, as do
`branch="NIH"` and `branch="National Institutes of Health"`.

**NIH and ARPA-E are sub-agencies**, folded into HHS and DOE. Reach them with
`branch=`:

```python
gs.download(branch="NIH", years=2023)      # NIH SBIR/STTR only
gs.download(branch="ARPA-E", years=2023)
```

```python
record.agency        # "HHS"  — stored, filter on this
record.agency_name   # "Health and Human Services"
record.agency_label  # "Health and Human Services - National Institutes of Health"
```

## Schema

`Record` (`grantsearch/models.py`) is the common schema every source
normalizes into:

- `abstract` is the source text; **`abstract_clean`** is the search-facing
  version (HTML stripped, `"DESCRIPTION (provided by applicant):"`-style
  headers removed, whitespace collapsed). Retrieval indexes `search_text`
  (title + clean abstract + company).
- **`recipient_norm`** is the company join key
  (`"AGILE DATA DECISIONS, INC."` → `"agile data decisions"`). The bulk
  export has **no UEI**, so company aggregation keys on this.
- **`ri_name`** is the STTR research-institution partner — the
  university-spinout signal, present on all STTRs (~16% of records).
- `raw` keeps the original source row, so a missed field is a local reparse
  rather than a refetch. Excluded from `to_csv()` and `to_dicts()`.

Rows whose abstract is missing, `"N/A"`, or under 100 characters are
dropped: they can't be retrieved and only distort IDF weights.

`record_id` is `sbir:{agency}:{contract}`, falling back to a content hash
when a row has neither a contract nor a tracking number. It excludes the
title so it survives the source rewording a project name, and it's keyed on
the **logical source** rather than the transport — which is what lets the
API and CSV paths produce interchangeable records.

Every row is an SBIR/STTR award to a small business, so "startups only"
holds by construction; `recipient_type` is always `company`.

## CLI

```bash
grantsearch ingest --years 2015-2025 --agency NASA --state CA
grantsearch profile data/raw --branch NIH
grantsearch probe-api
```

Same filters as the library, as `--flags`.

## Adding a source

Subclass `SourceAdapter` (`grantsearch/ingest/base.py`) with `fetch_year`
and `normalize`, then register it in `grantsearch/ingest/__init__.py`. The
driver, JSONL layout, resume logic, dedupe and filtering are shared.

Set `name` to the transport slug and `source` to the logical dataset. Two
transports serving the same dataset share a `source`, which is what makes
them interchangeable.

`fetch_year` receives the active filter as a **hint** for pushdown — the API
adapter uses it to sweep one agency instead of eleven. The driver reapplies
the filter regardless, so ignoring the hint is always correct.

## Tests

```bash
python -m pytest tests -q    # 160 tests, no network required
```

Parser tests encode the actual junk in federal exports (`"171,433"`,
`"(500)"`, `"01/01/1900"`, double-escaped entities); fallback tests cover
transport selection, pinning, and id equality across paths.
