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

## P5-5 Plotting architecture · uncertainty-based loss weighting · Training Setup Clues

### Plot specs (`domain/plotspec.py`, `application/plotting.py`)

Every plot is a **PlotSpecV1** — strict schema (unknown fields refused, ≤ 12 series, typed axes, transforms
`raw · ema · exp_neg2 · normalize · delta_from_first`, source `rules · analysis · ai · user`). `validate` checks the
schema, then **grounding**: the run exists, each key is one of its series (with "similar" keys on a miss), a log
axis has positive values. An invalid spec is never drawn (`/plots/render` answers 422 with the errors).
`suggest` builds plots from the *shape* of the keys (losses, evaluation metrics, LR, learned task weights) — no
project metric names. The **local-LLM plotter** (`/runs/{run}/plots/ai`) gets the JSON Schema and the run's key
list; every spec it returns is validated or listed as rejected with the reason; offline it falls back to the rules.
API: `/plots/schema`, `/plots/validate`, `/plots/render`, `/runs/{run}/plots/suggest`, `/runs/{run}/plots/ai`.
UI: **Plots** tab (plotter question, suggestions, spec editor with validation), `PlotView` used by every section.

### Uncertainty-based loss weighting (`application/uncertainty.py`)

A dedicated section for `L = Σ exp(-2·log σ_i)·L_i + log σ_i`: per head log σ start → end, effective weight and
its **share** at the end, drift over the last 10 %, the configured **bounds**, the head's own unweighted-loss
progress, and a status — `switched_off` (share < 2 %), `at_bound`, `dominant`, `drifting`, `settled` — each with a
sentence why; the weighting config keys; the `task_weighting` analysis findings; validated plots (log σ,
effective weight, unweighted loss relative to start). No σ in the run → a `missing_data` message, no invented
heads. UI: **Uncertainty** tab. Demo: `xf-sparse-uw` shows the velocity head switched off; `xf-sparse-uw-bounded`
the clamp.

### Training Setup Clues (`application/setup_clues.py`)

Optimizer, LR, batch size, schedule, warm-up, weight decay, precision, clipping, seed, workers, devices, nodes,
accumulation, EMA, epochs / steps, loss weighting and dataset version — found by generic key patterns, each with its
source (`config:<path>`, `metric:<key>`, `run:compute`); observations (effective batch, warm-up seen in the LR
curve, LR end value, no seed, reduced precision without clipping); what was not found is listed. UI: **Setup** tab.

Tests: `tests/unit/test_plots_uncertainty_clues.py` (schema strictness, grounding, 422 on render, log-axis check,
AI output validated and rejected with reasons, offline fallback, uncertainty statuses on both demo runs, setup clues
with sources); e2e journey 38.

## P5-6 Configuration checks · local-LLM prompt set

* `application/config_checks.py` — `CFG-AZ-1` (interface file filled in), `CFG-WS-1` (workspaces connect),
  `CFG-PROJ-1` (profiles load), `CFG-MC-1` (metric columns have values), `CFG-TASK-1` (local task overrides valid),
  `CFG-TASK-2` (configured costs match measurements), `CFG-TS-1` (Test Space sample runs), `CFG-PF-1` (no stalled /
  failed prefetch), `CFG-ST-1` (storage usable), `CFG-LLM-1` (local LLM, optional), `CFG-GIT-1` (site files
  git-ignored). Each: stable id, area, severity, status, **current**, **expected**, remediation, the **prompt**
  that repairs it and the **validation** command. Read-only. `mf checks [--json]` (exit 1 on a critical failure),
  `GET /api/v1/checks`, Diagnostics → *Configuration checks*.
* `setup-agent/prompts/` — the nine prompts (`run_light_heavy_audit`, `test_space_profile`,
  `test_space_hypothesis`, `prefetch_observability_diagnostics`, `plot_selection`, `uncertainty_loss_forensics`,
  `interface_diagnostics`, `metric_columns_diagnostics`, `configuration_check_and_repair`), each with problem,
  checks, inputs, task, output, **scope**, **validation** and the shared rules; `README.md` holds the
  problem → prompt → checks → scope → validation table.
* check → prompt → validation, documented in both directions: a failing check names its prompt; each prompt names
  its checks and the command that proves the repair.

Tests: `tests/unit/test_config_checks.py` (ids unique and stable-shaped, every field filled, every prompt exists and
mentions its check, failures carry current vs expected, API, CLI `--json`, the nine prompts with all sections and
the README table); e2e journey 39.

---

# Final report

**Principle** — every long-running thing answers: *is it running* (task state, heartbeat), *what is it doing*
(phase, step, files in flight), *how much is done* (counts; a fraction only when sizes are known), *what is blocking
it* (waiting on a dependency, diagnosis with evidence), *what will happen next* (`next`, the pending part says which
task fills it).

## Root causes (traced before changing anything)

| Symptom | Root cause | Fixed in |
|---|---|---|
| Run page blocks for seconds | one synchronous overview request loaded every series of the run and its stages, configs, files and a baseline comparison | P5-1 |
| Warm-up wheel lost on a page change | state in React component state | P5-1 |
| No view of running work | no application-wide task registry | P5-1 |
| Prefetch "stuck" without explanation | coarse states, free-text events, no heartbeat, % from known sizes only | P5-2 |
| Readers' problems unreadable | free-string notes without class or schema | P5-3 |
| Metric columns empty (Azure) | listing carries no values + hard-coded `NDS/mAP/AMOTA` | P5-3 |
| Task costs guessed | declared by hand, never measured | P5-4 |
| Plots ad hoc / an LLM could plot invented keys | no plot contract | P5-5 |

## Architecture (unchanged layering, new parts)

`domain` ← `analysis` ← `application` ← `infrastructure` / `api` / `cli`; wiring in `container.py` / `api/app.py`.

| New part | Layer | Role |
|---|---|---|
| `domain/messages.py` | domain | classified parser / schema messages |
| `domain/plotspec.py` | domain | PlotSpec v1 contract |
| `application/tasks.py` + `defaults/tasks.yaml` | application | TaskManager (bounded, deduplicated, dependency-aware, measured) + master task config |
| `application/run_loader.py` | application | Light / Full progressive run opening |
| `application/prefetch.py` (extended) | application | phases, per-file states, heartbeat, events, stalls, diagnosis |
| `application/metric_columns.py` | application | columns from data, MC-1, fill |
| `application/test_space.py` | application | experiments, percentiles, hypotheses, proposals, review |
| `application/plotting.py` | application | suggest / validate / render / AI plotter |
| `application/uncertainty.py`, `setup_clues.py` | application | forensic sections |
| `application/config_checks.py` | application | CFG-* checks mapped to prompts |
| `infrastructure/artifacts/store.py` (`on_stage`) | infrastructure | per-file download hooks |

## Interfaces added

API: `/runs/{run}/light · /load · /tasks · /overview-cached`, `/tasks/active`, `/runs/metric-columns`,
`/runs/summaries`, `/prefetch/{id}/events`, `/test-space…`, `/plots/schema · /validate · /render`,
`/runs/{run}/plots/suggest · /plots/ai`, `/runs/{run}/uncertainty`, `/runs/{run}/setup-clues`, `/checks`.
CLI: `mf checks`, `mf inspect` (classified messages). Config: `defaults/tasks.yaml` (shipped),
`config/tasks.local.yaml`, `config/test-space/*` (git-ignored; example + README committed).

## Measured results — run opening (demo pipeline run, 0.5 s per backend call)

| | before | after |
|---|---|---|
| cold, time to the run page shell | **4124 ms** | **9 ms** (`/light`) |
| cold, overview of what is loaded | 4124 ms | 12 ms (`/overview-cached`), filled in as tasks finish |
| Full Mode, every part loaded | 4124 ms, serial, nothing visible meanwhile | 2150 ms, parallel, each part visible as it lands |
| warm | 11 ms | 7–11 ms |

Prefetch: start answers at once (listing moved into the worker); a stall is raised after `stall_after_s` (60 s
default) without a byte, cleared on the next byte.

## Continuity and observability

Run tasks, prefetch tasks and jobs live on the server: switching pages or reloading never resets them; the header
indicator lists everything running; the warm-up wheel derives from server jobs; prefetch tasks survive a restart as
resumable.

## Acceptance tests

| Requirement | Test |
|---|---|
| Run page never an empty wait; Light / Full; state survives a page change | e2e 33; `test_tasks.py::test_progressive_run_opening_over_the_api` |
| Run ID column copy / search | e2e 34; `test_run_id_is_searchable…` |
| Tasks deduplicated, dependencies, concurrency, timings | `test_tasks.py` |
| Prefetch phases, per-file states, heartbeat, events, stalls, diagnosis, no invented % | `test_prefetch_observability.py`; e2e 35 |
| Parser messages classified with expected vs observed; never a false zero | `test_parse_messages.py`, `test_inspect_drift.py` |
| Metric columns repaired end to end, no hard-coded names (smoke test) | `test_metric_columns.py`; e2e 36 |
| Test Space cold / warm, p50 / p90 / p99, hypotheses, versioned reviewed proposals | `test_test_space.py`; e2e 37 |
| Plot specs schema-validated and grounded; AI plotter validated | `test_plots_uncertainty_clues.py`; e2e 38 |
| Uncertainty weighting section; setup clues with sources | same; e2e 38 |
| Config checks → prompt → validation; nine prompts + table | `test_config_checks.py`; e2e 39 |

Totals at the end of Prompt 5: **556 backend tests, 39 e2e journeys**, `ruff` and `mypy --strict` clean.

## TODOs / limits

* Light-mode metric histories on Azure ML are still one request per metric and stage; a batched run-history call
  would make `run.metrics` medium — measure it in the Test Space once available.
* Stall threshold is global (60 s); per-backend values could come from Test Space measurements.
* `CFG-PF-1` needs the server (the CLI does not open prefetch tasks, to avoid touching running ones).
* The AI plotter is single-turn; rejected specs are reported, not retried automatically.
* Setup clues use generic key patterns; a project profile could add project-specific clue keys (contract v2).

## Follow-up: metric histories as a background task

* `run.metrics` is its own task (heavy, concurrency 2). Light Mode never starts it on opening; it starts when a view
  needs it, from **Load metrics in background** in the run's loading panel, or for many runs from the Runs
  selection bar (**Load metrics** → `POST /runs/load-metrics`). It keeps running across pages; the header shows it.
* Progress is real: `metric histories 37/212 · part 2/4 (train)` and a bar per task — histories fetched / listed
  per part (run + pipeline stages) as each part starts.
* Azure ML: one history request per metric, now **6 in parallel** (`metric_workers`), still under the connector's
  rate limiter (8 req/s, burst 16) so throttling does not get worse. Measured with 300 ms per request:
  20 metrics 6.0 s → 1.2 s, 60 metrics 18.0 s → 5.8 s, 200 metrics 60.1 s → 23.3 s (rate-limit bound).
* Single flight: while a run's histories are being fetched, any other caller (Findings, Compare, a study) waits for
  that fetch instead of repeating it. Results stay in memory and, for finished remote runs, in the disk cache.

Tests: `test_tasks.py::test_metric_loading_in_background_with_progress_and_single_flight`,
`::test_azure_histories_are_fetched_in_parallel_with_progress`; e2e journey 40.

## Follow-up: opening a run never loads everything

Measured by replaying the run page's requests on a cold server (demo pipeline run, configs not indexed — like
Azure ML), counting backend calls:

| On open (Light Mode, Overview tab) | before | after |
|---|---|---|
| metric-history requests | 4 (hundreds on a real Azure run) | **0** |
| config loads (a file listing + downloads per stage on Azure) | 8 | **0** |
| file listings / downloads | 4 / 51 | **0 / 0** |
| run / stage lookups | 8 | ≤ 1 + stages, once; 0 when reopened |

Root causes: (1) the Overview's story strip ran the full *Check my run* (all metrics, file listing, evaluation file
downloads) on every completed run — Home did the same for the newest run; (2) `run.configs` was a Light Mode task;
(3) applying knowledge to the overview loaded every stage's configs.

Now: the check runs when asked (*Check this run*), automatically only in Full Mode; `run.configs` is heavy and
loaded by the views that need it (Config, Findings, Setup, Uncertainty); knowledge on the overview uses configs only
if already loaded; headline values come from the index; *What changed?* says it is not loaded and offers *Compare
with the baseline* (a `run.comparison` task, with the picked baseline). Guarded by
`test_tasks.py::test_opening_a_run_in_light_mode_stays_within_its_call_budget` (a call-budget test) and e2e 41.

## Follow-up: the overview stays small

* The overview shows **at most 6 headline values** (the project profile's `metrics.headline` first, then evaluation
  metrics, chosen from the data — `light.headline`), with "N of M values shown · all metrics →". Every metric is in
  the Metrics tab, loaded when that tab opens.
* **Every run opens in Light Mode.** Full is a choice for the current run view only; it is no longer remembered, so
  one click on Full never makes later runs heavy.
* Downloads happen only where you ask for them: the Artifacts tab and *Bundle* / Prefetch.

Tests: `test_tasks.py::test_light_view_offers_a_short_headline`; e2e 42.
