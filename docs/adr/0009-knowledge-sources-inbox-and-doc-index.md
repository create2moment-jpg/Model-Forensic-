# ADR-0009: Knowledge as files from many sources, one inbox, a rebuildable search index

Status: accepted

## Context
Knowledge about the training code is produced in many places: coding agents (Copilot) write notes and
documentation inside several codebases, people write entries by hand, the harness answers tasks. Without a managed
setup it scatters across repositories and is never read again. Model Forensic must read all of it, keep it reviewable,
and stay fast with thousands of files across many codebases.

## Options considered
1. **A database as the source of truth** (entries and documents in SQLite/Postgres, edited only through the UI).
   Rejected: agents work with files, reviews happen in git diffs, and documentation written next to the code would
   have to be copied in and would drift.
2. **A vector store / embeddings search.** Rejected for now: needs a model at index time, is not deterministic, and
   exact identifiers (`task_log_sigma`, `radar_head_width_loss`) are what people search for — lexical search with
   stemming finds them reliably. Can be added later as a second index over the same files.
3. **Files as the source of truth + a cache index** (chosen).

## Decision
* **Sources**: the inbox plus any number of folders, from `knowledge_sources:` in `model-forensic.yaml` or
  `MF_KNOWLEDGE_SOURCE_<NAME>=<folder>` (`${VAR}` and `~` expanded). Each reads knowledge YAML (`entries:`),
  documentation (Markdown / reStructuredText / text), or both.
* **One inbox** (`MF_KNOWLEDGE_INBOX`, default the knowledge folder): the only place agents write to
  (`<topic>-<date>.yaml`, `docs/<codebase>/<topic>.md`); `mf knowledge where` prints the instruction to give them.
* **Trust**: entries from agent-written sources (`trust: proposed`, the default) and harness output apply only after
  someone accepts them. Accepting or retiring an entry writes an override into the inbox's `knowledge.local.yaml` —
  source files are never modified.
* **Index**: SQLite FTS5 (`<data_dir>/docs.sqlite`), one row per Markdown section titled by its heading path,
  incremental by (mtime, size), BM25 ranking, all-words match with an any-word fallback. A cache: deleting it costs one
  re-index. Parsed knowledge files are cached by the same fingerprint.
* **Safety**: only indexed files are served, resolved inside their source folder (no traversal or symlink escape);
  documents render as React elements, never as injected HTML.

## Consequences
+ Knowledge accumulates in one reviewed place while documentation stays next to the code it describes.
+ Adding a codebase is one environment variable; nothing to migrate.
+ Measured: 1,500 documents indexed in about a second, an unchanged re-index in milliseconds, search well under
  0.5 s (`tests/unit/test_knowledge_sources.py::test_doc_index_scales`).
− Two copies of the text exist (file + index); the index is a cache and is refreshed at most every 30 s on reads
  (and on *Re-index now* / `mf docs index`).
− Lexical search does not find synonyms (σ vs sigma) unless the words overlap; the any-word fallback softens it.
