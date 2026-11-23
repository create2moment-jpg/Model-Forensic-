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

## Follow-up: what is cached automatically

Root cause of hundreds of cached files nobody downloaded: views read evaluation files by downloading them first —
Check my run / the story strip, Precision & recall, Sequences, Findings (eval-JSON analyses) — up to 200 MB per call.

Rule for remote workspaces (Azure ML):

| Automatically (≤ 50 MB per request, `auto_fetch_max_bytes`) | Only on request (Artifacts tab, Prefetch, *Plan download*) |
|---|---|
| logs, configs, Lightning `metrics.csv` | evaluation JSONs, per-sequence parquets, plots / visualisations, checkpoints, everything else |

* `eval_json` / `eval_tables` read only what is already cached; `fetch=True` (an explicit request) downloads first.
  A partial read is not remembered, so after a download the full set is read.
* Views say what is missing: `pending` (files, bytes) on Precision & recall, Sequences and Check my run, shown as
  "N files (X MB) not downloaded — this view shows what is" with **Plan download** (opens the Prefetch plan to
  review first).
* Log scans fetch uncached logs only within the 50 MB budget; the rest is listed as "over the automatic limit".
* Local folders are read in place as before (no network, nothing to save).
* `mf cache-clear [--run <id>] [--all] [--yes]` frees disk: removes local copies (a pipeline's stages included),
  keeping logs, configs and metrics.csv unless `--all`. Azure is never touched; files can be fetched again.

Tests: `tests/unit/test_fetch_policy.py`.

## Follow-up: pipeline metrics as stage / metric

* `GET /runs/{run}/metric-catalog` (`application/stages.py::metric_catalog`): every metric of a pipeline with the
  stage that logged it, in pipeline order; a metric logged by several stages (e.g. `train/loss` in
  `train_pretrain` and `train_finetune`) appears once per stage, and the last stage's plain copy (kept for
  headlines / comparisons) is not listed twice.
* Metrics tab: stage toggles (on / off; remembered by stage name, so hiding `train_pretrain` applies to every run
  that has it), the list grouped as `stage / family`, charts titled `stage / metric`. Plain runs are unchanged.

Tests: `tests/unit/test_metric_catalog.py` (two training stages), e2e 43.

## Follow-up: every table sorts

`web/src/lib/tableSort.ts`, installed once by the layout: click any column header of a data table (`table.t` with a
header row) — ascending, again descending, a third time the original order (▲ / ▼, `aria-sort`). Values come from a
cell's `data-sort` when set, else its text, read as numbers with units where they look like one (`12 %`, `1.2 GB`,
`350 ms`, `−0.03`, `×1.4`, `2.5e-4`); empty cells (`—`) stay last. The order survives live refreshes. Tables that sort
themselves (Runs), opt out (`data-nosort`) or have detail rows (`td[colspan]`) are left alone. The Uncertainty table
sorts *start → end* columns by the end value and the status by severity; Setup clues got a header row. e2e 44.

## Follow-up: charts zoom and go full screen

Every chart (`components/Chart.tsx`, used by Metrics, Plots, Uncertainty, Compare, Progression …): **zoom in /
zoom out / reset** buttons, drag across the chart to zoom into a range, double-click to reset, **Ctrl/⌘ + wheel** to
zoom around the cursor (plain wheel inside full screen); the y axis follows the visible range. **Full screen** opens
the chart as an overlay (Esc, the button or a click outside closes it); the zoom is kept when entering / leaving full
screen or changing smoothing / log scale. The chart toolbar is always faintly visible (fully on hover). e2e 45.

## Follow-up: compare starts with an overview of the two runs

`pages/compare/RunsOverview.tsx`: before the metric deltas and config diff (which load both runs' histories), a
card shows the two runs side by side from indexed facts only (`/runs/{run}/light`, no backend call): display name,
run id (copy), **feature branch @ commit** (dirty flag), status, experiment, owner, created, duration, compute,
kind / stages, description, tags that differ, and the headline values with the change in %. Rows that differ are
highlighted and summarised ("They differ in feature branch, owner, compute, 2 tag(s)"). e2e 46.

## Follow-up: several candidates, and one model's results spread over several run ids

* **Run groups** (`application/run_groups.py`): `group:<run>+<run>+…` is one subject made of several run ids — a
  training job and its separate evaluation job, a resumed run, … It is a *virtual pipeline whose stages are its
  members*: metrics as `member / metric` (a metric logged by several members kept per member; the last member's
  value is the headline), configs and files of every member (a member may itself be a pipeline; its stages are
  included), downloads from the member that owns the file, logs concatenated per member. Status = the worst member
  status; branch = the common branch, else `a | b`. Groups are never written to the run index and never synced. A
  group can be a baseline, a candidate, a study run — anywhere a run id is accepted, API included.
* **Compare**: one baseline and up to 10 candidates (`?candidate=…&more=…,…`; `?focus=i` picks the one shown in
  detail). The overview card has a column per subject with differences from the baseline highlighted per cell;
  tabs switch the detailed deltas / config diff between candidates; the other candidates stay in the curves. The
  baseline and each candidate are a `SubjectPicker`: one run, or **+ run** to add the run ids that belong to it.
* **Studies**: the new-study form takes a baseline subject and several candidates, each with **+ run**.
* **Runs table**: select runs → **Open as one run**.

Tests: `tests/unit/test_run_groups.py` (reference format and validation, built from members, never indexed,
pipeline members, compare with groups on both sides, studies, downloads from members, bad groups), e2e 47.

## Follow-up: display quality — fixed space, trends, loss sections, wide screens

* **Nothing overflows**: one rule everywhere — fixed space; text that does not fit is cut with … and shown in full
  on hover (`lib/autoTitle.ts` adds the tooltip to any clipped element; `.trunc`, tags, stage chips, chart titles,
  metric names, table cells).
* **Headline tiles** (`components/MetricTile.tsx`): fixed size; short name on top, split · dimensions underneath,
  full key on hover; the value; a sparkline of the run and the change from start to end (coloured by whether it
  moved the right way; only for metrics with a history). Trends load only once the histories are loaded (*show
  trends* starts that in the background) — never a reason by itself to load them.
* **Pipeline stage strip**: a stage whose status the backend does not report shows a quiet dot with the reason on
  hover instead of an "unknown" pill.
* **Metrics — study a section of the losses**: *Sets* built from the run's own names (all losses, each loss family,
  one term across layers, evaluation, learning rate, learned task weights); *One chart* (curves named by what differs
  between them, optionally *normalised* to their start); metrics with dimensions (per layer / term) can be plotted;
  **zooming one chart zooms every chart** of the run (shared step range); the **section summary** gives per metric
  start, end, min, max and change inside that range (sortable).
* **Wide displays**: pages use up to 2320 px from 2000 px wide screens (3000 px beyond 2600 px); metric charts lay out
  in an *auto* number of columns (≥ 520 px each).

e2e 48 (sets, one chart, synced zoom, section summary, tiles never overflow).

## Follow-up: metrics left, losses right, stages on top

* Metrics tab: a **Stages** bar on top (pipeline stages on / off, *show all stages*; remembered by stage name), the
  **Metrics** list on the left and a **Losses** list on the right (every metric whose name contains *loss*), each
  with its own filter and the same grouping; charts in the middle. Below 1500 px the losses list moves under the
  metrics list.
* Lists: names without a split are grouped by their prefix (`CpuMonitor.ctx_switches` → *CpuMonitor* ·
  ctx_switches) instead of one header per metric; a dimension that is the same in every row of a group
  (`task=sparse_detection`) is shown once in the group header, not on each row.
* Sets row: the first eight, *+ N more* for the rest. *Ignore spikes*: the y axis follows the 1st–99th percentile so
  a start-up spike no longer flattens the curve.

e2e 50.

## Follow-up: visual pass for Compare and Study

* **Config diffs across run shapes** (`analysis/config_diff.py::canonical`, `leaf`): a pipeline keeps a config file per
  stage (`evaluate_finetune/config.yaml.data.batch_size`), a plain run one (`data.batch_size`). Settings with the same
  value in every file now collapse to their plain path; a setting that differs between stages stays per stage and is
  compared with the other run's single value (one *changed* row per stage, not *removed* + *added*). Demo study:
  332 rows → 20, all real (per-stage seeds, the evaluation dataset, the metrics ruler).
* **Names that always fit** (`MetricName`, `ConfigPath`): metrics as short name + split · dimensions, settings as the
  setting + a small *stage · file* tag; full key / path on hover; tables with a fixed layout so nothing spills out of
  its card.
* **Compare**: overview headline rows use those names, find values under either spelling (`val/AMOTA` / `AMOTA`) and
  colour the change by whether it moved the right way; the deltas table and the config diff use the same names.
* **Study**: the Metrics card shows metrics whose only dimension is the task (it said "no evaluation metrics" before)
  and offers *Show them* when only sliced metrics exist; *What differs from the baseline* is grouped per run
  (heading with the number of differing settings), 25 rows then *show all*, values cut with … and shown on hover.

Tests: `tests/unit/test_config_align.py`, e2e 51.

## Follow-up: the Uncertainty tab as a study; picking runs by experiment

* **Uncertainty** (`application/uncertainty.py`, `GET /runs/{run}/uncertainty?baseline=`):
  * over time: each head's **share of the total weight**, its **share of the weighted loss** (weight × its own
    unweighted loss, where logged), the **effective number of tasks** (exp of the share entropy) and the **balance**
    (normalised entropy);
  * per head: share at the end, min – max, the step it **switched off** (share < 2 % to the end), the step its log σ
    **settled**, the **gap to the optimum** (for `L·exp(−2s) + s` the optimum is `s* = ½·log(2L)`; > 0 means
    under-weighted), the **σ ↔ loss correlation** (theory: σ follows the head's loss), own-loss progress;
  * **study against a baseline**: per head the share change in points and the baseline's status, balance vs the
    baseline, heads only in one run;
  * UI: summary tiles (heads, effective tasks, balance, largest / smallest weight, balance vs baseline), charts
    with synced zoom (shares, weighted-loss shares, effective tasks, log σ), *all heads / need attention*, a sortable
    per-head table.
* **Run browser** (`components/RunBrowser.tsx`): every run picker has **Browse** — experiments on the left (yours
  starred, run counts, filter), the experiment's runs on the right (name, run id, status, owner, branch, age), search
  with the Runs-page syntax, *Mine* / *Completed*. Used by Compare (baseline, candidates, + run), Studies, the
  Uncertainty baseline, …

Tests: `test_plots_uncertainty_clues.py::test_uncertainty_over_time_theory_and_baseline`, e2e 52–53.

## Follow-up: chosen stages, every run feature in Compare, comparison filters

* **Subjects from chosen stages** (`components/SubjectPicker.tsx`): each member shows its status; a pipeline opens
  to its stages (status, failed ones flagged *untick to exclude*). Unticking a stage replaces the pipeline by its
  other stages; **+ stage** adds one stage of another run (e.g. train from run A, evaluation from run B). Stages are
  runs, so this is the run group (`group:<stage>+<stage>`) — compared, studied and opened like any run; a stage
  member is labelled *pipeline / stage*.
* **Compare tabs**: *Deltas & config*, *Metrics* (candidate vs baseline, other candidates in the curves),
  *Uncertainty* (the candidate studied against the baseline), *Plots*, *Setup* (all subjects side by side, differences
  highlighted), *Findings* — each for the candidate in focus. The heavy comparison runs only on its own tab.
* **Filters** (`pages/compare/Filters.tsx`): free text, metric family, and chips per dimension the metrics carry
  (class, range, distance, task, …); an empty filtered view says how many match under *All*.

e2e 54–55.

## Follow-up: plot straight from table rows

* **Row chart button** (`components/RowActions.tsx`): every row of the tables below the charts (section summary,
  slice breakdowns, Compare deltas) ends in a chart button with a small menu: *Add as chart*, *Plot only this*,
  *Plot its family together* (≤12 slices in one chart), *Remove the chart*, *Copy the name*; in Compare *Plot it
  across the runs* opens the Metrics tab with that metric (`ctab=metrics&mk=…&mt=1`), and *Filter the table to its
  family*. The menu is drawn above the page (portal, follows its button), so tables never clip it.

e2e 56.

## Follow-up: slice breakdowns as plots, foldable sections

* **Slice breakdowns are plots** (`pages/run/Metrics.tsx::SliceFamily`): one chart per metric family, a curve per
  slice (baseline dashed in the same colour), zoom synced with the other charts, a compact legend that names each
  slice by what differs (`module · scale` → `radar_fpn · 2`). *Final values* switches back to the table (remembered).
  Families beyond the first four start folded and load nothing until unfolded; ≤12 slices per chart.
* **Folding** (`components/ui.tsx::Card fold=…`, `FoldAll`): section summary, slice breakdowns (and every family),
  Compare overview / metrics / configuration diff / setup fold from their head; the choice is remembered per section.
  *Fold all / Unfold all* on Compare, *Fold / Unfold families* in the slice breakdowns.

e2e 57.

## Follow-up: hide / remove charts, more sets in two rows, multi-level filters

* **Charts hide or go**: every chart has *Hide* (kept in the selection, listed in a *Hidden* strip to show it again)
  and *Remove*; the one-chart view has *Remove*. Slice families hide the same way (remembered), and a click on a
  legend chip switches a single slice curve off and on.
* **Sets in two rows** (`buildSets`): *Losses* and *Metrics* rows, sorted stage by stage, so multi-stage runs line up.
  More sets, all from the names the run has: losses per stage, train vs val losses, per loss family and term, per
  modality; metrics: evaluation, learning rate, task weights, gradients, features, system, every sliced family,
  per modality and per class. Duplicate sets (same metrics) are shown once.
* **Multi-level filter** (`pages/compare/Filters.tsx::facetsOf`): class, modality and distance first, then every
  other dimension; chips combine (any value within a dimension, all dimensions together). Usual spellings are mapped
  (`cls`/`category` → class, `dist`/`range` → distance, `sensor` → modality) and the modality is read from the name
  when no dimension says it (`module=camera_fpn`, `loss/radar_head`). In the Metrics view the filter narrows both
  lists, the sets, the charts and the slice breakdowns; Compare uses the same facets.

e2e 58.

## Follow-up: filter facets as configuration, architecture study prompts, decoder layers

* **Facets config** (`defaults/facets.yaml` + git-ignored `config/facets.local.yaml`, `application/facets.py`):
  aliases per facet, the possible values in display order, value aliases, `from_name`, `only_listed`, `hide_dims`,
  `order`. `GET /api/v1/facets[?run=]`, `mf facets [--run] [--json] [--check FILE] [--brief --run ID]`; the brief
  plus `setup-agent/prompts/filter_facets.md` let a local LLM propose the file. The UI applies the same rules.
* **Harness tasks** `backbone`, `neck_and_fusion`, `head_and_queries`, `loss_study`, `config_study`.
* **Decoder layers**: a loss family that varies only by `layer` shows the final value per layer and the change from
  the previous layer, for the run and the baseline.
* Guide: [`docs/guide/sparse-multimodal-detection.md`](guide/sparse-multimodal-detection.md).

Tests: `test_facets.py`, `test_harness.py::test_tasks_and_rules_exist`, `test_config_checks.py`; e2e 59.

## Follow-up: training health questions

* `analysis/loss_questions.py` (deterministic): is the loss diverging, NaN, spikes, plateau / still learning,
  oscillation, overfitting; terms rising / dominating / flat; **layer refinement diverging**, later layers
  saturating; weighting muting a task; gradients; dying modules; queries; against a baseline: early advantage lost,
  which terms / layers got worse, loss vs evaluation. Each answer: status, numbers, series to plot, where to look,
  what to try, why it matters.
* `GET /api/v1/runs/{run}/loss-questions?baseline=`, `mf questions --run … [--baseline …] [--problems] [--json]`;
  the Metrics tab (run and Compare) shows them first, problems only by default.
* Guide: [`docs/guide/loss-study-checklist.md`](guide/loss-study-checklist.md) — every question (also the ones that
  need judgement), where to look, what to try, and the order to step up.

Tests: `test_loss_questions.py`; e2e 60.

## Follow-up: features on / off, user levels, convergence

* **Features** (`web/src/lib/features.ts`, Settings → Features): 21 features (Metrics panels, run tabs, Compare tabs)
  with a level (simple / standard / expert) and a cost (light / medium / heavy). The level sets the defaults; single
  switches override; presets *Light view*, *Level defaults*, *Everything*. A feature switched off is not rendered and
  loads nothing (e.g. the slice query is disabled). Folding stays for reading.
* **Levels**: [`docs/guide/user-levels.md`](guide/user-levels.md) — what separates simple, intermediate and expert.
* **Convergence**: question *Has the model converged?* (loss + LR + evaluation: converged / still learning /
  stalled at a high LR / loss converged but evaluation improves / not converging).

Tests: `test_loss_questions.py::test_convergence_verdicts`, `…::test_loss_converged_but_eval_still_improves`; e2e 61
(e2e journeys start at the expert level).

## Follow-up: experiment ledger, model internals

* **Experiment ledger** (`application/ledger.py`, page *Ledger*, `mf ledger …`): one entry per experiment —
  hypothesis and decision written by people, the **outcome computed** per candidate (meaningful config changes, the
  metrics that moved with the significance basis, the training health problems). *Add to ledger* on Compare;
  status planned → analysed → decided; Markdown export (`/ledger.md`, `mf ledger export`); a decided entry becomes a
  *proposed* knowledge entry (a **belief with a test** when the change is one numeric config key). Stored in the
  local document store, never in Azure.
* **Model internals**: the kit logs cross-attention per decoder layer × modality (`attn_mass`, `attn_entropy`,
  `attn_top1`; `AttentionRecorder` hooks on `nn.MultiheadAttention`, or `log_attention` with your own weights) and
  query ages (`q_age_mean`, `q_age_frac`). `analysis/internals.py` builds the layer × modality table (end of training,
  first quarter, baseline) and notes: a modality starved (< 5% in every layer), abandoned by later layers, faded over
  training; attention collapsed / diffuse; memory unused / stale. Metrics → *Model internals* (expert feature),
  `GET /runs/{run}/internals`, questions *Does every modality get the decoder's attention?* and *Is the temporal
  memory used?*. Demo runs carry these series (the radar-collapse run's attention on radar fades).
* The kit's tests no longer need torch except where they use it (`tests/_toy.py`).

Tests: `test_ledger.py`, `test_internals.py`, `instrument/tests/test_attention.py`; e2e 62–63.

## Follow-up: loss drift, expected vs actual losses, expert review

* **Loss drift** (`analysis/loss_drift.py` on `divergence.diverge`): every loss both runs log — total, terms, per
  layer / branch / module — compared over the whole progression: where it leaves the baseline beyond its own noise,
  whether the gap holds / closes / crosses, convergence speed (share of the schedule to 50% / 90% of the drop),
  noise and spikes. Readings across losses: drift from the first steps (architecture / data / loss definition),
  losses drifting together at one point (a shared cause there, with the LR context), drift confined to one family,
  gaps that close (speed, not optimum). Schedules of different length are aligned on the schedule fraction.
  Compare → Metrics (*Loss drift vs the baseline*), `GET /compare/loss-drift`, `mf drift`.
* **Expected vs actual** (`application/expectations.py`): the harness task `loss_expectations` (own output contract)
  lets a local LLM write, from the training code and the run's resolved config, every term's formula, weight,
  expected start value, range, direction, switch-on, layers; the total's composition; config rules — to
  `<inbox>/expectations/*.yaml` (`harness/expectations.schema.json`; matched by run id or `when_config`). Checks:
  presence, start value (lower → data, higher → implementation), range, direction / stops learning (data), switch-on
  (schedule), layer count (config) and order, weight key (config), **composition** — the weights the curves imply
  vs the code's (implementation), config rules. `GET /runs/{run}/expectations`, `mf expectations`.
* **Expert review** run tab: one prioritised list from the code checks, the health questions and the drift, each
  with the likely kind of cause and the next step; the code checks with the curves and the expected start drawn in;
  how to produce the expectations when none exist (brief download, where the agent writes).
* `mf demo` writes demo expectations (`demo-detector.yaml`): the demo total is unweighted although the config says
  weights, and the velocity loss stops learning — both found.

Tests: `test_loss_drift.py`, `test_expectations.py`; e2e 64–65 (e2e 17 now checks the stable picker label).

## Follow-up: one stage at a time, the analysis dossier

* **Stages are never mixed** (`application/stages.py::stage_view`): a metric logged by several stages is kept once
  per stage (`stage=` dimension) and the plain key holds its last stage. Every analysis now reads one view: the
  default (each metric from its last stage, the `stage=` copies dropped) or one named stage (exactly what it logged,
  under plain keys). `stage=` on the loss-questions, internals, expectations and loss-drift endpoints; `--stage` on
  `mf questions / drift / expectations / dossier`; a stage selector in the Metrics view (questions, drift, internals
  follow it) and in Expert review. Fixed: model internals could overwrite one stage's attention with another's.
* **Analysis dossier** (`application/dossier.py`, `defaults/dossier_prompt.md`): everything Model Forensic found
  about a run — the run, configuration (or its differences to the baseline), results with significance basis, loss
  trajectories, health questions, loss drift, expectations from the codebase, model internals, stored findings, team
  knowledge, ledger entries, and what could not be examined — each observation with an evidence id (`[E-Q3]`),
  hypotheses marked, secrets and paths left out; then the instructions for the interpreting LLM (diagnosis,
  observations citing ids, differential, where to look, next steps, confidence and gaps). `mf dossier`,
  `GET /runs/{run}/dossier[?baseline&stage&detail&save]`, *Dossier for the LLM* on the run page and Compare; saved to
  `<inbox>/dossiers/`. Interpretation happens outside the tool.

Tests: `test_stage_views.py`, `test_dossier.py`; e2e 66.

## Follow-up: design review, work orders

* [`docs/design-review.md`](design-review.md): context / layers / data-flow / API diagrams, nine bottlenecks ranked,
  nine decisions with timing; [ADR-0013](adr/0013-planner-executor-work-orders.md): Claude plans, the operator model
  executes, Model Forensic validates.
* Done now (D1–D3): `workorders/index.yaml` — 25 work orders (every harness task, setup prompt, facets, dossier
  interpretation; calibration planned) with tier, trigger, inputs, output contract, scope, validation, focus, checklist;
  `mf work list | show | brief | check | escalate`; escalation packets in `<inbox>/escalations/`.
* Correction (the operator's limit is judgement, not context): briefs no longer cut anything — the work order's
  focus orders them (what the task needs first, everything else after, complete; no key caps); token budgets are
  gone; every card carries the method (collect facts with their source, then decide) and a self-check list
  (generic + task-specific for loss expectations, loss composition, facets, dossier interpretation).

Tests: `test_workorders.py` (registry complete, files and commands exist, the focused brief loses nothing, cards carry
method and self-checks, contract checks, packet size).

## Follow-up: thresholds in configuration (D5), calibration on real runs (D6)

* **D5** `defaults/analysis.yaml` holds every threshold of the health questions, loss drift, model internals and the
  expected-vs-actual checks — value, meaning, alarm direction; `config/team.local.yaml` → `analysis_params.<section>`
  overrides (applied by the container; unknown keys are reported). `analysis/thresholds.py` (`t`, `table`,
  `override`), `mf thresholds`, `GET /settings/thresholds`, Settings → *Analysis thresholds*.
* **D6** questions that compare one measured value with one threshold now report `measure`, `param`, `alarm_when`.
  `mf calibrate template --runs …` writes every alarm with what was measured — the operator fills only `verdict`
  (true / false / unsure) and `why`, plus problems the tool `missed`; `mf calibrate check`; `mf calibrate propose`
  computes per threshold the value between the false alarms and the real problems (missed problems are re-measured),
  or says it cannot separate them (escalate: the question needs another signal); `mf calibrate apply` merges an
  accepted proposal into `team.local.yaml` (`.bak` kept). WO-calibrate-questions is ready
  (`workorders/calibrate_questions.md`: two passes, decision table, worked example, self-checks).

Tests: `test_thresholds.py`, `test_question_calibration.py`; e2e 67.
