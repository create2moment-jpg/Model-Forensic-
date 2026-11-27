# Azure ML studio parity and continuous-training tracks — the plan

Status: **wave A (time) built; tracks built (wave B, first part)** · 2026-11-27 · asked: "the job running duration and stage-level duration are visible in Azure studio,
our tool does not have it; there are many things in studio we miss — have everything planned. Continuous training runs
are recurring runs; I should be able to track the progress of the models."

Continuous training, as answered: **recurring runs** — a schedule or CI launches a new training job / pipeline every
day or week (new data, same recipe); each model is to be compared with the previous one and the best one, over time.

## The rule this plan keeps

Model Forensic stays **read-only** (AGENTS.md rule 1): it shows and explains everything studio shows about a run,
and more; it never submits, cancels, re-runs, registers, deploys or edits. Anything in studio that writes stays in
studio — we link to it (every run already has *Open in Azure ML studio*).

## 1. Durations and time — the gap you hit first

**Root cause (from the code).** `azureml/mapper.py::map_job` reads a job's start and end only from the job's
`properties` (`StartTimeUtc` / `EndTimeUtc`, or the MLflow start / end). The Azure job resource does not always carry
them — pipelines and jobs that never logged through MLflow arrive with no start / end, so *Duration* is "—" in the runs
table, the run header and Today. Stages only got their times today (`map_child`, from the run-history child listing),
and the pipeline strip and Timeline still estimate where they lack them. Studio reads these times from **run history**,
which we already call.

**Plan.**

| # | What | Where |
|---|---|---|
| T1 | Times from run history when the job lacks them: created, **queued → started → ended**, status, for every listed job — one batched run-history call per experiment page, cached with the index (terminal runs never re-read) | `runhistory.py` (batch get), `mapper.py`, `service.py::_with_stages` next to stages |
| T2 | **Queue time** (created → started) and **run time** (started → ended, or → now while running) as fields of a run; *compute time* = run time × nodes when the node count is known | `domain/run.py` (derived, not stored twice) |
| T3 | Run header: *Queued 12 min · Ran 9 h 04 m · Ended 21 h ago* (studio's Overview line), live while running | `RunDetail.tsx` |
| T4 | Runs table: *Duration* and *Queued* columns sortable; Today's *Running now* shows run time (done) and queue time for queued jobs | `Runs.tsx`, `Home.tsx` |
| T5 | Pipeline: each stage's measured queue / run time in the stage strip; the Timeline becomes a **Gantt of the stages** (start offset, queue part, run part), estimated spans marked as such only where run history has nothing | `Overview.tsx`, `Timeline.tsx`, `application/timeline*` |
| T6 | `mf doctor --live` reports which time fields the workspace returns (properties vs run history), so a gap is visible, not silent | `live_checks` |
| T7 | Durations across runs: *Progression* and tracks (§3) can plot run time / queue time like a metric — "the nightly got 40 % slower since …" | progression, tracks |

Needs from you: one `mf doctor --live --json --bundle support.json` after T1/T6 to confirm on the real workspace.

**Built (2026-11-27): T1–T4, T6.** `RunHistoryGateway.run_times` (one `get_by_query_by_experiment_name` per
experiment, newest first, stops when all asked runs are seen, ≤ 500 rows) and `run_time` (one `runs.get` for a run
opened directly); `mapper.with_history_times` fills only missing fields and marks `times_from`;
`AzureMLRunSource._with_times` (≤ 20 experiments per page, 4 in parallel, finished runs cached) and `_run_with_times`;
doctor check **times**; `web/src/components/RunTimes.tsx` in the run header, live running time in the Runs table.
Not yet: T5 (Gantt of measured stage times — stage runs now carry their times, the Timeline still estimates where a
local run lacks them), compute time × nodes.

## 2. What studio has that we do not — the full list

Studio's job page tabs and workspace pages, against what Model Forensic has now. **Priority**: P1 next, P2 after,
P3 when asked, — not planned (writes, or outside run forensics).

| Studio | Studio shows | We have | Gap → plan | P |
|---|---|---|---|---|
| Job · Overview | status, created / start / end, duration, queue, compute, submitted by, tags, properties, inputs / outputs, environment, command, arguments | status, owner, tags, git, compute target, command, datasets, models; **times often missing** | §1; **properties** (all, searchable) and **inputs / outputs** with their asset name + version and datastore path; environment name + version + image | P1 |
| Job · Metrics | charts of logged metrics | far more: stages, baseline overlay, drift, noise, verdict | — (ahead) | |
| Job · Images | logged images per step | none | **Images tab**: logged images by name and step, side by side with the baseline's at the same step (read via run-history artifacts) | P2 |
| Job · Child jobs | pipeline graph with node status, duration, inputs / outputs | linear stage strip, stages as runs (today) | **Pipeline graph** (DAG from the pipeline definition: nodes, edges, per-node status and time, which outputs feed which inputs); stage strip stays for linear pipelines | P2 |
| Job · Outputs + logs | file tree, logs, live logs | files, logs (telling log first), downloads, offline copies | **Live log tail** while running (follow mode, every 10 s) | P2 |
| Job · Code | the code snapshot | code diff vs baseline | **Browse the snapshot** (tree + file view), diff stays | P3 |
| Job · Monitoring | GPU / CPU utilisation, GPU memory, disk, network over time (Azure Monitor) | none | **Resources tab**: utilisation curves per node from Azure Monitor metrics (read-only `metrics` API; needs *Monitoring Reader*); findings: idle GPUs, data-loader bound, memory headroom, one node lagging | P1 |
| Job · Properties / raw JSON | everything | some fields | **Raw** view (the job JSON, secrets redacted) | P2 |
| Sweep jobs | trials table, parallel-coordinates, best trial | sweeps listed as runs | **Sweep view**: trials with their hyper-parameters and primary metric, best trial, parallel coordinates; compare any two trials | P2 |
| Schedules | recurring jobs, their history | none | **Tracks** (§3): a schedule is a track; its jobs are the track's runs | P1 |
| Models | registered models, versions, lineage to the job | `models` on a run (when the job lists them) | model versions on a track's runs; *registered from this run* on the run page; list models + versions with their job (read-only) | P2 |
| Data assets | versions, lineage | dataset names on a run | **data version per run**; a track shows when the data changed | P1 (in tracks) |
| Environments | versions, image, conda | name only | environment name + version per run; *environment changed* in What changed and in tracks | P2 |
| Compute | clusters, nodes, queue | target name, instance type, node count | **Why queued**: cluster's current nodes / max, other jobs waiting (read-only compute list) on a queued run | P3 |
| Compare (preview) | metrics side by side | Compare (much more) | — (ahead) | |
| Notifications | email on finish / fail | watch a run (desktop + toast) | watch a **track** (§3) | P1 |
| Submit / cancel / re-run / clone, deploy, endpoints, notebooks, labeling, prompt flow, designer | writes or authoring | — | not planned (read-only by design) | — |

**Built (2026-11-27): Job · Overview.** Summary → Details (`web/src/pages/run/Details.tsx`) from `Run.properties`
(redacted) and `Run.io` (`mapper.py::_io`, assets and versions from `azureml:name:v` and `…/data/name/versions/v`
paths); environment name + version parsed from its id. Not yet: the environment's image (needs one environments read
per environment — left for when it is asked), properties searchable across runs (`prop:` in the search language).

**Built (2026-11-27): Job · Monitoring.** Run page → Resources: `AzureMonitorGateway` (metricDefinitions once per
workspace, then ≤ 4 metric reads per run, split by `InstanceId` / `DeviceId`; 401/403 → *Monitoring Reader* hint),
fallback to logged system metrics (`domain/resources.logged_kind`), local `system_metrics.jsonl`; deterministic checks
in `application/resources.py` (idle GPUs ± CPU-bound, memory headroom / near OOM, straggler node, disk full); finished
runs cached. Metric names on a real workspace still to be confirmed (`CANDIDATES` takes whichever it offers).

## 3. Continuous training: tracks

A **track** is a recurring line of runs that produce the same kind of model — "the nightly detector", "weekly
fine-tune on new labels". The question it answers on every new run: **is this model better than the last one and than
the best one so far, and if not, what changed?**

**Defining a track** (deterministic, no guessing at the run level):

1. **From an Azure ML schedule** — schedules are listed read-only; jobs a schedule triggered are its runs (matched by
   the schedule's job properties / name prefix, confirmed against the real workspace with `mf doctor --live`).
2. **From a rule** for runs launched by CI or by hand: experiment + tag / name pattern / branch, in the project profile
   (`config/projects/*.local.yaml`: `tracks: [{id, title, experiment, match: {tag: ...}, headline: [...]}]`), so it
   stays out of git with the other site details.
3. **From the UI**: *Track these runs* on a selection or an experiment proposes a rule (shown, editable, saved to the
   local profile).

**What a track shows** (one page, `/tracks/<id>`, a sixth section under Runs):

* **The progress line**: each headline metric over the track's runs in time order, with the **best so far** (the
  champion) marked, the **noise band** from the baseline's replicates / the metric registry's meaningful change, and
  each run coloured better / within noise / worse **vs the previous** and **vs the champion** — the same verdict
  engine as Compare, so a "regression" is meaningful, not noise.
* **Per run, what changed**: data version, config keys, code (commit range), environment — the reason a point moved.
* **Health over time**: run time and queue time (§1), failure rate, runs that never finished; a stage that got slower.
* **Model versions** where the runs register models (version, which run, its numbers).
* **The latest run's verdict** at the top ("Run 2026-11-27: better than the previous on 6 of 8, still 1.2 % below
  the champion from 2026-11-12 on NDS; data v41 → v42").

**Staying on top of it**:

* *Watch this track*: a notification when a new run is worse than the champion beyond noise, fails, or takes far
  longer than usual (the watcher already in the app, extended to tracks).
* Today: a **Tracks** card — each watched track's latest run in one line (better / worse / failed).
* `mf track list|show <id>|check <id>` (exit code 1 when the latest run regressed — usable in CI), `GET /tracks`,
  `GET /tracks/{id}`, `GET /tracks/{id}/latest`.
* Ledger: *Add to ledger* on a regression records it with the evidence.

**Reused, not rebuilt**: verdict and noise (`application/verdict.py`, registry), What changed (config diff), Progression
charts, stage indexing, run watcher. New: `domain/track.py`, `application/tracks.py`, the schedule listing in the
Azure connector, the Tracks page and card.

**Built (2026-11-27).** As asked: a track is **a workspace + an experiment**, picked from two pull-down menus (Runs →
*Continuous training*); several can be followed at once, across workspaces. `application/tracks.py` (definitions in the
document store; progress from the index: each run vs the previous finished run and vs the best so far, noise-aware;
health), `api/routes/tracks.py`, `cli/tracks.py` (`mf track …`), `web/src/pages/Tracks.tsx`, Today's card. Not yet:
schedule-based tracks, notifications on a regression (the Today card shows it), per-track metric choice in the UI
(`metrics` via the API / `mf track add --metric`), data / config change per run beyond the data version.

## 4. Order

| Wave | What | Size | Needs you |
|---|---|---|---|
| **A. Time** | T1–T6 (+ the doctor check) | M | one doctor bundle after it ships |
| **B. Tracks** | rule-based tracks first (works for CI and hand-launched runs), then schedule-based; track page, verdict per run, watch, Today card, CLI / API | L | the names of one or two real recurring lines (or confirm the proposed rule) |
| **C. Run page parity** | properties + inputs / outputs + environment version, Raw view, live log tail | M | — |
| **D. Resources** | utilisation from Azure Monitor + findings | M | *Monitoring Reader* on the workspace (or we show what is allowed) |
| **E. Pipelines & sweeps** | pipeline graph, sweep view | M–L | — |
| **F. Assets** | images tab, model versions, environment versions, snapshot browser, why-queued | S–M each | — |

Each wave: deterministic code + unit tests against SDK fakes (no real names), e2e journeys on the demo (the demo gets a
nightly schedule with 30 runs, a drift and a regression to find), docs and CHANGELOG, `mf doctor --live` checks for
whatever it reads from Azure.

## Open questions

1. How are the recurring runs launched today: an Azure ML **schedule**, CI (which tags / names do the jobs get), or by
   hand? (Decides whether tracks start from schedules or from rules.)
2. Which metrics decide "better model" for those lines — the project headline, or a different set per track?
3. Do the runs register models in the Azure ML registry?
4. Is *Monitoring Reader* (Azure Monitor metrics) available to you on the workspace?
