# Task: label targets

Find **which label sources provide which targets** (box geometry, class, velocity, acceleration, attributes,
tracking ids) for the datasets in the brief.

1. For each label source (ground-truth / manual vs online / auto labels), which targets are real, which are filled
   with defaults (zeros, NaN, copied values), which are missing.
2. Which losses and evaluation metrics become meaningless when a target is missing (they would measure the prediction,
   not an error).

Write `constraint` entries with `when.dataset_contains` (or `when.setup`) and `effects.ignore_metrics` globs over the
**logged** metric/loss names in the brief, plus a `caveat` explaining it. Example: manual labels have no velocity or
acceleration → set aside velocity/acceleration losses and metrics for runs trained or evaluated on them.
