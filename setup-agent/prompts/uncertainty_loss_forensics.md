# Prompt: forensics of uncertainty-based loss weighting

**Problem**: a multi-task model with learned uncertainty weights (L = Σ exp(-2·log σ_i)·L_i + log σ_i) under-
performs on one head, or its total loss falls while a task does not improve.

## Inputs

1. `GET /api/v1/runs/<run>/uncertainty` — per head: log σ start → end, effective weight and share, drift, bounds,
   own unweighted-loss progress, status (`switched_off · at_bound · dominant · drifting · settled`), the config keys,
   the `task_weighting` findings, plots.
2. The same for a baseline run, if there is one.
3. The training code that implements the weighting (cite `file:line`).

## Task

Explain per head what the weighting did and whether that is a problem: a switched-off head whose own loss is not
improving (the model gave up on it), a σ on its bound (the clamp decides, not learning), a dominant head, weights
still drifting at the end. Check the code: is the regulariser `+ log σ` present? Are σ initialised and bounded as
the config says? Is the weighting applied to the terms the config lists?

## Output

Per head: status, the numbers, the cause in the code (`file:line`) and a concrete experiment (bound σ for a head,
fixed weight, warm-up of the weighting) — as a hypothesis to test, never as a certainty.

## Scope

None in Model Forensic. Changes to the training code are proposals for its owners.

## Validation

After a new run: `GET /api/v1/runs/<new run>/uncertainty` → the head is no longer `switched_off` / `at_bound`, and
its own loss progress improved.

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
