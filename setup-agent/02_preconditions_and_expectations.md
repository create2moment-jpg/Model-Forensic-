# 02 — Preconditions and expectations

Goal: the **complete checklist** of what Model Forensic needs, before comparing it with the target (03).

## Required

| Area | Precondition | How it is met |
|---|---|---|
| runtime | Python ≥ 3.11; `pip install -e "backend[azure]"` when a workspace is Azure ML | venv |
| access | read access to the runs: Azure ML *Reader* (or more) on the workspace via `az login` / managed identity; or read access to the run folders | the user's identity |
| paths | `.mf/` (data, caches, index) and the knowledge inbox writable; ≥ 5 GB free | `model-forensic.yaml` (`data_dir`), `PROJECT_PATH` |
| configuration | at least one workspace / run folder | `config/azure.local.yaml` (`mf init`) or a local backend |
| contract | every workspace passes the adapter contract v1 (`projects/contract.py`): listing, consistent `get_run`, `NotFoundError` for unknown runs, canonical metric keys with aligned steps, safe relative artifact paths, downloads resumable from an offset | `mf projects check <id>` |
| privacy | every local file is git-ignored | `mf setup check` → *privacy* |

## Expected (optional, but most of the analysis depends on them)

* Metric names the core can map (built-in: generic `train_*` / `val_*`, MLflow-safe `name__k_v`, multi-task
  `train_<task>/…`, `evaluation/<task>/<Class>`) — otherwise `metrics.aliases` / `metrics.rules` in the profile.
* Composed configs saved with the run (a `*composed_config*.yaml` per stage) — for config diffs and the study book.
* Evaluation outputs (JSON / parquet per class / sequence) — for per-class and per-sequence analysis.
* A code diff saved by the job (`code/*repo_diff*.txt` by default; `artifacts.code_diff` in the profile).
* Uncertainty-weighted losses logged per term (`uloss/`, `loss/`, `loss_noreg/`, `task_log_sigma`) — for the loss
  ledger and the uncertainty chapter.
* Optional services: an OpenAI-compatible LLM (local or remote) — Model Forensic works fully without one.

## Conventions

* Run ids are `<workspace>:<run id>`; a workspace belongs to one project.
* Credentials are never stored in files; keys come from environment variables named in the config.

Output: this checklist, copied into your report with one line per item, ready for 03.
