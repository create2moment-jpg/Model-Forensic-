# 03 — Compatibility check and gap report

Goal: classify **every** precondition from 02 against the discovery from 01, before changing anything.

1. Run `mf setup check --json` (add `--live` once workspaces are configured: it also runs the adapter contract).
   Each requirement has `status`: **satisfied**, **missing**, **incompatible**, **unknown**, **optional**, a
   `detail` and a `fix` naming the file to change. `allowed_paths` lists where changes are permitted.
2. Add what the command cannot see from your discovery (01): metric names that do not map, stage names, where the
   evaluation outputs and code diff are, the project's headline metric.
3. Write the **gap report** — human-readable and machine-readable:

```yaml
target: <project id>
satisfied: [R1, P2, …]
missing:
  - {id: C1, what: no workspace configured, fix: "mf init → config/azure.local.yaml"}
incompatible:
  - {id: metric-names, what: "eval/F1_lane is not a known name", fix: "metrics.aliases in config/projects/<id>.local.yaml"}
unknown:
  - {id: P1, what: PROJECT_PATH not set, how_to_resolve: "ask the user for the project root"}
interface_gaps: []        # things the fixed interface cannot express — do NOT work around them in code
risks: [...]
smallest_adaptation: [ordered list of file changes, all inside allowed_paths]
```

Rules: no "probably works". An item is *satisfied* only with evidence. If something needs a capability the
interface does not have, it is an **interface gap**: report it, propose a general interface change, and stop
there for that item.
