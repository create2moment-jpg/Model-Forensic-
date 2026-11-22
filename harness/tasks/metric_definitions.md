# Task: metric definitions

For the evaluation metrics in the brief, find **how each is computed**.

1. The combined / headline metric: which components and weights (the "ruler"), which config file selects it.
2. Per-class metrics: matching criterion (distance or IoU thresholds), score threshold of working points, how the
   target recall in a working-point name relates to the measured recall.
3. Names that mislead (a "score" that is not AP, an error metric that is undefined when ground truth is constant).

Write `fact` entries with code refs; `caveat` entries for misleading names; a `constraint` where a metric must not be
compared across runs with different rulers or thresholds.
