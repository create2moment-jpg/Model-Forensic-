# 04 — Interface adaptation (target-facing configuration only)

Goal: the **smallest change, inside the allowed area**, that closes the gaps from 03.

Allowed files (git-ignored): `model-forensic.yaml`, `config/azure.local.yaml`, `config/team.local.yaml`,
`config/projects/<id>.local.yaml`, `config/knowledge/`, `config/plugins/`, `config/setup/`.

| Gap | Where to close it |
|---|---|
| no workspace | `mf init` (writes `config/azure.local.yaml`) or `backends:` in `model-forensic.yaml` |
| runs in exported folders | `local_roots:` in the project profile (root relative to `PROJECT_PATH`) |
| project's metric names | `metrics.aliases` (exact) / `metrics.rules` (`{match: regex, to: replacement}`) |
| headline metric | `metrics.headline` |
| stage names | `stages:` (glob → setup / data / training / evaluation / export / analysis) |
| code diff / evaluation / configs elsewhere | `artifacts.code_diff` / `evaluation` / `configs` (globs) |
| project words | `terminology:` |
| documentation | `knowledge_sources:` (folders, relative to `PROJECT_PATH`) |
| who is "me" | `defaults.me` in `config/azure.local.yaml` (or Settings → Azure) |
| storage on other disks | `data_dir` (and the paths in Settings → Storage) |
| a project-specific analysis | a local plugin in `config/plugins/` (see `docs/development.md`) — never in `backend/src` |

**Forbidden**: editing `backend/src/`, `web/src/`, `harness/`, `setup-agent/`, tests, or adding any `if project ==`
logic anywhere. If the gap cannot be closed with the table above, it is an interface gap — write it down (what the
target has, which interface lacks it, a general proposal) and leave the code alone.

Make one change at a time, then re-run `mf setup check` to see the item move to *satisfied*.
