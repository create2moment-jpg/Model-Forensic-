# Prompt: run the configuration checks and repair what fails

**Problem**: something is misconfigured, or you are setting Model Forensic up on a new machine.
**Checks**: all — `CFG-AZ-1 · CFG-WS-1 · CFG-PROJ-1 · CFG-MC-1 · CFG-TASK-1 · CFG-TASK-2 · CFG-TS-1 · CFG-PF-1 ·
CFG-ST-1 · CFG-LLM-1 · CFG-GIT-1`.

## Inputs

1. `mf checks --json` — per check: `id`, `severity`, `status`, `current`, `expected`, `remediation`, `prompt_path`,
   `validation`.
2. `mf setup check --json` (target preconditions R*/P*/C*) and `mf paths`.

## Task

Work through the failing checks, critical first. For each: restate *current* vs *expected*, apply the remediation
inside the scope below, or hand over to the prompt named in `prompt_path` when it is more specific. A check you
cannot fix within the scope: explain why and stop.

## Output

Per check: what you changed (file + the lines, without site values in anything that is committed) and the
validation result.

## Scope

The allowed adaptation area only: `model-forensic.yaml`, `config/azure.local.yaml`, `config/team.local.yaml`,
`config/projects/*.local.yaml`, `config/test-space/test-space.yaml`, `.gitignore` (adding ignores only).

## Validation

`mf checks` → no failing `critical` check (exit code 0); each repaired check passes.

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
