# Prompt: test a performance hypothesis in the Test Space

**Problem**: you believe something about task cost ("metric histories are heavy because of one request per
metric", "the disk cache makes warm opening ≥ 5× faster") and want it confirmed or refuted by measurement.
**Checks**: `CFG-TS-1`.

## Inputs

1. The hypothesis in one sentence.
2. `GET /api/v1/test-space` (settings, sample runs, production timings).

## Task

Translate the hypothesis into one supported type — `cost_class` (`expect`: lightweight · medium · heavy ·
very_heavy), `warm_speedup` (`value`: factor), `under_ms` (`value`: bound on cold p90) — choose the task kind and
the sample runs, and say what result would refute it. After the run, read `verdict` (supported · refuted ·
inconclusive) and `stats`; if inconclusive, say what is missing (samples, errors, no warm sample).

## Output

The request body for `POST /api/v1/test-space/experiments`, the refutation criterion, and afterwards the verdict
with its numbers. Never restate a hypothesis as confirmed without a `supported` verdict.

## Scope

None (the experiment runs in the application).

## Validation

`GET /api/v1/test-space/experiments/<id>` → `verdict.result` and `verdict.why`.

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
