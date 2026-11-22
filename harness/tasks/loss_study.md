# Task: losses in depth (main, auxiliary, denoising, weighting)

Go further than `loss_composition`: find every loss term **and how it reaches the total**, so a reader can tell a
real improvement from a re-weighting.

1. Matching: the assignment (Hungarian / one-to-many), its cost terms and their weights — and how they differ from
   the loss weights.
2. Per term (classification, box regression per component — centre, size, rotation, velocity —, IoU, depth,
   auxiliary dense / heatmap losses, per-modality auxiliary heads): the formula, the weight, which decoder layers
   contribute (all layers = auxiliary losses on intermediate layers, or only the last), the normaliser (number of
   matched boxes, averaged across GPUs?), and the logged name (match the brief's names and dimensions such as
   `layer=` / `term=` / `module=`).
3. Denoising losses: which terms, their weight, logged names.
4. Weighting: fixed weights, uncertainty weighting (σ per task, the regulariser), GradNorm or other schedules;
   warm-up of a term; terms switched on only in some stages.
5. What the logged total is (weighted? including the regulariser? only the last layer?) and which logged series to
   compare across runs when the weights differ.

Write `fact` entries per term with code refs, `caveat` entries for comparisons that mislead (e.g. "totals across runs
with different weights are not comparable; compare the unweighted terms"). Longer explanation:
`<inbox>/docs/<codebase>/losses.md`.
