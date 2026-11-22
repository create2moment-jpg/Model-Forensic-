# Task: backbone (per modality)

Find how each sensor's **backbone** is built and trained, so a reader of the runs knows what the feature statistics
and gradient metrics in the brief refer to.

1. Per modality (camera, radar, lidar, …): the backbone class and its config keys (depth / width, stages out,
   pre-trained weights and where they are loaded, frozen stages, norm layers frozen or trained, input resolution
   or voxel / pillar size, number of input frames / sweeps).
2. Learning-rate multipliers or separate optimizer groups for the backbone (e.g. `backbone lr × 0.1`), weight decay
   exceptions, gradient checkpointing, mixed precision.
3. Which logged series observe the backbone (feature RMS / std / zero-fraction, gradient norms, dead-gradient
   fractions, update ratios — match the brief's names) and at which point in the network they are measured.
4. What makes those series easy to misread: frozen stages (zero gradient is expected), BN in eval mode, a modality
   dropped by an ablation switch (its features are all zero by design), different input normalisation per setup.

Write `fact` entries (one per modality, with config keys and code refs) and `caveat` entries for the traps.
Put a longer explanation (diagram in words: input → stages → outputs, shapes) in
`<inbox>/docs/<codebase>/backbone.md`.
