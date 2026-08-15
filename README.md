# sbirgrantsearch

A Python library for downloading US government innovation-funding data —
SBIR/STTR **awards** and open **solicitation topics** — into one common
schema, filtered the way you ask for it.

You say *what* you want. The library works out *how* to get it, falling back
across three transports — the JSON API, the site's filtered CSV export, and
the full bulk download — until one answers. The records are identical either
way, same fields and same `record_id`, so which one served your query is an
operational detail rather than something your code handles.

```python
import sbirgrantsearch as gs

awards = gs.download(agency="NASA", state="CA", years=2023, min_amount=500_000)

print(awards.summary())
# 31 awards | 28 companies | $29,650,957 | 2023-2023 | via search

awards.to_csv("nasa_ca.csv")
```

Stdlib only — no dependencies.

## Install

```bash
pip install sbirgrantsearch
```

Or from source:

```bash
pip install git+https://github.com/AnirudhJM24/SBIR-grant-search.git
```

## A quick tour

```bash
python examples/demo.py 2023
```

Every number below is fetched live — nothing is canned. One request pulls
the year; the rest is local filtering.

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
  5,998     3,571       $4.4B           $284.5K        search

▌ 2. Where the money goes
──────────────────────────────────────────────────────────────────────────
  >>> awards.by_agency()  # via record.agency_name

  Department of Defense          ██████████████████████████     $2.2B
  Health and Human Services      █████████████████░░░░░░░░░     $1.5B
  Department of Energy           ███░░░░░░░░░░░░░░░░░░░░░░░   $289.9M
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
  Phase II over $1M                  1,835         $3.3B
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
  LUNA LABS USA LLC                 M6JVSYRQRYM9       27      $18.4M

▌ 5. Potential university spinouts
  STTR awards name their research partner: a company commercialising
  university work.
──────────────────────────────────────────────────────────────────────────
  >>> awards.filter_by(program="STTR", require_ri=True)

  1,040 of 5,998 awards name a university

  company                         state  research partner
  ·······················································
  EOS ENERGETICS, INC.            CO     Battelle Memorial Institute
  M4 ENGINEERING, INC.            CA     Mississippi State University
  GLINT PHOTONICS, INC.           CA     UNC Charlotte
  BEAM ENGINEERING FOR ADVANCED   FL     Rochester Institute of Technol
  AWAREABILITY TECHNOLOGIES, LLC  OH     The Ohio State University
  CALIOLA ENGINEERING, LLC        CO     University of Southern Califor

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
  Open solicitations — the forward-looking half of the picture.
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

  → awards over $1M      demo_awards.csv        1,843 rows
  → open topics          demo_topics.csv          337 rows

  also: .to_jsonl() · .to_dicts() · .to_pandas()
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

**When nothing matches**, the result is empty rather than an error, and it
says what was asked for:

```python
d = gs.download(agency="NASA", state="WY", years=2025)
d.summary()   # "No awards matched. Query: agency=NASA, state=WY, years=2025, via search"
bool(d)       # False
d.first       # None
d[0]          # IndexError, naming the filters that produced no rows
```

`d[0]` still raises — indexing an empty sequence is an `IndexError` in
Python, and returning `None` there would hide a no-match until something
downstream broke oddly. Use `d.first` when you have not checked yet.

### Working with results

```python
d = gs.download(agency="DOE", years=2023)

len(d), d.transport            # 552, "search"
d[0].title                     # indexable and iterable
d.filter_by(state="CA")        # narrow further, no refetch
d.by_company()                 # {normalized name: [records]}, biggest first

d.first                        # first record, or None if nothing matched
d.query                        # "agency=DOE, years=2023, via search"

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

Three ways in, tried in order of how little they make you download:

| Transport | Status | How it works |
|---|---|---|
| `api` | **down** | Targeted JSON requests |
| `search` | **working** | The award-search form's filtered CSV export. Agency, sub-agency, year, state, phase and program all filter server-side. **Carries UEI.** Capped near 10,000 rows per query |
| `csv` | **working** | One ~367 MB bulk export, cached, then filtered locally. Always complete |

As of 2026-08-15 the SBIR.gov JSON API returns `{"message":"Forbidden"}` for
every request — their public APIs are under maintenance. `transport="auto"`
(the default) handles that by moving to `search`, so a narrow query costs one
small request instead of a 367 MB download.

```python
gs.download(...)                       # auto: api -> search -> csv
gs.download(..., transport="search")   # pin the filtered export
gs.download(..., transport="csv")      # pin the bulk file
gs.download(..., transport="api")      # pin the API; raises if it's down
```

Fallback happens twice over: once when picking a transport, and again if the
chosen one fails **during** a download. A probe only proves the endpoint
answers, not that a large request will succeed, so a transport that dies
mid-fetch is abandoned and the next one retries that year. Rows are
materialized before any are emitted, so a half-finished download never
leaks partial results.

Pin a transport when you need determinism — `auto` is for convenience, and a
pipeline that must be reproducible should fail loudly rather than silently
switch. A pinned transport never falls back, and only "this endpoint is
down" errors trigger a fallback at all: a `TypeError` from a bad filter
surfaces rather than being masked by quietly changing source.

**Two things to know about `search`.** It is an undocumented form endpoint,
not a public API, so it can change without notice — which is why the bulk CSV
stays as the guaranteed floor.

And its failure modes are quiet. Past ~10,000 rows it redirects to HTML
instead of erroring — *and answers an empty query exactly the same way*, with
no distinguishing message. A non-CSV response is therefore ambiguous, so the
adapter halves the agency set to tell the two apart: a genuinely oversized
slice returns CSV once split, an empty one stays empty. Detection is by
content type; a 200 alone means nothing here.

Probe results are cached for 5 minutes, so several `download()` calls cost
one round trip.

## Topics

Awards are what *was* funded; **topics** are what agencies are asking for now.
Same mechanism, separate schema — a topic has no recipient and no award
amount, so it is a `Topic`, not a `Record`.

```python
open_topics = gs.download_topics()                      # defaults to open
gs.download_topics(agency="NSF", keywords="quantum")
gs.download_topics(agency="NIH", closes_before="2026-12-31")
gs.download_topics(status="closed", years=2024)         # the archive
```

```python
t = gs.download_topics()
t.summary()          # "337 topics | 337 open | 3 agencies"
t.closing_soon(10)   # nearest close dates first, open only
t.by_agency()        # {"NSF": [...], "DOD": [...]}
t.filter_by(contains="autonomy")
t.to_csv("topics.csv")
```

`keywords=` is run server-side; `contains=` is a local substring filter
applied afterwards. From the CLI:

```bash
sbirgrantsearch topics --agency NSF --keywords quantum
sbirgrantsearch topics --closes-before 2026-12-31 --out topics.csv
```

Only agencies with live solicitations appear in open topics — at the time of
writing that is NSF, DoD and HHS, not all 44.

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

`Record` (`sbirgrantsearch/models.py`) is the common schema every source
normalizes into:

- `abstract` is the source text; **`abstract_clean`** is the search-facing
  version (HTML stripped, `"DESCRIPTION (provided by applicant):"`-style
  headers removed, whitespace collapsed). Retrieval indexes `search_text`
  (title + clean abstract + company).
- **`uei`** is the government company identifier. The `search` transport
  populates it on essentially every row; the bulk export has **no UEI
  column at all**, so it is `None` there. Prefer it for company
  aggregation when present.
- **`recipient_norm`** is the fallback company join key
  (`"AGILE DATA DECISIONS, INC."` → `"agile data decisions"`), and the only
  one available on the bulk path.
- **`ri_name`** is the STTR research-institution partner — the
  university-spinout signal, present on all STTRs (~16% of records).
- `raw` keeps the original source row, so a missed field is a local reparse
  rather than a refetch. Excluded from `to_csv()` and `to_dicts()`.

Rows whose abstract is missing, `"N/A"`, or under 100 characters are
dropped: they can't be retrieved and only distort IDF weights.

`record_id` is `sbir:{agency}:{contract}`, falling back to a content hash
when a row has neither a contract nor a tracking number. It excludes the
title so it survives the source rewording a project name, and it's keyed on
the **logical source** rather than the transport — which is what lets all
three paths produce interchangeable records.

Every row is an SBIR/STTR award to a small business, so "startups only"
holds by construction; `recipient_type` is always `company`.

## CLI

```bash
sbirgrantsearch ingest --years 2015-2025 --agency NASA --state CA
sbirgrantsearch profile data/raw --branch NIH
sbirgrantsearch topics --agency NSF --keywords quantum
sbirgrantsearch probe-api
```

Same filters as the library, as `--flags`.

## Contributing

Work happens on `feature/<name>` and `fix/<name>` branches and merges to
`main`; a merge to `main` publishes to PyPI when the version changes. See
[CONTRIBUTING.md](CONTRIBUTING.md).

## Adding a source

Subclass `SourceAdapter` (`sbirgrantsearch/ingest/base.py`) with `fetch_year`
and `normalize`, then register it in `sbirgrantsearch/ingest/__init__.py`. The
driver, JSONL layout, resume logic, dedupe and filtering are shared.

Set `name` to the transport slug and `source` to the logical dataset. Two
transports serving the same dataset share a `source`, which is what makes
them interchangeable.

`fetch_year` receives the active filter as a **hint** for pushdown — the
search adapter uses it to query one agency instead of all of them. The driver
reapplies the filter regardless, so ignoring the hint is always correct.

## Tests

```bash
python -m pytest tests -q    # 233 tests, no network required
```

Parser tests encode the actual junk in federal exports (`"171,433"`,
`"(500)"`, `"01/01/1900"`, `"September 23, 2026"`, double-escaped entities);
transport tests cover selection, pinning, id equality across paths, and the
empty-versus-capped ambiguity.

## License

MIT — see [LICENSE](LICENSE).

## Disclaimer

sbirgrantsearch is an independent open-source project and is not affiliated
with, endorsed by, or maintained by the U.S. Small Business Administration or
SBIR.gov. Award data is obtained from publicly available SBIR.gov APIs and
bulk downloads and remains subject to the applicable terms and policies of
the source. Users are responsible for complying with source-data
requirements.
