# AGENTS.md — guide for coding agents (local LLMs included)

Model Forensic is a **read-only** forensic workbench for Azure ML training runs: FastAPI backend + CLI `mf`
(`backend/`), React/TypeScript UI (`web/`), training-side instrumentation kit (`instrument/`).

## Hard rules

1. **Never write to Azure.** Only list/get/read calls. No job submission, no tag edits, no deletes.
2. **No site details in git.** Subscription ids, tenant ids, resource groups, workspace names, hostnames, user
   names live only in `config/azure.local.yaml` / `config/team.local.yaml` / `model-forensic.yaml` (git-ignored).
   Tests use invented names.
3. **Deterministic first, AI grounded.** Analyses never call an LLM; AI output passes `ai/grounding.py`.
4. Every change comes with a test; keep `ruff check`, `mypy --strict` and `pytest` green.

## Commands

```bash
python -m venv .venv && . .venv/bin/activate && pip install -e "backend[dev,azure]"
cd backend && ruff format src tests && ruff check src tests && mypy src && pytest -q
cd web && npm ci && npx tsc -b && npx vite build          # UI
mf demo && mf serve                                     # demo data, http://127.0.0.1:8765
mf status / mf stop                                     # running servers (URL, pid, memory) / stop them
git pull && (cd web && npm ci && npx vite build) && mf stop; mf serve   # update: rebuild the UI, restart
mf doctor --live --json --bundle support.json           # diagnose the real workspace (redacted bundle)
mf checks                                               # configuration checks → repair prompt per failure
mf paths                                                # PROJECT_PATH (your data) and MF_HOME (Model Forensic's files)
```

## Fixing a connection problem

1. Read `support.json` from `mf doctor --live --json --bundle support.json`: `preflight` (packages, `az`, proxy/CA
   env, workspace config) and per workspace the checks `token → list_jobs → get_run → metric_names →
   metric_history → naming → files`, each with `detail` and, when failed, `diagnosis.cause` + `fixes`.
2. If the fix is environmental (login, tenant, CA bundle, proxy, role), apply it — **no code change**.
3. If it is a code problem, the path is:

| Step that failed | Code |
|---|---|
| credential / token | `infrastructure/connectors/azureml/runhistory.py::make_credential` |
| job listing (`list_jobs`) | `runhistory.py::RawARMJobs`, `fast_jobs`; mapping in `azureml/mapper.py::map_job` |
| `get_run`, stages | `azureml/connector.py::AzureMLRunSource.get_run`, `runhistory.py::RunHistoryGateway` |
| metric names / history | `runhistory.py` (data plane), names → keys in `infrastructure/connectors/naming.py` |
| files / downloads | `runhistory.py` (datastore + SAS), `application/service.py` downloads |
| unknown failure text | add a `Rule` in `azureml/diagnose.py` + an example in `tests/unit/test_diagnose.py` |
| config shape | `interface/azure.py` (reads `config/azure.local.yaml`) |

Retries, rate limiting and not-found/transient classification: `azureml/connector.py::_call`,
`shared/retry.py`. Tests use SDK fakes: `tests/unit/test_azure_runhistory.py`, `test_azure_mapper.py`,
`test_live_checks.py` — reproduce a failure there first, then fix.

## Fixing a parsing problem (real files differ from what the code expects)

`mf inspect <folder> --json` shows how each file was read and lists format-drift notes. Readers:
`analysis/evaljson.py` (evaluation JSONs), `analysis/sequences.py` (per-sequence parquets),
`infrastructure/connectors/naming.py` (metric names), `analysis/study_checks.py` (configs, ruler, checkpoint
monitor). Add a sanitised fixture under `backend/tests/fixtures/real_layout/` and a test in
`tests/unit/test_inspect_drift.py`. Unknown formats must produce a visible note, never a crash or a silent zero.

## Harness adaptation (you, inside the training project)

If you were given a Model Forensic **brief** (`mf harness brief --run <id> --task <task>`), you are the harness:
read the training code to answer the task and reply **only** with knowledge YAML per
`harness/knowledge.schema.json` (cite `file:line` in `source.code_refs`, never invent config keys or metric names —
use the brief's). The user imports it with `mf knowledge import answer.yaml`; it is stored as *proposed* until accepted.
Tasks and rules: `harness/README.md`, `harness/tasks/`.

**Where knowledge goes (always one place):** the brief's *Where to write* section names the inbox
(`mf knowledge where` prints it). Knowledge YAML → `<inbox>/<topic>-<yyyy-mm-dd>.yaml` with `status: proposed`;
longer explanations → `<inbox>/docs/<codebase>/<topic>.md`. Never write knowledge anywhere else. Model Forensic also
reads documentation folders of other codebases (`MF_KNOWLEDGE_SOURCE_<NAME>=<folder>`; ADR-0009).

## Configuration checks and repair prompts

`mf checks --json` (or `GET /api/v1/checks`) lists the `CFG-*` checks: severity, current vs expected, remediation,
the prompt under `setup-agent/prompts/` that repairs it, and the validation command. Pick the prompt from the
problem → prompt → scope → validation table in `setup-agent/prompts/README.md`; change only the files in its scope.
Task costs change only through reviewed Test Space proposals (`config/test-space/README.md`).

## Connecting a new target project

Follow `setup-agent/README.md` (protocol 01–06). Start with `mf setup check --json`; change only the files it lists
under `allowed_paths`; validate with `mf setup check --live` and `mf projects check <id>`. Never edit `backend/src/` or
`web/src/` to fit one project — report the interface gap (`docs/projects.md`).

## Layout

`domain` (pure types) ← `analysis` (deterministic plugins, `analysis/plugins/`) ← `application` (services,
studies, check-my-run, gate, noise) ← `infrastructure` (connectors, persistence, LLM) / `api` (FastAPI) / `cli`.
Wiring only in `container.py`. Settings in `settings.py`. Study book: `reports/study_book.py`.
More: `docs/architecture.md`, `docs/development.md`, `docs/troubleshooting.md`.
