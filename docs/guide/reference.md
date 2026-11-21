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
* CLI: `mf --help` (search, show, compare, analyze, check, study, gate, noise, calibrate, doctor, backup, serve).
* HTTP API: OpenAPI at `/docs` on a running server; errors are `{"error": {"code", "message", "details"}}`.
