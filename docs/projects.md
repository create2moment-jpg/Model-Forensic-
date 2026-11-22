# Projects — connect a new target project without changing the core

Model Forensic is one engine for many projects (object fusion, lane detection, road attributes, …). Everything that
differs between projects is **configuration behind a fixed contract**, never a branch in the core.

## What a project is

A profile in `config/projects/<id>.local.yaml` (git-ignored — your private project details never reach the
repository). Start from one of the committed templates:

| Template | Kind of project | Shows |
|---|---|---|
| `sensor-fusion.example.yaml` | 3D objects from camera + radar / lidar, Azure ML pipelines | workspaces, experiment globs, stage roles, code-diff location |
| `lane-detection.example.yaml` | lanes, own metric names, runs exported to a local folder | `local_roots`, metric `aliases` and regex `rules`, terminology |
| `road-attributes.example.yaml` | per-segment attributes | data / training / evaluation stage names |

Fields: `id`, `title`, `description`, `contract_version`, `workspaces` (names from `config/azure.local.yaml`),
`local_roots` (`{name, root}`, root relative to `PROJECT_PATH`), `experiments` (globs; empty = all),
`metrics.aliases` / `metrics.rules` / `metrics.headline`, `stages` (glob → setup | data | training | evaluation |
export | analysis), `artifacts.code_diff` / `evaluation` / `configs` (globs), `terminology`, `knowledge_sources`.

A workspace belongs to **one** project (a second claim is reported and skipped). Several projects run side by side in
one installation; the header's project selector scopes every run list, search and experiment list.

## Add a project (from a clean checkout)

```bash
cp config/projects/lane-detection.example.yaml config/projects/my-project.local.yaml   # then edit it
mf projects list                       # loaded profiles, workspaces, problems
mf projects check my-project           # the adapter contract against its workspaces (read-only)
mf serve                               # pick the project in the header
```

A local coding agent can do this for you, following `setup-agent/` (discover → declare → check → report gap →
adapt only `config/` → validate → run).

## The fixed contract

* **Run sources** (`application/ports.py::RunSource`) — every adapter (Azure ML, the local layout, a project's
  own) must pass `projects/contract.py::check_run_source`: listing, consistent `get_run`, `NotFoundError` for unknown
  runs, canonical metric keys with aligned steps, safe relative artifact paths, downloads that resume from an offset,
  logs readable or absent. `tests/contract/` runs it on every built-in adapter and proves it catches a broken one;
  `mf projects check` runs it on real workspaces.
* **Profiles** — `projects/profile.py::ProjectProfile`, version `CONTRACT_VERSION` (1).
* **Metric names** — a project's names are mapped (`aliases`, then `rules`) *before* the core's canonical mapping
  (`infrastructure/connectors/naming.py::NamingRules`); analyses only ever see canonical keys. The raw name stays as
  provenance.

## Versions

`contract_version` in a profile is the contract it was written for. A profile newer than the installed Model
Forensic is refused with a clear message (upgrade Model Forensic). When the contract changes incompatibly, the
version goes up, the change is listed here with a migration step, and older profiles keep loading until a
deprecation note says otherwise. Version history: **1** — first version.

## If the contract cannot express what a project needs

Do not add a project-specific `if` to the core. Report the gap (what the project has, which interface lacks it) and
propose an interface change — a new optional profile field or a new adapter capability — that every project could
use. `setup-agent/04_interface_adaptation.md` tells a coding agent the same.
