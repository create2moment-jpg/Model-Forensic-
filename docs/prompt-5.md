# Prompt 5 — progress and final report

Principle: every screen answers *is it running, what is it doing, how much is done, what is blocking it, and what
will happen next?*

## Root causes traced first (§21)

| Symptom | Root cause (traced in code) |
|---|---|
| Opening a run blocks for seconds | `RunDetail` waited on `GET /runs/{run}` = `svc.overview`: every metric series of the run **and its stages**, configs, the file listing and a baseline comparison, synchronously in one request |
| Warm-up wheel resets on a tab change | its state lived in component-local React state (`Runs.tsx`), not on the server |
| No overview of what is running | no application-wide task list; jobs, prefetch tasks and page loads were separate |
| Metric columns empty for Azure runs | the Azure mapper never fills `summary_metrics`; the UI hard-coded `NDS/mAP/AMOTA` (fixed in P5-3) |
| Run ID not visible in the table | the model has it; there was no column and the id was not in the full-text index |

## P5-1 Progressive run opening · application-scoped tasks · Run ID column

* `application/tasks.py` — `TaskManager`: one bounded pool for the whole application, deduplicated by
  (kind, subject) (a second request joins the running task), dependency-aware (`depends_on`; a failed input fails
  its dependants *with the reason*), per-kind concurrency, cache state (cold / warm), measured timings per kind
  (last 200, in the document store `task_stat`) for the Test Space.
* `defaults/tasks.yaml` — the master task configuration: per task kind the cost class (lightweight / medium /
  heavy / very_heavy), **why**, cacheability, dependencies, concurrency, Light Mode eligibility. Local reviewed
  overrides: `config/tasks.local.yaml` (git-ignored).
* `application/run_loader.py` + API:
  `GET /runs/{run}/light` (index and caches only), `POST /runs/{run}/load?mode=light|full&kinds=…`,
  `GET /runs/{run}/tasks`, `GET /runs/{run}/overview-cached` (never a backend call; `ready` per part),
  `GET /tasks/active` (run tasks + prefetch + jobs).
* UI: the run page renders its shell from `light` at once; a *Run loading* panel lists each part with state,
  elapsed time, cost class, cache state, why it is heavy and errors; Light / Full toggle (remembered); tabs ask for
  what they need (Metrics → `run.metrics`); a part not loaded yet shows which task fills it — never an empty view
  or a false zero. Header: a global task indicator on every page. Runs table: a **Run ID** column (mono,
  selectable, copy button, sortable, searchable — the id is in the full-text index). The warm-up wheel derives its
  state from the server's jobs (`/jobs` names the run), so it survives page changes and reloads.

Measured (demo pipeline run `xf-pipe-finetune`, every backend call delayed 0.5 s to imitate a remote workspace):

| | before | after |
|---|---|---|
| time to the run page shell (cold) | 4124 ms (blocked on everything) | **9 ms** (`/light`) |
| overview from what is loaded | — | 12 ms (`/overview-cached`) |
| Full Mode, all parts loaded | 4124 ms serial | 2150 ms, parts in parallel, each visible as it lands |
| warm (cached) | 11 ms | 7–11 ms |

Tests: `tests/unit/test_tasks.py` (join, dependencies, failed dependency, per-kind concurrency, cache state and
timings, master config + local override, the progressive API end to end, searchable Run ID, jobs naming their run);
e2e journeys 33 (progressive opening, Full Mode, state kept across a page change, global indicator) and 34 (Run ID
column copy / search).

## P5-2 Prefetch observability · structured events · stall detection · diagnosis

Root cause: the prefetch task had coarse states only (planned / running / stopped …), a per-file state without
"in flight", free-text events, no liveness signal and a progress bar computed from known sizes only — an unknown
size silently made the percentage wrong.

* **Phases** (`application/prefetch.py::PHASES`): `planned → manifesting → awaiting_selection → queued →
  downloading → validating → completed`, plus `stopping`, `stopped` (resumable), `cancelled`, `failed`. Every
  transition is an event with the time spent in the previous phase; `phase_times` sums them. Listing and cache
  re-validation now run in the worker, so *Start* answers at once and the UI watches *manifesting*.
* **Per-file states**: `pending · queued · downloading · validating · done · reused · partial · failed ·
  cancelled`, with bytes this session, attempts, start / end times. The store reports them through a new
  `on_stage` hook (`downloading` with the resume offset, `bytes`, `validating`, `retry`).
* **Heartbeat**: the worker beats every second; `alive` / `heartbeat_age_s` in the view; a stale heartbeat while
  running is diagnosed as `worker_not_alive`.
* **Structured events** (`kind`: phase, file, retry, stall, recovered, info, error; last 300 kept);
  `GET /prefetch/{id}/events?after=n` for a live log.
* **Stall detection**: no byte for `stall_after_s` (60 s default) while downloading → `stalled`, a `stall` event
  naming the files in flight; the first new byte clears it with a `recovered` event.
* **Diagnosis** (`diagnose`): only causes the evidence supports, each with evidence and a fix —
  `worker_not_alive`, `stalled`, `backend_transient` (retries, last error), `low_throughput` (vs earlier tasks),
  `per_file_latency` (many small files), `single_worker`, `disk_space`, `slow_listing`.
* **Honest progress**: `progress.fraction` only when every selected size is known; otherwise `determinate: false`,
  counts and bytes so far, and an indeterminate bar — never an invented percentage. `next` says in words what
  happens next.
* UI (Prefetch page): phase steps, live strip (heartbeat, phase and its duration, per-state counts, files in flight
  with bytes / attempts, retries, stall warning, *Next:*), a "Why it is slow or blocked" card, an events log with kinds.

Tests: `tests/unit/test_prefetch_observability.py` — phases and per-file states through a real download, unknown
sizes never get a percentage, a stalled stream is detected, named and cleared, diagnosis from evidence (each cause),
retries counted per file, API view + events endpoint; e2e journey 35.
