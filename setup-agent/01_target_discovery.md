# 01 — Target discovery

Goal: an **inventory of what actually exists** in the target project and on this machine — not what should exist.
Do not change anything in this step.

Inspect and record, with the command or file each fact came from:

1. **Machine**: OS, CPU count, free disk where `.mf/` will live, Python version, Node version (for the UI),
   `az` CLI present / logged in (`az account show`), proxy / CA variables (`HTTPS_PROXY`, `REQUESTS_CA_BUNDLE`).
2. **Roots**: `mf paths` — the project root (`PROJECT_PATH`) and the Model Forensic folder (`MF_HOME`).
3. **Where runs live**: Azure ML workspaces (subscription, resource group, workspace — from the user or `mf init`),
   or exported run folders (`<root>/<run_id>/run.json`, `metrics.jsonl`, `config.yaml`, `logs/`, `outputs/`).
4. **One real run**, read-only: its stages (pipeline children), metric names as logged, config files, artifact
   layout (where evaluation outputs, code diff, checkpoints are), log files. For Azure:
   `mf doctor --live --json --bundle support.json`; for files: `mf inspect <folder> --json`.
5. **The project's vocabulary**: what it calls runs, classes, detections; its headline metric; its pipeline stage
   names (training, evaluation, data preparation…).
6. **Documentation folders** of the project's codebases (for `knowledge_sources`).
7. **What is already configured**: `mf setup check --no-record`, `mf projects list`.

Output: a short discovery note (in your answer, or `config/setup/discovery.local.md`), every item with its source.
Mark anything you could not verify as **unknown** — never fill it in from assumptions.
