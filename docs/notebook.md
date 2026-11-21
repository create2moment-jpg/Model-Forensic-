# Expert workflow: notebook bridge, plugins, launch gate, replicate statistics

## Notebook bridge (pandas)

```bash
uv pip install -e "backend[notebook]"     # pandas + pyarrow
```

```python
from model_forensic import connect
mf = connect()                                  # same config as the CLI; read-only

runs = mf.runs("experiment:sparse_detection status:completed")
loss = mf.series("<run>", "train/uloss/*")      # long: run, key, raw_name, stage, step, value
wide = mf.wide("<run>", "train/task_log_sigma*")  # step × key
lr   = mf.lr("<run>")                           # logged lr or Lightning metrics.csv
mf.checks("<run>")                              # P1–P8 as a DataFrame
mf.findings("<run>", baseline="<base>", analyses=["loss_ledger", "stage_delta"])
mf.per_class("<run>")                           # ruler-free per-class metrics per evaluation stage
mf.sequences("<run>")                           # {"stage/file.parquet": DataFrame indexed by (sequence_hash, class_name)}
mf.compare("<base>", "<cand>")                  # noise-aware deltas
mf.noise(["<seed0>", "<seed1>", "<seed2>"])     # noise floor per metric
mf.effects(["<seed0>", "<seed1>"], "<cand>")    # delta, z, 95 % interval of the baseline mean
```

Everything comes from the same caches as the UI, so a notebook and the web app never disagree.

## Your own analyses (plugins)

Put Python files into `config/plugins/` (or any folder listed in `plugin_dirs` of `model-forensic.yaml`). Each file
defines `PLUGINS = (MyAnalysis(), …)` with the same contract as the built-ins — start from
`config/plugins/example_analysis.py.example`. They show up in the Findings tab, the study book, `mf analyze`,
the notebook bridge and the AI evidence. `mf plugins` lists them; `POST /api/v1/analyses/reload` reloads after an
edit; a broken file is reported (Diagnostics / `mf plugins`), never fatal. Built-in names cannot be replaced.

## Launch gate

```bash
mf gate configs/my_train.yaml --against <reference run>     # exit 1 on FAIL — use it in CI before submitting
```

| Check | Fails when |
|---|---|
| G1 ruler | the config's `combined_metrics_yaml` differs from the reference run's (headlines would not be comparable) |
| G2 validation | `limit_val_batches: 0`, or no validation settings at all (warn) |
| G3 checkpoint monitor | it watches a `val*` metric without validation, or the reference run never logged it |

`POST /api/v1/gate` takes `{"config": "<yaml text>", "against": "<run>"}` for tooling.

## Replicate statistics

`mf noise --run <seed0> --run <seed1> [--candidate <run>]` (or `GET /api/v1/noise?runs=…&candidate=…`): mean, std
and coefficient of variation per metric across seeds; with a candidate, its delta, z-score and whether it is
beyond the noise floor (|z| ≥ 2). Tag seeds with `group=<name>` so comparisons use them automatically.
