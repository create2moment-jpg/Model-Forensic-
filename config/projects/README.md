# Project profiles — many target projects, one unchanged core

A **profile** maps one target project onto Model Forensic's fixed interfaces. It is configuration, never code:
which workspaces / run folders belong to the project, which experiments, how its metric names map to the names the
core knows, what its pipeline stages are, where its code diff and evaluation files are, its own words for things, and
its documentation folders.

* `<id>.local.yaml` — your profiles (git-ignored: private project details never reach the repository).
* `*.example.yaml` — templates for three different kinds of project; copy one, rename it `<id>.local.yaml`, adapt it.
* `mf projects list` — what is loaded; `mf projects check <id>` — runs the adapter contract against the project's
  workspaces (read-only) and prints what holds and what does not.
* The UI shows a project selector once a profile exists; every run list and experiment list can be scoped to it.

A workspace belongs to one project. `contract_version` is the version of the profile contract the file was written
for; Model Forensic refuses a newer one with a clear message instead of guessing. See `docs/projects.md`, and
`setup-agent/` for the protocol a local coding agent follows to connect a new project.
