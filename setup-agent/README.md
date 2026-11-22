# setup-agent — connecting Model Forensic to a target project

Instructions for a **local coding agent** (Copilot, a local LLM, …) or a person, to connect a target project to
Model Forensic **without changing Model Forensic's core**. This folder is the developer / operations interface layer;
it is not part of the forensic engine.

## The protocol

```
DISCOVER → DECLARE PRECONDITIONS → CHECK TARGET → REPORT GAP → ADAPT INTERFACE → VALIDATE → RUN
   01              02                  03             03             04             05       05
```

| Step | File | Output |
|---|---|---|
| Discover what actually exists | `01_target_discovery.md` | discovery notes |
| Declare every precondition | `02_preconditions_and_expectations.md` | checklist |
| Compare with the target, report the gap | `03_compatibility_check.md` | `mf setup check --json` + gap report |
| Adapt only the target-facing configuration | `04_interface_adaptation.md` | files in the allowed area |
| Validate (contract, smoke, compatibility again) | `05_validation.md` | `mf projects check`, `mf setup check --live` |
| Several projects side by side | `06_multi_project_setup.md` | one profile per project |

## Boundaries (non-negotiable)

* **Allowed to change** (all git-ignored, local to this machine): `model-forensic.yaml`, `config/azure.local.yaml`,
  `config/team.local.yaml`, `config/projects/<id>.local.yaml`, `config/knowledge/`, `config/plugins/`,
  `config/setup/`. `mf setup check --json` lists them under `allowed_paths`.
* **Never change for one project**: `backend/src/`, `web/src/`, `harness/`, `setup-agent/`, tests. If the fixed
  interface cannot express the target, **report the interface gap** and propose a general interface change
  (`docs/projects.md` → *If the contract cannot express what a project needs*). Do not add project-specific code.
* **Never write to Azure** (read-only tool) and **never commit** the local files above — private paths, workspace
  names and credentials stay on this machine.
* **Never assume** a target matches expectations: every claim in the report comes from something you inspected or
  a command you ran.

## Bootstrap (clean checkout → working project)

```bash
python -m venv .venv && . .venv/bin/activate && pip install -e "backend[azure]"
(cd web && npm ci && npx vite build)
scripts/set-project-path.sh /path/to/project && source ~/.bashrc     # PROJECT_PATH
mf setup check                                    # what is satisfied / missing — read it before changing anything
mf init                                           # Azure workspaces → config/azure.local.yaml (if Azure ML)
cp config/projects/<closest>.example.yaml config/projects/<id>.local.yaml   # then adapt (04)
mf projects check <id> && mf setup check --live   # validate (05)
mf serve
```

## Safe upgrade

Pull a new Model Forensic version, then run `mf setup check --live`. Profiles carry `contract_version`; a newer
contract is announced in `CHANGELOG.md` and `docs/projects.md` with a migration step. Local files are never touched
by an upgrade.

## Repair prompts

Once connected, `mf checks` names a prompt in [`prompts/`](prompts/README.md) for every failing configuration
check (interface, metric columns, task costs / Test Space, prefetch, plots, uncertainty weighting, configuration).
