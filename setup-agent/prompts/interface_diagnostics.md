# Prompt: diagnose the Azure ML interface

**Problem**: a workspace does not connect, runs are missing, stages / metrics / files do not load.
**Checks**: `CFG-AZ-1`, `CFG-WS-1`.

## Inputs

1. `mf doctor --live --json --bundle support.json` → `support.json` (redacted): `preflight` and, per workspace,
   `token → list_jobs → get_run → metric_names → metric_history → naming → files`, each with `detail` and, when
   failed, `diagnosis.cause` + `fixes`.
2. `mf checks --json`.

## Task

Find the first failing step per workspace and its cause. If it is environmental (login, tenant, CA bundle, proxy,
role, missing package) give the exact fix — **no code change**. If it is the interface configuration
(`config/azure.local.yaml`: subscription, resource group, workspace, tenant placeholders) give the corrected field
names (values stay with the user). If it is code, name the place from `AGENTS.md → Fixing a connection problem` and
the failing check, and stop.

## Output

Per workspace: failing step, cause (quoted from the bundle), fix, and who applies it.

## Scope

`config/azure.local.yaml`, `model-forensic.yaml` (git-ignored). Nothing else.

## Validation

`mf doctor --live` → every step green; `mf checks` → `CFG-AZ-1`, `CFG-WS-1` pass.

## Rules (always)

- Read-only towards Azure: list / get / read calls only — no job submission, no tag edits, no deletes.
- No site details in git: subscription / tenant ids, resource groups, workspace names, hostnames and user names go
  only into git-ignored files (`config/*.local.yaml`, `config/projects/*.local.yaml`, `model-forensic.yaml`,
  `config/test-space/`).
- Change only the files listed under *Scope*. Never edit `backend/src/` or `web/src/` to fit one project — if the
  fix needs core code, stop and describe the interface gap (`docs/projects.md`).
- Cite evidence (`file:line`, the JSON field, the measured number) for every claim; never invent metric names,
  config keys or timings. If the evidence is not there, say so.
- Finish with the *Validation* command and quote its result.
