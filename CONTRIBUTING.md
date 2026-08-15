# Contributing

## Branching

Nothing lands on `main` directly. Every change goes on a branch:

```bash
git checkout main && git pull
git checkout -b feature/topic-keyword-search    # new capability
git checkout -b fix/empty-result-indexing       # bug fix
```

Only `feature/<name>` and `fix/<name>` are accepted — CI fails a pull
request whose branch is named anything else.

Open a pull request and merge it. **A merge to `main` publishes to PyPI**
if the version changed, so `main` is a release channel rather than a
workspace. That is the whole reason for the branch step: a PyPI version
number can never be reused or withdrawn, so nothing should reach `main`
without a reviewable moment first.

## Releasing

Releasing is a side effect of merging, not a separate procedure:

1. On your branch, bump the version in **both** `pyproject.toml` and
   `sbirgrantsearch/__init__.py`. A test enforces that they agree.
2. Add a section to `CHANGELOG.md` under the new version number. It
   becomes the GitHub Release notes verbatim.
3. Merge to `main`.

The publish workflow then reads the version, checks whether PyPI already
has it, and if not: runs the tests, builds, publishes, tags the commit
`v<version>`, and opens a GitHub Release with the artifacts attached.

It is idempotent. A merge that does not change the version publishes
nothing, and re-running the workflow cannot double-publish.

Pick the version with the usual rules: a fix is a patch bump, a new
capability is a minor bump. Anything that changes existing behaviour under
a caller's feet deserves a minor bump and a clear changelog note, since
this is still `0.x`.

## Tests

```bash
python -m pytest tests -q
```

They must pass without network access. Anything that would reach SBIR.gov
is faked — see `drive()` in `tests/test_sbir_search.py` for the pattern.
The publish workflow runs them before it uploads anything.

When fixing a bug, add the test that fails before the fix. Several of the
bugs in `CHANGELOG.md` were silent data loss that only surfaced by
cross-checking a query against the bulk corpus, so a regression test is
often the only thing standing between a wrong number and nobody noticing.

## Working with SBIR.gov

Two habits worth keeping:

- **A 200 response proves nothing.** The search and topics endpoints
  answer an oversized query and an empty one with the same redirect to
  HTML, and they never return an error for either. Check the content type,
  and treat an unexpected shape as ambiguous rather than as success.
- **Cross-check counts against the bulk export.** Every silent-data-loss
  bug so far was caught by comparing a filtered query against
  `data/cache/award_data.csv`, not by a test. Note that the bulk file lags
  for recent years, so a disagreement there can mean the bulk file is
  stale rather than the query being wrong.
