# Prompt: profile task costs in the Test Space

**Problem**: the configured cost of a task kind does not match this site (`CFG-TASK-2`), or nothing is measured yet.
**Checks**: `CFG-TASK-2`, `CFG-TS-1`.

## Inputs

1. `mf checks --json` (the failing checks).
2. `GET /api/v1/test-space` (settings, production timings, existing experiments and proposals).
3. A list of finished runs of typical size (`mf runs` / the Runs page; ids only).

## Task

1. Propose `config/test-space/test-space.yaml` (from `config/test-space/test-space.example.yaml`): 3–5 sample runs
   that are finished and representative (one small, one large pipeline), `repetitions` (≥ 3), `min_samples` (≥ 5).
2. For each kind to profile, the experiment to run: `POST /api/v1/test-space/experiments {kind, runs, repetitions}`.
3. After it finished: read `stats.cold` / `stats.warm` (p50 · p90 · p99, n, errors). Is the evidence enough
   (n ≥ min_samples, no errors, cold > warm)? Recommend *propose* or *repeat with more samples*.

## Output

The settings file content, the list of experiments, and per finished experiment a verdict with the numbers.

## Scope

`config/test-space/test-space.yaml` only.

## Validation

`GET /api/v1/test-space/experiments/<id>` → `state: done`, `stats.cold.n ≥ min_samples`; `mf checks` → `CFG-TS-1` pass.

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
