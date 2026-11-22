# Prompt: diagnose a slow, stalled or failed prefetch

**Problem**: a prefetch task is slow, stalled, failed, or shows no progress.
**Checks**: `CFG-PF-1`.

## Inputs

1. `GET /api/v1/prefetch/<id>` — `phase`, `phase_times`, `alive` / `heartbeat_age_s`, `stalled`, `retries`,
   `item_states`, `in_flight`, `progress` (determinate or not), `diagnosis`, `next`.
2. `GET /api/v1/prefetch/<id>/events?after=0` — structured events (`phase`, `file`, `retry`, `stall`, `recovered`,
   `error`).
3. `GET /api/v1/settings/storage` (workers, free space).

## Task

Answer the five questions with evidence: is it running (heartbeat), what is it doing (phase, files in flight), how
much is done (counts; a percentage only when `progress.determinate`), what is blocking it (each `diagnosis` cause
with its evidence), what happens next (`next`). Rank the causes; separate network / backend causes (retries,
low throughput, stalled stream) from local ones (workers, disk). Propose the smallest fix: resume, more workers,
another storage folder, retry later.

## Output

Five short answers, the ranked causes with the event lines that prove them, and the fix.

## Scope

`model-forensic.yaml` → `download_workers` only, if more workers are the fix (or Settings → Storage).

## Validation

After the fix: `GET /api/v1/prefetch/<id>` → `phase: completed` (or `downloading` with `stalled: false` and
`alive: true`); `mf checks` → `CFG-PF-1` pass.

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
