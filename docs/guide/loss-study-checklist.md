# Studying losses: questions to ask, where to look, what to try

The Metrics tab of a run (and Compare → Metrics) opens with **Training health questions**: each question below that
the data can answer is answered from the run's series — ✗ problem, ! worth a look, ✓ fine, · not logged — with a
**Plot** button, *where to look* and *what to try*. `mf questions --run <id> [--baseline <id>] [--problems]` prints
the same for a terminal or a local LLM. Questions marked *(judgement)* need you: they depend on data, labels or
evaluation the logs do not show.

Work top-down: a problem early in the list (the optimisation is broken) makes everything below it unreliable.

## 1. Is the optimisation healthy?

| Question | Look at | If yes — try |
|---|---|---|
| Is the loss **diverging** (turns upward, ends above its minimum)? | total and terms; zoom minimum → end; Section summary | lower peak LR / longer warm-up; clipping; what changed at that step (LR restart, stage change) |
| Any **NaN / Inf**? | NaN badge on the chart; Logs around that step | fp16 loss scaling; zero normaliser (no matched boxes); clipping |
| **Spikes**? | total + grad norm + LR at the spike | clipping; inspect those batches; lower LR |
| **Oscillation** late in training? | total with smoothing 0 | lower LR / larger effective batch; sampler alternating datasets |
| Gradients **growing** or **clipped all the time**? | gradients set, with LR | clip near the healthy median; find the module (per-module norms) |
| Is the **LR schedule** what the config says (warm-up length, decay, final LR)? *(judgement)* | learning-rate set | fix the schedule before anything else |

## 2. Is the budget right?

| Question | Look at | If yes — try |
|---|---|---|
| **Still learning** at the end? | last 20% of the total | train longer, or same budget with higher LR |
| **Plateau early** (most of the run adds < 5% of the drop)? | where the curve flattens vs the LR | decay at the plateau; more capacity / data; stop earlier |
| Does the val loss **rise** while train falls (**overfitting**)? | train + val loss in one chart | checkpoint at the val minimum; augmentation / regularisation; shorter schedule |
| Large **train / val gap** from the start? *(judgement)* | val / train ratio | domain gap between train and val data; label differences |

### Convergence — what "converged" means here

The question **Has the model converged?** reads the loss, the LR and the headline evaluation metric together:

| Verdict | Evidence | Next step |
|---|---|---|
| **Converged under this schedule** | the last 20% of training made < 10% of the loss drop, the LR has decayed, evaluation is flat | more steps buy little: shorten, or spend the budget on capacity / data |
| **Still learning** | the last 20% still made > 10% of the drop | longer schedule; with a decayed LR the run is under-trained |
| **Stalled at a high LR** | flat loss while the LR is still > 30% of its peak | the loss sits at the noise floor of that LR — decay it (schedule-bound plateau, not convergence) |
| **Loss converged, evaluation still improves** | flat loss, evaluation up > 1% in the last 20% | judge by evaluation; evaluate more often, pick the checkpoint by it |
| **Not converging** | the loss rises or ends above its start | fix the optimisation first |

Convergence is always *relative to the schedule*: a lower final LR or more steps usually moves a "converged" loss
further. Training loss converging also says nothing about generalisation — check the val loss (overfitting) and the
evaluation metric, and compare final numbers only between runs that converged the same way.

## 3. Are all tasks learning?

| Question | Look at | If yes — try |
|---|---|---|
| Does a **term rise** while the total falls? | train losses, One chart, Normalise | rebalance weights; check its targets; per-head LR |
| Does **one term dominate** (> 70% of the sum)? | train losses | intended? compare with the weights; normalise per term |
| Is a term **flat** from the start? | its chart; its weight in Setup | enabled? targets present? frozen module behind it? |
| Does the **weighting mute** a task (σ grows, weight share collapses)? | Uncertainty tab | clamp / init σ; fixed weight for the task you care about |
| Is the logged total **comparable** between runs (same weights, no +log σ)? *(judgement)* | config diff of the weights | compare unweighted terms instead |
| Do terms have **targets in every dataset / setup** (velocity, attributes)? *(judgement)* | knowledge: `label_targets` | mask or drop the term where labels lack it |

## 4. Does the decoder refine?

| Question | Look at | If yes — try |
|---|---|---|
| Is the **layer refinement diverging** (last layer worse than an earlier one, or layers rising)? | Slice breakdowns → per-layer readout (final value · change from the previous layer) | lower decoder LR / warm-up; reference-point update (detached?); check the index order |
| Do the **later layers still help** (≥ 1% each)? | per-layer readout | fewer layers; give later layers new inputs (other modality, memory) |
| Does the gap between first and last layer **shrink** over training? *(judgement)* | the family in One chart | deep supervision saturating; normal late in training |
| Do **denoising** losses fall faster than matched ones? *(judgement)* | DN vs matched terms | noise scale / groups; DN only helps early |

## 5. Do the inputs carry information?

| Question | Look at | If yes — try |
|---|---|---|
| Is a **module or modality dying** (features → 0, dead gradients)? | features / gradients sets; filter by modality | dropped by design? (knowledge); normalisation / activation; LR multiplier; pre-trained weights loaded? |
| Does each modality **contribute** (ablation runs: no_camera / no_radar)? *(judgement)* | Compare an ablation run; filter modality | fusion design; modality dropout rate |
| Are **queries** used (matched ratio) and **not duplicated**? | query statistics | query init / count; self-attention with memory; one-to-many matching early |

## 6. Against the baseline (Compare)

| Question | Look at | If yes — try |
|---|---|---|
| Is the candidate's loss lower — **and does it stay lower** (early advantage lost)? | Compare → Metrics, where curves cross | longer schedule / lower final LR |
| **Which terms and layers** got worse? | Deltas & config filtered to losses | trace each to a config difference |
| Does the lower loss give **better evaluation**? | loss vs NDS / mAP | overfitting; loss vs metric mismatch (`metric_definitions`); seed noise (replicates) |
| Is the difference **larger than seed noise**? *(judgement)* | baseline replicates (significance pill) | run baseline seeds before concluding |
| Which **slices** moved (class × distance × modality)? | filters + Slice breakdowns | where to look for the cause |

## Stepping up: from "sub-optimal" to the next experiment

1. **Fix what is broken** (sections 1–2): divergence, NaN, spikes, schedule. Re-run before studying anything else.
2. **Use the budget** (section 2): longer / shorter, decay at the plateau.
3. **Balance the tasks** (section 3): the term or head that loses is usually the cheapest gain.
4. **Architecture** (sections 4–5): layers that do not refine, modalities that do not contribute, idle queries —
   study the code with the harness tasks `head_and_queries`, `neck_and_fusion`, `backbone`, `loss_study`.
5. **Confirm** (section 6): one change at a time against a baseline with replicates; write the Study.

See also: [sparse multimodal detection](sparse-multimodal-detection.md) (what the losses and layers are).
