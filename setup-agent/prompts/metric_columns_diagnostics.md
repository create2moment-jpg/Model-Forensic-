# Prompt: diagnose empty or wrong metric columns

**Problem**: the Runs table shows no metric columns, empty cells, or columns that do not match the project.
**Checks**: `CFG-MC-1`.

## Inputs

1. `POST /api/v1/runs/metric-columns {"runs": [...]}` — ranked columns with coverage, the default columns, the
   project headline, and the diagnosis `MC-1` per workspace (runs without values, cause, fillable).
2. `mf inspect <downloaded run folder> --json` → `messages` with code `naming.unmapped` (metric names without a split).
3. The project profile (`config/projects/<id>.local.yaml`) if there is one.

## Task

Decide which of the three causes applies, with the evidence:
(a) the listing carries no metric values (Azure ML) → fill them (`POST /api/v1/runs/summaries`);
(b) metric names do not map to a split → a naming rule in the project profile (`metrics.aliases` / `metrics.rules`);
(c) the wanted headline is not first → `metrics.headline` in the project profile.
Never hard-code metric names in code; the columns come from the data.

## Output

The cause, the evidence lines, and the profile change (YAML) if (b) or (c).

## Scope

`config/projects/*.local.yaml`.

## Validation

`pytest backend/tests/unit/test_metric_columns.py` (smoke test) passes; `POST /api/v1/runs/metric-columns` →
`runs_with_values` > 0 and the expected `default`; `mf checks` → `CFG-MC-1` pass.

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
