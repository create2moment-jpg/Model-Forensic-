# ADR-0010: Persistence at scale — summaries, WAL, server-side drafts

Status: accepted

## Context
A team accumulates hundreds of studies, finished and half-finished work, jobs and investigations. The first version
stored them as JSON documents in SQLite (right engine) but listed studies by parsing every full study (timelines,
comparisons grow without bound), had no paging or search, ran SQLite in rollback-journal mode (a writer blocks
readers on a team server), and kept drafts in the browser's localStorage (lost on another machine or browser,
invisible to the server, only two forms).

## Options considered
1. **Move to Postgres + an ORM.** Rejected for now: one more service to run and back up for a tool that is used by
   one person or a small team; SQLite in WAL mode handles thousands of reads per second and tens of thousands of
   documents. The `DocumentStore` port keeps a later move a contained change (one adapter).
2. **Normalised tables per entity.** Rejected: domain models evolve quickly (schema-versioned documents with
   migrations on read already work); lists need only a few fields.
3. **Documents + a summary column + WAL** (chosen).

## Decision
* Every document may carry a **summary** (a small JSON object written with it). Lists, filters (`json_extract`),
  text search and paging read summaries only, with the total (`X-Total-Count`). Stores created before summaries
  existed get the column added and are **backfilled lazily, once** per kind; unreadable old documents still list.
* SQLite runs in **WAL** mode with `synchronous=NORMAL` and a busy timeout; all access goes through one lock per
  connection. Lists that were unbounded (investigations) read only what they show.
* **Drafts are server objects** (`/drafts`): per user, a `kind`, a `title`, the in-app `route` that resumes them
  and the form `payload` (≤ 256 KiB); autosaved by the UI (`useServerDraft`, debounced, flushed on leave), pruned
  after 90 days and capped at 200 per user. Any number can coexist; each is resumable by link (`?draft=<id>`).
  Drafts from the old localStorage are moved to the server once.
* **Continue where you left off** (`/work`, Command center): your drafts, open studies with their next step, jobs
  that did not finish (*Run again*), knowledge waiting for review.

## Consequences
+ Measured: 600 studies with large timelines list in well under 0.3 s; 3 writers + 3 readers in parallel without
  errors (`tests/unit/test_persistence_scale.py`); drafts survive reloads and other browsers (e2e journey 17).
+ Half-finished work is never only in a browser tab.
− Summaries duplicate a few fields; they are rewritten with every save, so they cannot drift.
− Very large teams (many concurrent writers on one server) would eventually want Postgres — the port is ready.
