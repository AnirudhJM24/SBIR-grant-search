The weekly probe got usable data from `https://api.www.sbir.gov/public/api/awards`, so the maintenance window appears to be over.

`transport="auto"` will now prefer the API on its own — no code change is needed for the fallback to stop engaging.

**Verify before trusting it.** `sbir_api.py` has never run against a live response; its field names come from the documented contract, which is why `FIELD_ALIASES` exists. Before relying on the API path:

- [ ] Dump one raw row and check it against `FIELD_MAP` in `sbirgrantsearch/ingest/sbir_api.py`
- [ ] Confirm `record_id` matches the CSV path for the same award (`test_both_transports_share_one_logical_source` covers this)
- [ ] Compare a year's record count against the bulk CSV under the same filters
- [ ] Check pagination terminates — the API exposes no total, so the code stops on a short page

Opened automatically by `.github/workflows/probe-api.yml`.
