# ADR-0012: Project profiles and a versioned adapter contract

Status: accepted

## Context
The same engine must serve very different projects (object fusion, lane detection, road attributes) without forks
and without project-specific conditionals in the core. Until now one installation had one global configuration.

## Decision
* A **project profile** (`config/projects/<id>.local.yaml`, git-ignored; committed `*.example.yaml` templates)
  declares the project's workspaces / local run folders, experiments, metric-name mapping, stage roles, artifact
  locations, terminology and documentation folders. Typed, validated (`ProjectProfile`), versioned
  (`contract_version`).
* The core reads profiles only through that model: search scoping (`project=`), naming rules attached to the
  workspaces a project owns, stage roles, artifact globs. A workspace belongs to one project — explicit identity,
  isolation by construction.
* The **adapter contract** is library code (`projects/contract.py`) as well as tests: the same checks run in CI
  over every built-in adapter, against a deliberately broken adapter, and on real workspaces (`mf projects check`,
  the setup agent's validation step).

## Consequences
+ Adding a project is configuration; three different example projects run side by side in one test
  (`tests/unit/test_projects.py`) with no core change.
+ A failed contract check names the broken guarantee (e.g. *downloads can resume from an offset*).
− Profiles cannot express arbitrary logic; a project that needs more must propose an interface change (documented
  in `docs/projects.md`), which is the intent.
