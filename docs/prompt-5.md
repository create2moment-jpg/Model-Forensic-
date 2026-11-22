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

## P5-3 Parser / schema messages · metric columns repaired end to end

### Classified messages (`domain/messages.py`)

Root cause: readers reported problems as free strings (`"recognised as … but no values extracted (format drift?)"`,
`"unrecognised schema"`), so nobody could tell a harmless skip from a broken reader, and nothing said *how* a
file differs from what the reader expects.

`ParseMessage(severity, code, message, file, expected, observed, remediation)` with the severities
`info · recoverable_warning · unsupported_schema · parse_failure · missing_data · critical`
(`unsupported_schema`, `parse_failure` and `critical` are *blocking*). `expected` is the shape the reader looks for, `observed` a value-free shape of the file
(`shape_of`: types and keys only — no data leaks into a support bundle).

| Code | Severity | From |
|---|---|---|
| `eval.no_values` | unsupported_schema | `mf inspect`: a family recognised by place with no value extracted |
| `eval.unrecognised` | info | an evaluation JSON no reader knows (not used by any analysis) |
| `eval.unreadable` / `file.unreadable` / `parquet.unreadable` | parse_failure | corrupt / truncated files |
| `eval.too_large` / `json.too_large` / `parquet.too_large` / `inspect.file_limit` | recoverable_warning | limits |
| `parquet.schema` | unsupported_schema | per-sequence table with an unknown index / columns |
| `naming.unmapped` | recoverable_warning | metric names without a split |
| `metrics.none` / `metrics.empty` / `sequences.none` | missing_data | the evidence is absent — said, never a zero |
| `reader.unavailable` | critical | a reader dependency (pyarrow) is missing |

Surfaces: `mf inspect` (severity, expected / observed, fix), `mf inspect --json` (`messages`, `message_counts`),
`GET /runs/{run}/sequences` (`messages`), the Sequences tab (`ParseMessages` with an expected-vs-observed view).
`InspectReport.ok()` is now "no blocking message" (and ≤ 5 % unmapped names); `notes` stays as text for older
consumers.

### Metric columns (`application/metric_columns.py`)

Root cause, end to end: (1) the Azure ML job listing carries no metric values, so `summary_metrics` stayed empty
for every Azure run; (2) the Runs table picked columns from a hard-coded `NDS / mAP / AMOTA` list.

Repair:
* the values: every load of a run's histories (run page, warm-up, `run.summary` task) records final
  val / test values and `train/loss` in the index (`record_summary` → `index.update_summary`; a later listing
  keeps them);
* the choice: `POST /runs/metric-columns` ranks the keys the visible runs have (evaluation splits first, then
  coverage; a project profile's `metrics.headline` first) — no metric name in code;
* the check **MC-1** (per backend): how many runs have values, why the others do not (listing without metrics vs
  runs that logged none) and whether it can be filled;
* the fix: *Fill metric columns* (`POST /runs/summaries`) loads the missing histories as bounded `run.summary`
  tasks (heavy, concurrency 2), visible in the global task indicator; the table refreshes when they finish.

Smoke test `tests/unit/test_metric_columns.py`: a remote-like source whose listing and lookups carry no values →
empty columns + MC-1 diagnosis → fill → columns and values appear in `/runs`; ranking and project headline without
hard-coded names. e2e journey 36: every shown column is one the runs have. Tests for messages:
`tests/unit/test_parse_messages.py`.

## P5-4 Test Space · measured classification · versioned proposals

Root cause: task costs were declared by hand and never checked against measurements; nothing showed what a task
really costs on a given workspace, and nothing could change the configuration safely.

* `application/test_space.py` — **experiments**: a task kind on sample runs, `repetitions` × (**cold**: the run's
  and its stages' caches dropped via `svc.drop_cached`, **warm**: straight after), timing the same work functions
  the run page uses; run as one `test.experiment` task at a time (concurrency 1, visible in the global indicator);
  results stored apart from production timings (document kind `test_experiment`).
* **Statistics**: p50 / p90 / p99, mean, min, max, n — per experiment and for production (`task_stat`, split by
  cache state). **Classification** from cold p90 with configurable thresholds (lightweight < 150 ms ≤ medium <
  1.5 s ≤ heavy < 15 s ≤ very_heavy); Light Mode eligibility from `light_mode_max_ms`.
* **Hypotheses**: `cost_class` (expect a class), `warm_speedup` (warm p50 ≥ ×F faster), `under_ms` (cold p90 below a
  bound) → supported / refuted / inconclusive, with the numbers.
* **Proposals**: `config/test-space/proposals/<date>-<kind>-v<N>.yaml` — versioned (never overwritten), with
  current → proposed per field, statistics, sample size, runs, environment, thresholds, author, timestamp. Refused
  with fewer than `min_samples` cold samples or when the measurements agree with the configuration.
  **Review**: *accept* writes the change into `config/tasks.local.yaml` (with `_from_proposal`) and reloads the task
  specs; *reject* keeps the proposal as history; both are audited.
* Isolation: own settings (`config/test-space/test-space.yaml`, example committed), own results, own proposals
  folder (git-ignored); production values change only through an accepted proposal.
* API `GET /test-space`, `POST /test-space/experiments`, `GET /test-space/experiments/{id}`,
  `POST …/{id}/propose`, `POST /test-space/proposals/{id}/accept|reject`. UI: **Test Space** page (production
  timings vs configured cost, new experiment with hypothesis, live progress, stats, raw samples, proposals with
  accept / reject).

Tests: `tests/unit/test_test_space.py` (percentiles and classes, cold slower than warm, hypothesis verdict,
proposal v1/v2, nothing changes before review, accept writes and reloads, reject, refusals for thin evidence and bad
input, production timings in the view); e2e journey 37.
