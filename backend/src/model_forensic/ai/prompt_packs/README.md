# Prompt packs

Each `*.yaml` file here is a pack of prompt templates for the AI investigator. Extra packs can live
anywhere listed under `prompt_dirs:` in `model-forensic.yaml` — copy one of these files, edit, done.
Ids must be unique across all packs. To change a built-in prompt, add a new `version` instead of
editing the old one, so past investigations stay reproducible (`prompt_id@version`).

```yaml
pack: camera                     # pack name
category: Camera branch          # default category for prompts in this file (UI grouping)
include:                         # default evidence for every prompt in this file (merged with per-prompt include)
  series: ["train/loss*"]
prompts:
  - id: cmp_camera_backbone      # unique id
    version: 1
    title: Did the stronger camera backbone help?
    description: One line shown in the picker.
    tags: [camera, backbone, comparison]
    requires: [run_id, baseline_run_id]   # baseline_run_id => comparison prompt (needs a baseline)
    include:
      logs: errors               # none | errors | tail | errors+tail   (logs are fenced as untrusted)
      series: ["train/loss/*", "val/*"]   # metric-key globs → compact curve digests
      config_keys: ["*backbone*", "*img*"]  # config-path globs → side-by-side values
      slices: true               # per-slice metrics (range/class/...) for single-run prompts
    template: |
      Compare {{run_id}} against {{baseline_run_id}} ...
      {{#backbone}}The new backbone is {{backbone}}.{{/backbone}}
```

## What the model always receives

Whatever the template, the context contains: run metadata (status, commit, compute, duration), the
metric deltas against the baseline with seed-noise z-scores (when a baseline is given) or final
metric values, the semantic config diff, and every deterministic finding with its evidence. The
`include` block adds to that. Every item has an id (`E12`) the model must cite; numbers it states
are checked against the cited evidence.

## Variables

`run_id`, `baseline_run_id` and `run_ids` (all runs in scope, for multi-run prompts) are filled
automatically. Others are typed into the UI (or `mf ask --var key=value`):

| variable | meaning |
|---|---|
| `change_description` | what changed, in your words ("ResNet-50 → ConvNeXt-B, 6 → 4 FPN levels") |
| `backbone` | backbone name |
| `sensor` | camera / lidar / radar |
| `component` | module under study ("radar FPN", "decoder") |
| `focus_metrics` | metrics to focus on ("NDS, mATE, AMOTA") |
| `slice_dimension` | slice to analyse (range, class, weather, occlusion) |
| `modalities`, `temporal_window`, `loss_terms`, `metric_name`, `evaluation_split`, `dataset`, `model` | as named |
| `hypothesis` | a claim to test |
| `budget_gpu_hours` | budget for proposed experiments |
| `notes` | anything else the model should know |

Optional text uses sections: `{{#var}} … {{var}} … {{/var}}` is dropped when `var` is empty.

## Tips for local models

* If your server or model does not support tool calling, set **Max tool steps = 0** in Settings; the
  model then answers from the evidence the template includes (that is what `include` is for).
* Keep `series` globs narrow on small-context models: each curve costs one line, but logs cost more.
