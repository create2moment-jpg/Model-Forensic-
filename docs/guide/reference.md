# Reference (experts)

## Analyses

| Analysis | What it finds | Key parameters (team policy `analysis_params`) |
|---|---|---|
| `training_dynamics` | NaN/Inf, divergence, spikes, oscillation, late plateau, overfitting | `spike_z`, `spike_min_rel`, `divergence_ratio`, `oscillation_threshold` |
| `study_checks` | pitfall checks P1–P8 | `degenerate_share`, `muted_sigma`, `early_fraction`, `schedule_lr_rel`, `schedule_min_remaining`, `learning_gain` |
| `loss_ledger` | per-subloss raw / weighted / network-facing loss, σ tax, dominant family | `muted_sigma`, `dominant_share` |
| `task_weighting` | heads the model gives up on (σ rises, own loss flat) | `sigma_move` |
| `decoder_layers` | layers that do not help, saturation depth, denoising health | `saturation_gain` |
| `regression` | metric regressions vs baseline, noise-aware with replicates | `high_rel`, `medium_rel` |
| `eval_localization` | where detection fails: class × distance, FP tails | — |
| `sequence_drilldown` | the worst recordings per class | `fp_per_gt` |
| `stage_delta` | what a later pipeline stage changed | `min_rel_change` |
| `metric_coverage` | metric families a stage should log but did not | `expected_metrics` (team policy) |

Precision and recall of each detector at its defaults: [calibration](../calibration.md) (`mf calibrate`).

## The loss ledger

For a Kendall-weighted multi-task loss each subloss contributes `raw·exp(−2σ)·w + w·σ`. The ledger reports, per
family, the raw share, the weighted share and the **network-facing** share (`weighted_loss_no_reg`), the learned σ
(trust), and the σ tax (`weighted − no_reg`, no gradient to the network). A negative weighted loss is the tax, not a
bug; a large share is magnitude, not influence. Across pipeline stages the ledger shows the **budget reallocation**.

## Plugins

Drop a Python file into `config/plugins/` (see `config/plugins/example_analysis.py.example`), then *Reload* in
Diagnostics or `POST /api/v1/analyses/reload`; `mf plugins` lists them. A plugin is a class with `name, version,
title, description, category, requires, limitations, Params` and `run(ctx, params) -> AnalysisResult`; build
findings with `FindingBuilder` and label evidence (observed / computed / inferred). Packaged plugins register via
the `model_forensic.analyses` entry point. Details: [development guide](../development.md).

## Notebook, CLI, API

* Notebook: `from model_forensic import connect; mf = connect()` → pandas frames ([notebook](../notebook.md)).
* CLI: `mf --help` (search, show, compare, analyze, check, verdict, summary, study, gate, noise, calibrate, doctor,
  backup, serve). `mf verdict --run <id>` — the run in one sentence with its headline numbers against the baseline
  picked for you; `mf summary --baseline <id> --candidate <id>` — the comparison as Markdown for a pull request.
* HTTP API: OpenAPI at `/docs` on a running server; errors are `{"error": {"code", "message", "details"}}`.

## Search language additions

`exp:starred` — runs of starred experiments · `is:fav` — starred runs · `experiment:mine` — experiments you have runs in. `is:top` — pipelines and plain runs (stages listed under their pipeline; the Runs page does this unless "Stages" is on) · `is:stage` — pipeline stages only.
Paste into ⌘K: a run id, `<workspace>:<run id>`, an Azure ML studio link (`…/runs/<id>`, `…/jobs/<id>`, `?runId=`) or
a local folder (`GET /api/v1/resolve?text=`).

## HTTP endpoints added with the redesign

| Endpoint | What it returns |
|---|---|
| `GET /runs/{run}/verdict?baseline=&stage=&cached_only=` | state, sentence, lead number, detail, ≤ 3 headline numbers (value, sparkline, delta, good / within noise), the baseline and why, one next step, the top problems |
| `GET /compare/verdict?baseline=&candidate=&stage=` | the comparison in one sentence and what changed |
| `GET /compare/summary.md?baseline=&candidate=&stage=` | Markdown for a PR: verdict, metrics, curves as text, config changes, reproduce command |
| `GET /resolve?text=` | the run a pasted id / link / folder names (or a folder to add) |
| `GET /runs/{run}/logs/files?stage=` · `GET /runs/{run}/logs/file?id=&tail_bytes=` | every log, the telling one first · one log's end (ranged read) |
| `PUT /experiments/star?name=&value=` | star an experiment |
| `GET /settings/local-runs/browse?path=` · `GET /settings/local-runs/complete?prefix=` | folders on this machine (local mode only) |
| `GET /backends` | now with each workspace's `runs`, `last_sync`, `syncing` |
| `GET /runs?top=true` | pipelines and plain runs only — a pipeline's stages are listed under it (`q=parent:<id>`) |
| `GET /tracks?progress=` · `POST /tracks` · `GET /tracks/{id}` · `PUT /tracks/{id}` · `DELETE /tracks/{id}` | continuous training: the experiments you follow (`workspace`, `experiment`, `title`, `metrics`, `watch`); a track's runs oldest first, each vs the previous finished run and the best so far (noise-aware), its health |

## Log insights

Run page → Files → *Log insights*: everything the run's logs say, pulled out by patterns — **problems** (errors,
warnings, OOM, NCCL, NaN, killed processes, tracebacks; counted, first and last occurrence, what usually fixes them),
**facts** (versions, world size, dataset loaded, evaluation results), **events** (checkpoints) and **curves** (loss by
step, data time, throughput, anything a pattern names). Incremental: a running run refreshes every 30 s and only what
the logs gained is read; a large remote log is read from its end the first time.

Patterns: built in (the v1 tool's readers + common failure signatures), **from the training code** (harness task
`log_patterns`: `mf harness brief --run <id> --task log_patterns`, the coding agent writes
`<inbox>/log_patterns/<codebase>.yaml`, check it with `mf work check WO-log-patterns <file>`), **proposed by the local
LLM** from the lines no pattern explains (*Propose patterns*; kept only when the regex compiles and matches the line it
cites, verbatim — without an LLM they are written from the lines' `name=value` numbers), or yours. Everything not built
in waits as *proposed* until accepted. CLI: `mf logs insights|propose|patterns|accept|reject`. Endpoints:
`GET /runs/{run}/log-insights`, `POST /runs/{run}/log-patterns/propose`, `GET|POST /log-patterns`,
`PUT|DELETE /log-patterns/{id}`.

## Continuous training (tracks)

Runs → *Continuous training*: pick a workspace (e.g. the continuous-training workspace) and one of its experiments from
the two pull-down menus and *Follow*; follow as many as you like. A track's page: the headline metrics over its runs
(oldest first) with the best so far, each run vs the previous finished run and vs the best (beyond the metric's
meaningful change — a run within noise of the best does not replace it), run and queue time, failures; missing values
are loaded when the page opens. *On Today* puts the track on Today. CLI: `mf track add --workspace W --experiment E
[--title] [--metric M …]`, `mf track list`, `mf track show <id> [--json]`, `mf track check <id>` (exit 1 when the latest
run failed or is below the best — for CI or cron), `mf track remove <id>`. Definitions are in the local document store,
never in git.

## Keyboard

`?` shows every shortcut (one list: `web/src/lib/shortcuts.ts`). ⌘K / `/` palette · `g h` Today · `g r` Runs ·
`g l` Local folders · `g c` Compare · `g p` Progression · `g t` Studies · `g k` Check a run · `g a` Ask · `g s`
Settings · `.` the run page's `⋯` · runs table: `j` `k` move, Enter / `o` open, `x` select, `s` star, `c` compare. ⌘K with nothing typed lists what you opened lately (runs and comparisons, in the view you left them) — ⌘K Enter goes back.

## Subjects made of several runs or of some stages

`group:<run>+<run>` — several run ids that hold one model's results, compared as one. `group:<pipeline>!<stage>!<stage>`
— the pipeline without those stages (expanded from the pipeline's stage list; the picker keeps the left-out stages
unticked); members can be mixed: `group:<pipeline>!publish+<eval run>`.
