# sbirgrantsearch

Download US government innovation funding data into a single normalized
schema. Covers SBIR/STTR awards and open solicitation topics from SBIR.gov.

Requires Python 3.10 or later. No runtime dependencies.

```python
import sbirgrantsearch as gs

awards = gs.download(agency="NASA", state="CA", years=2023, min_amount=500_000)

print(awards.summary())
# 31 awards | 28 companies | $29,650,957 | 2023-2023 | via search

awards.to_csv("nasa_ca.csv")
```

The library selects a data transport at runtime. It prefers the JSON API,
falls back to the site's filtered CSV export, and falls back again to the
full bulk download. All three produce identical records, including
`record_id`, so callers do not need to know which one served a query.

## Install

```bash
pip install sbirgrantsearch
```

From source:

```bash
pip install git+https://github.com/AnirudhJM24/SBIR-grant-search.git
```

## Example output

`examples/demo.py` runs a short tour against live data. Every figure is
fetched at runtime.

```bash
python examples/demo.py 2023
```

```
╭────────────────────────────────────────────────────────────────────────╮
│ sbirgrantsearch · FY2023                                               │
│ US innovation funding: who got paid, and what's being asked for next   │
╰────────────────────────────────────────────────────────────────────────╯

▌ 1. One call gets the year
  The transport picks itself: JSON API, else filtered CSV, else bulk.
──────────────────────────────────────────────────────────────────────────
  >>> awards = gs.download(years=2023)

  awards    companies   total funding   median award   transport
  ······························································
  5,998     3,571       $4.4B           $283.6K        search

▌ 2. Where the money goes
──────────────────────────────────────────────────────────────────────────
  >>> awards.by_agency()  # via record.agency_name

  Department of Defense          ██████████████████████████     $2.2B
  Health and Human Services      █████████████████░░░░░░░░░     $1.5B
  Department of Energy           ███░░░░░░░░░░░░░░░░░░░░░░░   $289.0M
  National Science Foundation    ███░░░░░░░░░░░░░░░░░░░░░░░   $214.2M
  NASA                           ██░░░░░░░░░░░░░░░░░░░░░░░░   $182.5M
  Department of Agriculture      ░░░░░░░░░░░░░░░░░░░░░░░░░░    $39.5M
  Department of Commerce         ░░░░░░░░░░░░░░░░░░░░░░░░░░    $19.2M

▌ 3. Filters compose, and narrow without refetching
──────────────────────────────────────────────────────────────────────────
  >>> awards.filter_by(agency="NASA", state="CA")

  filter                            awards       funding
  ······················································
  NASA, California                     108        $41.4M
  NIH (sub-agency of HHS)            1,329         $1.4B
  DARPA                                115       $179.1M
  Phase II over $1M                  1,833         $3.3B
  "quantum" anywhere                   142        $76.0M
  "autonomous" anywhere                302       $219.7M

▌ 4. Roll up to companies
  Keyed on UEI where present; the bulk export has none, search does.
──────────────────────────────────────────────────────────────────────────
  >>> awards.by_company()

  ✓ 5,998/5,998 records carry a UEI

  company                           uei            awards       total
  ···································································
  PHYSICAL SCIENCES INC.            RMG1AZ1ZH8Q7       64      $38.7M
  TRITON SYSTEMS, INC.              DA91DUWSMSQ7       46      $29.2M
  CFD Research Corporation          V3KCP1HNFM33       37      $24.4M
  SPECTRAL ENERGIES LLC             C3DAVH4VJDG3       36      $16.5M
  TDA RESEARCH, INC.                MK5ANJVWVZK7       29      $16.9M
  LYNNTECH INC.                     PMVAL8J63516       27      $13.3M

▌ 5. Potential university spinouts
  STTR awards name their research partner: a company commercialising
  university work.
──────────────────────────────────────────────────────────────────────────
  >>> awards.filter_by(program="STTR", require_ri=True)

  1,039 of 5,998 awards name a university

  company                         state  research partner
  ·······················································
  CellDrop Inc                    WY     Texas A&M AgriLife Research
  SILICON ASSURANCE LLC           FL     UNIVERSITY OF CENTRAL FLORIDA
  Robotics 88, Inc.               MA     Carnegie-Mellon University
  ORION THERAPEUTICS INC.         TN     The University of Tennessee, M
  Heliobiosys, Inc                CA     The University of Central Flor
  Revterra Corporation            TX     University of Wisconsin-Madiso

▌ 6. Abstracts arrive clean, ready to index
──────────────────────────────────────────────────────────────────────────
  >>> record.abstract_clean  # boilerplate and markup stripped

  Cybernetic Training for Autonomous Robots - Human Augmentation via Gen
  SARCOS GROUP LC · Department of Defense - USAF · $38.6M

  The Sarcos Cybernetic Training for Autonomous Robots - Human
  Augmentation via Generalizable Mobile Autonomous Robot Dexterity (C-H)
  program is a second Air Force SBIR Phase II. C-H advances Sarcos' vision
  of leveraging the expertise of competent task perf

▌ 7. What agencies want funded next
  Open solicitations: the forward-looking half of the picture.
──────────────────────────────────────────────────────────────────────────
  >>> gs.download_topics(status="open")

  337 topics | 337 open | 3 agencies

  closes      agency   topic
  ··························
  2026-09-21  HHS      Development of therapeutic or preventative techn
  2026-09-21  HHS      Development of devices, diagnostic technologies,
  2026-09-23  DOD      Collaborative Distributed Swarm Radar
  2026-09-23  DOD      Signal Classification and Anomaly Detection in C
  2026-09-23  DOD      Automated Post-Mission De-Brief and Re-Planning
  2026-09-23  DOD      DIRECT TO PHASE II: Intelligent Data Analysis of

▌ 8. Take it with you
──────────────────────────────────────────────────────────────────────────
  >>> awards.to_csv("awards.csv"); topics.to_csv("topics.csv")

  → awards over $1M      demo_awards.csv        1,841 rows
  → open topics          demo_topics.csv          337 rows

  also: .to_jsonl() · .to_dicts() · .to_pandas()
```

## Downloading awards

`download()` accepts filters as keyword arguments and returns a `Download`.

```python
gs.download(agency="DOE", branch="ARPA-E", min_amount=1_000_000)
gs.download(agency=["NASA", "NSF"], years=range(2020, 2026), state=["CA", "MA"])
gs.download(program="STTR", require_ri=True, contains="battery")
gs.download(years="2015-2025", phase="Phase II", start_after="2023-06-01")
```

Available filters:

| Filter | Accepts |
|---|---|
| `agency` | Code or full name, e.g. `"DOE"`, `"Department of Energy"` |
| `branch` | Sub-agency, e.g. `"NIH"`, `"ARPA-E"`, `"DARPA"` |
| `program` | `"SBIR"` or `"STTR"` |
| `phase` | `"Phase I"` or `"Phase II"` |
| `state` | Two-letter code or full name |
| `fiscal_year`, `year_min`, `year_max` | Integers |
| `start_after`, `start_before` | Dates, in most common formats |
| `end_after`, `end_before` | Dates |
| `min_amount`, `max_amount` | Numbers, or formatted strings such as `"1,000,000"` |
| `contains` | Substring, matched against title, abstract and company |
| `require_ri` | Boolean. Only awards naming a research institution |
| `require_abstract`, `min_abstract_chars` | Quality gates |
| `recipient_type` | `"company"`, `"university"`, and similar |

Singular and plural names are both accepted (`agency=` or `agencies=`), and
scalars are accepted where a set is expected. An unrecognized filter name
raises `TypeError` rather than being ignored, because an ignored filter
returns more records than requested without any visible error.

`years` accepts an integer, a range, a list, or a string such as
`"2015-2025"`. It defaults to 2015 through 2025.

### Working with results

```python
d = gs.download(agency="DOE", years=2023)

len(d), d.transport            # 552, "search"
d[0].title                     # indexable and iterable
d.first                        # first record, or None if nothing matched
d.query                        # "agency=DOE, years=2023, via search"

d.filter_by(state="CA")        # narrows in place, no refetch
d.by_company()                 # {normalized name: [records]}, largest first

d.to_jsonl("out.jsonl")        # archival format, retains `raw`
d.to_csv("out.csv")            # flat common schema
d.to_dicts()                   # list of dicts
d.to_pandas()                  # DataFrame, requires pandas
```

### Empty results

A query that matches nothing returns an empty result rather than raising,
and reports the filters that produced it.

```python
d = gs.download(agency="NASA", state="WY", years=2025)

d.summary()   # "No awards matched. Query: agency=NASA, state=WY, years=2025, via search"
bool(d)       # False
d.first       # None
d[0]          # IndexError naming the filters that matched nothing
```

`d[0]` raises because indexing an empty sequence raises in Python. Use
`d.first` when the result has not been checked.

### Streaming and persisting

```python
# Lazy, for result sets too large to hold in memory
for record in gs.stream(agency="DOD", years=range(2015, 2026)):
    ...

# Writes one JSONL file per year; repeat runs skip years already on disk
d = gs.download(agency="NASA", years="2015-2025", out_dir="data/raw")
d.stats.summary()
```

## Transports

| Transport | Status | Description |
|---|---|---|
| `api` | Unavailable | Targeted JSON requests |
| `search` | Working | Filtered CSV export from the award search form. Filters agency, sub-agency, year, state, phase and program server-side. Includes UEI. Capped near 10,000 rows per query |
| `csv` | Working | Single bulk export of roughly 367 MB, cached locally, then filtered in process. Always complete |

As of 2026-08-15 the SBIR.gov JSON API returns `{"message":"Forbidden"}` for
all requests, as its public APIs are under maintenance. The default
`transport="auto"` handles this by using `search`, so a narrow query costs
one small request rather than a full bulk download.

```python
gs.download(...)                       # auto: api, then search, then csv
gs.download(..., transport="search")   # pin the filtered export
gs.download(..., transport="csv")      # pin the bulk file
gs.download(..., transport="api")      # pin the API; raises if unavailable
```

Fallback applies at two points: when selecting a transport, and again if the
selected transport fails during a download. A successful probe only shows
that the endpoint responds, not that a large request will complete, so a
transport that fails mid-fetch is abandoned and the next one retries that
year. Rows are collected before any are emitted, so an interrupted download
does not produce partial results.

Pin a transport for reproducibility. A pinned transport never falls back.
Only errors indicating an unavailable endpoint trigger a fallback, so a
`TypeError` from an invalid filter is raised rather than masked by a change
of source.

Two notes on `search`:

1. It is an undocumented form endpoint rather than a public API, and may
   change without notice. The bulk CSV therefore remains as a guaranteed
   fallback.
2. Its failure modes are silent. Beyond roughly 10,000 rows it redirects to
   HTML instead of returning an error, and it answers an empty query the
   same way, with no distinguishing message. A non-CSV response is
   therefore ambiguous, so the adapter halves the agency set to
   distinguish the two cases: an oversized slice returns CSV once split,
   while an empty one stays empty. Detection is by content type, since a
   200 status alone is not meaningful here.

Probe results are cached for five minutes, so several `download()` calls
share one round trip.

## Solicitation topics

Awards describe completed funding. Topics describe what agencies are
soliciting now. A topic has no recipient and no award amount, so it uses a
separate `Topic` schema rather than `Record`.

```python
gs.download_topics()                                    # open topics
gs.download_topics(agency="NSF", keywords="quantum")
gs.download_topics(agency="NIH", closes_before="2026-12-31")
gs.download_topics(status="closed", years=2024)         # the archive
```

```python
t = gs.download_topics()

t.summary()          # "337 topics | 337 open | 3 agencies"
t.closing_soon(10)   # open topics, nearest close date first
t.by_agency()        # {"NSF": [...], "DOD": [...]}
t.filter_by(contains="autonomy")
t.to_csv("topics.csv")
```

`keywords=` is evaluated server-side. `contains=` is a substring filter
applied locally after download.

Only agencies with active solicitations appear in open topics. At the time
of writing that is NSF, DoD and HHS, not all 44 available agency values.

## Agency names

Records store a short agency code, which is the stable filter key. The
readable name is derived from it, so display names can change without
re-downloading anything.

| Code | Name | | Code | Name |
|---|---|---|---|---|
| `NASA` | NASA | | `DHS` | Homeland Security |
| `HHS` | Health and Human Services | | `DOC` | Department of Commerce |
| `NSF` | National Science Foundation | | `DOT` | Department of Transportation |
| `DOE` | Department of Energy | | `EPA` | Environmental Protection Agency |
| `USDA` | Department of Agriculture | | `ED` | Department of Education |
| `DOD` | Department of Defense | | | |

Filters accept either spelling on either side. `agency="DOE"`,
`agency="Department of Energy"` and `agency="ARPA-E"` all resolve, as do
`branch="NIH"` and `branch="National Institutes of Health"`.

NIH and ARPA-E are sub-agencies of HHS and DOE respectively. Use `branch=`
to select them.

```python
gs.download(branch="NIH", years=2023)      # NIH SBIR/STTR awards only
gs.download(branch="ARPA-E", years=2023)
```

```python
record.agency        # "HHS", the stored value and the filter key
record.agency_name   # "Health and Human Services"
record.agency_label  # "Health and Human Services - National Institutes of Health"
```

## Schema

`Record` in `sbirgrantsearch/models.py` is the schema every source
normalizes into. Fields that need explanation:

- `abstract` holds the source text. `abstract_clean` is the search-facing
  version, with HTML stripped, leading section headers such as
  `"DESCRIPTION (provided by applicant):"` removed, and whitespace
  collapsed. `search_text` concatenates title, clean abstract and company.
- `uei` is the government-assigned company identifier. The `search`
  transport populates it on effectively every row. The bulk export contains
  no UEI column, so the value is `None` on that path. Prefer it for company
  aggregation where available.
- `recipient_norm` is the fallback company join key, reducing
  `"AGILE DATA DECISIONS, INC."` to `"agile data decisions"`. It is the only
  join key available on the bulk path.
- `ri_name` is the STTR research institution partner, present on all STTR
  awards, which is roughly 16 percent of records.
- `raw` retains the original source row, so recovering a field that was not
  mapped is a local reparse rather than a refetch. It is excluded from
  `to_csv()` and `to_dicts()`.

Rows with a missing abstract, an abstract of `"N/A"`, or fewer than 100
characters are dropped. They cannot be retrieved by text search and they
distort IDF weights.

`record_id` is `sbir:{agency}:{contract}`, falling back to a content hash
when a row has neither a contract nor a tracking number. It excludes the
title, so it is stable if the source rewords a project name, and it is
keyed on the logical source rather than the transport, which is what allows
all three transports to produce interchangeable records.

Every row is an SBIR or STTR award to a small business, so `recipient_type`
is always `company`.

## Command line

```bash
sbirgrantsearch ingest --years 2015-2025 --agency NASA --state CA
sbirgrantsearch profile data/raw --branch NIH
sbirgrantsearch topics --agency NSF --keywords quantum
sbirgrantsearch probe-api
```

The CLI exposes the same filters as the library, as flags.

## Adding a source

Subclass `SourceAdapter` in `sbirgrantsearch/ingest/base.py`, implementing
`fetch_year` and `normalize`, then register it in
`sbirgrantsearch/ingest/__init__.py`. The ingest driver, JSONL layout,
resume logic, deduplication and filtering are shared.

Set `name` to the transport slug and `source` to the logical dataset.
Transports serving the same dataset share a `source` value, which is what
makes their records interchangeable.

`fetch_year` receives the active filter as a pushdown hint. The search
adapter uses it to query one agency rather than all of them. The driver
reapplies the filter afterwards, so ignoring the hint is always correct.

## Tests

```bash
python -m pytest tests -q    # 233 tests, no network access required
```

Parser tests use values taken from real federal exports, including
`"171,433"`, `"(500)"`, `"01/01/1900"`, `"September 23, 2026"` and
double-escaped HTML entities. Transport tests cover selection, pinning,
`record_id` equality across paths, and the ambiguity between an empty
result and one over the row cap.

## Contributing

Work is done on `feature/<name>` and `fix/<name>` branches and merged to
`main`. A merge to `main` publishes to PyPI when the version has changed.
See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT. See [LICENSE](LICENSE).

## Disclaimer

sbirgrantsearch is an independent open-source project and is not affiliated
with, endorsed by, or maintained by the U.S. Small Business Administration or
SBIR.gov. Award data is obtained from publicly available SBIR.gov APIs and
bulk downloads and remains subject to the applicable terms and policies of
the source. Users are responsible for complying with source-data
requirements.
