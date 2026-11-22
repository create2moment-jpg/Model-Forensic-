# Task: checkpoints and validation

Find how the **checkpoint to keep** and **validation** are configured.

1. The checkpoint monitor key and mode, whether that metric is actually logged under that exact name (compare with the
   brief's metric names), what happens when it is missing.
2. Whether a validation loop runs during training (and on which split), early stopping, evaluation-only stages.

Write `constraint` / `caveat` entries with code refs (e.g. "the monitor key is never logged, so 'best' = last").
