# Changelog

## 0.2.2

### Changed

- Rewrote the README in a reference-documentation register. Filters are now
  a table rather than a prose list, sections are ordered by what a reader
  needs first, and the commentary that explained design decisions to a
  reviewer has been removed or reduced to a factual note.
- Removed all em dashes and en dashes from the README and from the demo
  output embedded in it.

## 0.2.1

### Changed

- Rewrote `examples/demo.py` as a formatted tour: a funding-by-agency bar
  chart, aligned tables, compact currency, and the call that produced each
  block shown above it. Colour is used only when stdout is a terminal, so
  piping the output gives clean text.
- The demo's output is now embedded in the README, so the project shows
  what it does before asking anyone to install it.
- The demo forces UTF-8 on stdout. Windows consoles default to cp1252 and
  cannot encode box-drawing characters, so it crashed there on the first
  line.

## 0.2.0

### Added

- **Transport failover now covers the whole fetch, not just the choice of
  transport.** Selection already tried `api → search → csv`, but that
  decision was made from a probe and never revisited: a transport that
  passed its probe and then failed partway through a download raised
  instead of falling back. It now moves down the chain and retries the
  year, and `normalize()` follows the switch so rows are read with the
  column map of whichever transport produced them.
- Rows are materialized before any are emitted, so a transport that dies
  halfway through never leaves partial results behind for the next one to
  duplicate. The last transport in the chain streams instead, since the
  bulk file is too large to hold.

### Notes

- A pinned transport still raises rather than falling back — a pipeline
  that named a source should fail loudly, not quietly return data of a
  different shape (the bulk file has no UEI).
- Only "this endpoint is down" errors trigger a fallback. A `TypeError`
  from a bad filter surfaces instead of being masked.

## 0.1.1

### Changed

- An empty result now reports itself. `summary()` leads with "No awards
  matched" and names the filters instead of printing zeros, and indexing
  an empty result raises an `IndexError` that says which filters produced
  no rows. Indexing still raises: an empty sequence has no element 0, and
  returning `None` would hide a no-match until something downstream broke
  oddly.
- Added `.first` (the first record, or `None`), `.query`, and
  `RecordFilter.describe()`. `TopicResults` gets the same treatment.

### Internal

- Releases now publish from the version in `pyproject.toml` rather than
  from a manually pushed tag, and the workflow tags the commit and opens a
  GitHub Release afterwards.

## 0.1.0

### Added

- **Solicitation topics** (`gs.download_topics()`). Awards are what was
  funded; topics are what agencies are asking for now. Own `Topic` schema
  and `TopicResults` container — a topic has no recipient or award amount,
  so reusing the award schema would leave half its fields meaningless.
  Filter by agency, sub-agency, year, status, program, phase, keywords and
  close-date window. `sbirgrantsearch topics` on the CLI.
- **`search` transport** — the award-search form's filtered CSV export.
  A targeted query now costs one small request instead of a 367 MB bulk
  download, and agency, sub-agency, year, state, phase and program all
  filter server-side. Transport preference is `api → search → csv`.
- **`uei`** on `Record`, populated on essentially every row from the
  `search` transport. The bulk export has no UEI column at all, so company
  aggregation no longer has to key on a normalized name. Also added
  `duns`, `country`, `topic_code` and `research_keywords`.

### Fixed

- **An agency filter returned a fraction of its awards.** Filtering by a
  department sent the parent checkbox plus every child, assuming the form
  would union them. It does not: a parent together with a child narrows to
  the child, so `agency="DOE"` returned ARPA-E's 36 FY2023 awards instead
  of DOE's 583 — a silent 97% loss. A filter now sends exactly one
  checkbox.
- **An empty result was reported as an over-cap failure.** The search
  endpoint answers a too-large query and an empty one identically, so an
  ordinary empty slice raised "still exceeds the row cap". The two are now
  told apart by splitting the query rather than by reading the response.
- **`parse_date` could not read spelled-out months.** The topics export
  writes `"September 23, 2026"`, which parsed as `None`.

## 0.0.1

Initial release. SBIR/STTR award download with a common schema, composable
filters, and automatic fallback from the JSON API to the bulk CSV export.
