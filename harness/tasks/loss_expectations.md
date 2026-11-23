# Task: loss expectations — what the code says the losses should do, for this run's config

You are the expert of this training codebase. Model Forensic will check the run's logged losses against what you
write, so the developer learns whether a surprising curve comes from the **data**, the **implementation**, the
**config** or the **schedule**. Read the code paths this run's config actually takes (resolved config first: the
brief lists its flattened keys and values).

For every loss term the run logs (names in the brief's *Loss and weighting series as logged*):

1. **name** — the logged key exactly as in the brief (a glob for families: `train/loss/cls[layer=*]`).
2. **formula** — the loss as implemented: type (focal / CE / L1 / smooth-L1 / GIoU / NLL …), its parameters, the
   normaliser (number of matched boxes? averaged over GPUs?), what it is computed on (matched queries, DN queries,
   one decoder layer or all).
3. **weight** and **weight_key** — its weight in the total for this config, and the flattened config key that sets it.
   `in_total: false` if it is logged but not added to the total.
4. **initial** — the value you expect at the first logged steps, derived from the code, with `why`. Examples:
   sigmoid focal loss with bias prior π: per positive ≈ −α·(1−π)^γ·log π; cross-entropy over C classes ≈ log C;
   L1 on normalised box targets ≈ mean |target| at init; uncertainty-weighted terms with σ initialised to s0 multiply
   by exp(−s0) and add s0. Give a `tolerance` (relative) that reflects how certain the value is (0.3 if rough).
   Leave `initial` out when it cannot be derived.
5. **range** — values the formula can never leave (`min: 0` for most losses; none for a term with +log σ that can
   be negative).
6. **direction** — `decrease` (default), `flat` (e.g. a regulariser), `increase`, or `any`.
7. **active_from** / **active_unit** — when a term switches on (warm-up of a term, DN only from epoch k, a stage that
   enables it). Leave out if always on.
8. **layers** / **layer_order** — how many decoder layers log this term for this config; `decreasing` when later
   layers should end lower (auxiliary losses on refined predictions).
9. **required** — `false` if it is logged only under conditions this run may not meet.
10. **code_refs** — `file:line` for the formula, the weight and the switch.

Then:

* **total** — the logged key of the total and how it is composed: `weighted_sum` (Σ weight × term over the terms with
  `in_total`), `sum`, or `other` (say how in `notes`).
* **config** — rules the code needs from this config that a developer can get wrong: a key that must equal / lie in a
  range / be one of (e.g. the number of classes matching the dataset, a loss weight that must be > 0 when its head
  is on, a sampler setting a temporal model needs). Only rules the code enforces or silently depends on.
* **notes** — anything that makes the logged values easy to misread.
* **runs** — this run's id (from the brief); optionally **when_config** (flattened key glob → value glob) when the
  same expectations hold for every run with that config.

## Output contract (mandatory)

Write **YAML only** to the path in the brief's *Where to write* section, valid against
`harness/expectations.schema.json`:

```yaml
runs: [<run id from the brief>]
codebase: <repository name>
generated_by: <your name / model>
generated_at: <yyyy-mm-dd>
config_source: <the resolved config file you read>
terms:
  - name: train/loss/cls
    formula: sigmoid focal loss (alpha 0.25, gamma 2), sum over matched + unmatched queries / num matched
    weight: 2.0
    weight_key: model.head.loss_cls.loss_weight
    initial: {value: 1.1, tolerance: 0.3, why: "focal loss at init with prior 0.01, ~900 queries, ~30 matched"}
    range: {min: 0}
    direction: decrease
    layers: 6
    layer_order: decreasing
    code_refs: ["models/heads/sparse_head.py:212", "configs/base.py:88"]
total:
  name: train/loss
  composition: weighted_sum
  code_refs: ["models/detector.py:140"]
config:
  - key: model.head.num_classes
    equals: 10
    why: the classification layer is built from this; the dataset has 10 classes
    code_refs: ["models/heads/sparse_head.py:57"]
notes: []
```

Rules: use the brief's exact metric names and config keys; never invent a term, a weight or a key; cite `file:line`
for every term; derive initial values from the code (show the arithmetic in `why`) or leave them out. Check the file
with `mf expectations --check <file>` before you finish.
