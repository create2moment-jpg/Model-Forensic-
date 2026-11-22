# Prompt: filter facets — which values the class / modality / distance filters offer

**Problem**: the metric filters (Metrics view, Compare) miss a class, show a dimension under the wrong name
(`cls`, `obj_type`, `rng`), do not see the modality in names like `loss/rdr_head`, or offer noise (`seed`, `rank`).

## Inputs

1. `mf facets --brief --run <run id>` — the current facets, what the run's names carry with them, the dimensions no
   facet claims, and every metric / loss name as logged. (Same data: `GET /api/v1/facets?run=<run id>`.)
2. Optional, from the training code: where the class list, sensor names and distance bins are defined
   (e.g. the dataset's class names, the evaluation's distance ranges).

## Task

Write `config/facets.local.yaml` with **only the facets you change** (the others keep their defaults):

```yaml
order: [class, modality, distance, layer]   # filters shown first, in this order
hide_dims: [seed]                           # dimensions never offered as a filter
facets:
  class:
    aliases: [class, cls, obj_type]         # dimension names that mean this facet
    values: [car, truck, pedestrian, cyclist]   # display order (values seen in the names)
    value_aliases: {ped: pedestrian}        # spelling in the names → shown value
  modality:
    aliases: [modality, sensor]
    values: [camera, radar, lidar]
    value_aliases: {rdr: radar, cam: camera}
    from_name: true                         # find a value in the name when no dimension carries it
  distance:
    aliases: [distance, dist, rng]
    values: ["0-30", "30-60", "60-inf"]
```

Rules: every value and alias must occur in the brief's names (or be cited from the code as `file:line` in a YAML
comment); never invent classes or sensors; keep `only_listed: false` unless the names carry junk values.

## Output

The file content (YAML), saved to `config/facets.local.yaml`, and one line per facet saying where its values come
from (the brief's names or `file:line`).

## Scope

`config/facets.local.yaml` only (git-ignored; site-specific class names stay out of git).

## Validation

`mf facets --check config/facets.local.yaml` → valid; `mf facets --run <run id>` → every facet shows the expected
values and *no facet* lists only dimensions you meant to leave alone.

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
