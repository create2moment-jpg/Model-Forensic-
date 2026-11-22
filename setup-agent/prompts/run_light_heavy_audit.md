# Prompt: audit Light / Heavy task classification

**Problem**: a run page is slow to open, or Light Mode loads something heavy / misses something cheap.
**Checks**: `CFG-TASK-1`, `CFG-TASK-2`.

## Inputs (paste)

1. `GET /api/v1/test-space` → the `production` table (per task kind: configured cost, cold / warm p50 · p90 · p99, n,
   measured class, `light_mode`, `depends_on`, `why`).
2. `GET /api/v1/runs/<run>/tasks` for one slow run (state, elapsed, cache, error per task).
3. `backend/src/model_forensic/defaults/tasks.yaml` (read only) and `config/tasks.local.yaml` if present.

## Task

For every task kind: is its cost class supported by the measurements (cold p90 vs thresholds; n ≥ 5)? Is
`light_mode: true` only on kinds whose cold p90 stays under `light_mode_max_ms`? Do `depends_on` chains put a heavy
kind in front of a light one? Name the kinds that disagree with the numbers.

## Output

A table *kind · configured · measured · n · verdict · evidence*. For each disagreement: a Test Space experiment to
run (`kind`, sample runs, repetitions, hypothesis `cost_class`). Do **not** edit `config/tasks.local.yaml` yourself —
changes go through a reviewed Test Space proposal.

## Scope

None (report only). Proposals are created and accepted in the Test Space UI.

## Validation

`mf checks --json` → `CFG-TASK-1` and `CFG-TASK-2` pass after the accepted proposals.

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
