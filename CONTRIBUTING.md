# Contributing to the Mapsee event aggregator

The most useful contributions add reliable public event supply without working
around a publisher's terms, robots policy, authentication, or rate limits.

## Request a source

Open a **Request an event source** issue. Include the publisher's public event
page or feed, its city/country, the best category, and any terms or permission
context. A source is useful only when it returns real future events.

## Add a source

Most additions are config changes:

1. Choose the matching config (`ics_sources.json`, `localist_sources.json`,
   `opendata_sources.json`, `ods_sources.json`, or another existing adapter).
2. Run `python catalog_curate.py verify <candidate-file>`.
3. Confirm that the production User-Agent receives future events and that the
   source URL is preserved on each normalized event.
4. Run the same checks as CI:

   ```bash
   python test_categories.py
   python test_ingest_categories.py
   python test_link_series.py
   python test_health_check.py
   python test_ingest_places.py
   python -m compileall -q .
   ```
5. Open a focused pull request describing the feed, region, category, evidence,
   and any terms review.

Do not commit API keys, downloaded event stores, local caches, contact lists, or
credentials. Do not add sources that require bypassing access controls.

## Add an adapter

Copy the closest existing adapter and preserve the common contract documented
in the README: `--config`, `--store`, `_entries()`, append-only store writes,
credential-free no-op behavior, honest identification, normalization, and a
cost-appropriate workflow job. Add focused tests for parsing and category
mapping.

## Review standard

A contribution should be reproducible, respectful of the source, idempotent,
and observable when it fails. A larger catalog is not an improvement if it
silently imports nothing, duplicates events, misclassifies categories, or
depends on permission the publisher has not granted.
