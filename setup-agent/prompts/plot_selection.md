# Prompt: choose plots for a question (the plotter)

**Problem**: you want the plots that answer a question about a run, as validated plot specs.

## Inputs

1. `GET /api/v1/plots/schema` — the PlotSpec v1 JSON Schema.
2. `GET /api/v1/runs/<run>/plots/suggest` — the rule-based plots and, through their `series`, the run's keys.
3. The question.

## Task

Reply **only** with a JSON array (≤ 4) of PlotSpec v1 objects. Use only metric keys the run has (copy them from the
inputs); `run` is the run id. `transform: exp_neg2` only for `task_log_sigma` keys; a log y axis only for strictly
positive losses; few, focused series; `question` says what each plot answers.

## Output

The JSON array. The application validates every spec (schema + keys + scale) and lists rejected ones with the
reason — fix and resend those.

## Scope

None (specs are data, not files).

## Validation

`POST /api/v1/plots/validate` with each spec → `ok: true`.

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
