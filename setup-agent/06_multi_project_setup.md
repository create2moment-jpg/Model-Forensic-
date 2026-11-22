# 06 — Several projects side by side

One installation serves many projects; each is a profile in `config/projects/<id>.local.yaml`.

1. One profile per project (start from `sensor-fusion`, `lane-detection` or `road-attributes` `.example.yaml`).
   Give each a unique `id`. A workspace / run folder belongs to **one** project — split shared workspaces with
   `experiments:` globs only if the runs really belong to different projects.
2. Each project keeps its own metric mapping, stage roles, artifact locations, terminology and documentation
   folders; none of them change the core or another project.
3. Check each: `mf projects list`, `mf projects check <id>`.
4. In the UI, the header's project selector scopes runs, search and experiment lists to one project ("All projects"
   shows everything).
5. Removing a project = deleting its profile; nothing else references it.

Never solve a difference between projects by changing code — if two projects need something the profile cannot say,
that is an interface gap (see 04).
