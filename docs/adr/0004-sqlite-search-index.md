# ADR-0004: SQLite + FTS5 (trigram) as the local search index

Status: accepted

## Context
Search must feel instant over thousands to millions of runs while Azure ML list APIs are slow and
throttled. Options: query Azure live; Elasticsearch/OpenSearch; Postgres; SQLite.

## Decision
A local SQLite index (WAL mode) with structured columns, tag/param/metric side tables with
composite indexes, and an FTS5 trigram table for fuzzy substring search. Backends are synced in the
background, incrementally.

## Consequences
+ Zero operations; sub-millisecond queries on the demo set; scales to millions of rows with indexes.
+ The `SearchIndex` port allows Postgres (pg_trgm) for shared deployments.
− Data can be stale between syncs; the UI shows sync time and refreshes running runs on access.
