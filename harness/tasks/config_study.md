# Task: configuration — which keys matter and how they are resolved

Find how a run's configuration is put together, so config differences between runs can be judged.

1. The config system (files, inheritance / base configs, overrides from the command line or pipeline stage), the
   order in which values win, and where the **resolved** config is written (the file Model Forensic should read).
2. The keys that change the model or the training in a meaningful way — architecture (backbone / neck / head / query
   counts), losses and weights, optimizer and schedule, data (datasets, sampling, augmentations, sensor setup),
   evaluation (ranges, thresholds) — with their exact flattened names as in the brief.
3. Keys that differ between runs but do not matter (paths, seeds when replicates are intended, logging / checkpoint
   frequency, worker counts) — so they can be set aside.
4. Keys whose meaning depends on another key (a weight only used when a switch is on; a value overridden per stage).

Write `fact` entries for meaningful keys, `caveat` entries for coupled keys, and a `constraint` listing the
runtime-only keys. Longer explanation: `<inbox>/docs/<codebase>/configs.md`.
