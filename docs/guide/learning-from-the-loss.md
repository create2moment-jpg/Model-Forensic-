# Learning more from the loss: research ideas

This is a reading list with a plan. It collects recent research on what a training loss can tell you, sorted into
two parts:

* **Part A, Model Forensic:** analyses the tool could add. Each idea says what data it needs, where it would plug
  in, and what it is worth. Ideas A1 and A2 work on the runs you already have.
* **Part B, the training codebase (SOTA, for inspiration):** changes to the model or the training recipe from
  recent papers. Each idea says which Model Forensic reading suggests trying it and how you would tell it worked.
  Read it when you have time; nothing here is a to-do.

Numbers quoted from papers are the authors' own and come from their benchmarks. Treat them as a reason to try an
idea, not a promise. With a single seed per run, judge a change only when it is larger than the run-to-run noise
(see the [loss study checklist](loss-study-checklist.md)).

What the tool already covers, so the ideas below don't repeat it:

* health questions (diverging, spikes, plateau, overfitting, term rising, flat or dominating, layer refinement,
  modality share, query and attention statistics)
* loss drift against a baseline
* expected losses from the codebase
* the decoder-layer and task-weighting analyses
* the Expert review, the dossier and the study book

---

## Part A: analyses to add to Model Forensic

**Built: A1 and A2.** Use `mf dynamics --run <id> [--baseline <id>] [--stage <s>]`, the Expert review card *Loss
dynamics*, or `GET /api/v1/runs/{run}/loss-dynamics`. Their readings also reach the review list, the dossier
(section 4, evidence ids `E-S…`), the study book and the ledger. A2 is also the health question *Does a loss term
train slower than the others?* (`term_rate`). Thresholds are in `defaults/analysis.yaml` → `loss_dynamics`.

**Built: A3–A5 (kit).** `MatchTracker`, `attn_logit_stats`, `output_logit_stats` and `layer_groups` in
`instrument/` (README: *Matching stability, logit growth, which decoder layers learn*). They are read in Model
internals → *Matching and logits*, and in the health questions `matching` and `logits`. Thresholds are in
`defaults/analysis.yaml` → `internals`. They need a training run that uses the new kit.

| # | Idea | Data it needs | Works on past runs | Value | Effort |
|---|---|---|---|---|---|
| A1 ✓ | What the learning-rate schedule did vs what the model learned | logged losses + LR | yes | high | **built** |
| A2 ✓ | Training rate of each loss term | logged losses | yes | high | **built** |
| A3 ✓ | Matching instability (epoch to epoch, layer to layer) | kit: matched GT per query on a fixed val batch | no | high for DETR-style heads | **built** |
| A4 ✓ | Early warning before divergence | kit: max attention logit, output logit norm | no | medium | **built** |
| A5 ✓ | Which layers are actually learning | kit: ‖ΔW‖/‖W‖ per layer group | no | medium | **built** (kit `update_ratio` + *Module learning health*; `layer_groups` for per-layer groups) |
| A6 | Class score vs box quality | evaluation outputs with score and IoU per prediction | if the files have them | medium | small |
| A7 | Gradient noise scale (is the batch size wasted?) | kit: per-device grad norms | no | low | small |
| A8 | Sharpness / edge of stability | Hessian power iteration | no | low (expensive) | large |
| A9 | Per-scene difficulty maps | per-scene training loss per epoch | no | low for now | medium |

### A1 · What the learning-rate schedule did vs what the model learned

**Idea.** Tissue et al. (2024) fit the whole loss curve with one formula of the learning-rate schedule:

  L(s) ≈ L₀ + A · S₁(s)^(−α) − C · S₂(s)

* S₁ is the forward area: the summed learning rate up to step s.
* S₂ is the annealing area: how much the rate has been lowered so far, weighted by recency.

Fitting one or two curves predicts the loss at any step under any schedule.

**What it would tell you**

* When a candidate's loss falls faster, how much of that is only an earlier or deeper LR decay, and how much
  remains after the schedule is accounted for. That remainder is the effect of the change.
* The loss a run would reach if it trained longer or decayed later (the fitted L₀ and curve).
* A fair comparison between runs of different length or schedule: compare the fitted L₀ and A, not the last
  value.

**Where it plugs in**

* Per loss term, next to loss drift: a reading such as "after the schedule, the candidate's `cls` loss is 3% lower;
  the visible 9% gap is mostly the earlier decay".
* A health question: "Is the gain only the schedule?"
* Observations, so the Expert review, the dossier and the study book pick it up.

**Caveats**

* The formula was derived for language-model pretraining with a fixed batch size; a later study found the forward
  term is not robust to batch-size changes.
* The tool must report the fit quality per term and say "does not fit" rather than extrapolate.
* Warm-up steps are excluded from the fit.

### A2 · Training rate of each loss term

**Idea.** GradNorm (Chen et al., 2018) measures each task's progress with the loss ratio
L̃ᵢ(t) = Lᵢ(t)/Lᵢ(0). The *relative inverse training rate* rᵢ(t) = L̃ᵢ(t) / mean_j L̃ⱼ(t) compares that progress
across terms. A value above 1 means the term trains slower than average.

**What it would tell you**

* Which head, modality, decoder layer or denoising branch lags, from when, and by how much.
* A number that is comparable between runs.
* It replaces the fixed thresholds of the *term flat* and *weighting* questions with a measured rate.

**Where it plugs in.** As a series per term (plot it like a metric), as a health question ("Which loss term is
starved?"), and in drift (a term whose rate changed against the baseline).

**Caveats**

* L(0) is noisy; use the mean of the first logged points after warm-up.
* It is a diagnosis, not a recommendation to re-weight: Kurin et al. (2022) find that a well-tuned plain sum of
  losses matches the multi-task balancing methods (see B4).

### A3 · Matching instability

**Idea.** DN-DETR defines an instability score: how often the Hungarian matching of queries to ground-truth boxes
on a fixed validation batch changes between epochs. *Detection Transformer with Stable Matching* (Liu et al.,
2023) studies the same instability between decoder layers.

**What it would tell you**

* Why the layer losses do not refine: the layers chase different targets.
* Whether denoising is doing its job: instability should fall when it works.
* Whether a change to the matching costs helped.

**Kit change.** Every N epochs, on one fixed validation batch, record the matched GT index per query for each
decoder layer. Log:

* the fraction of queries that changed match since the last measurement (epoch instability)
* the fraction whose match differs between consecutive layers (layer instability)

This costs a few scalars per measurement.

### A4 · Early warning before divergence

**Idea.** Wortsman et al. (2024) show that two failure modes of transformer training can be seen before the loss
moves:

* attention logits growing (queries and keys get large, not more aligned)
* output logits drifting

The authors predict an instability before it emerges. Moonshot's MuonClip caps attention logits for the same
reason.

**Kit change.** Record the maximum |q·k|/√d per attention block and the output-logit norm. The kit already records
attention statistics, so this is a small addition.

**Where it plugs in.** As a health question ("Are attention logits growing toward instability?") in the Model
internals view.

### A5 · Which layers are actually learning

**Idea.** The size of an update relative to the weights, ‖ΔW‖/‖W‖, is a layer's *effective* learning rate. With
weight decay it settles to an equilibrium (Kosson et al., 2024).

**What it would tell you**

* A backbone that hardly moves during finetuning, or a head that moves too much.
* A layer group that never reaches equilibrium.
* Whether the scale of each modality's encoder is balanced.

**Kit change.** At every logging step, record ‖ΔW‖/‖W‖ per layer group (backbone, neck, each modality encoder, each
decoder layer).

### A6 · Class score vs box quality

**Idea.** Rank-DETR and Align-DETR identify a mismatch between classification confidence and localisation quality:
confident boxes that are badly placed, or well-placed boxes with low scores. It is a common reason why the loss
falls but mAP does not.

**What it needs.** For matched predictions in the evaluation outputs, the score and the IoU (or centre distance for
3D). The tool computes their rank correlation per class and per distance band, and compares it between runs.

### A7–A9 · Lower value for this team

* **A7 · Gradient noise scale** (McCandlish et al., 2018): the ratio of gradient variance to the squared gradient
  norm estimates the largest useful batch size. Merrill et al. (2025) question how well it holds for Adam.
  Interesting only if you plan to change the batch size.
* **A8 · Sharpness / edge of stability:** the largest Hessian eigenvalue (preconditioned, for Adam) rises and
  settles at a threshold set by the learning rate. It is explanatory but expensive to measure, and A4 catches the
  practical cases more cheaply.
* **A9 · Dataset cartography** (Swayamdipta et al., 2020): per-example mean confidence and its variability across
  epochs separate easy, ambiguous and hard (often mislabelled) examples. It needs per-scene training losses, so
  revisit it if scene-level analysis becomes interesting.

### Suggested order

1. **A1 + A2.** They work on every run you already have and need no training change.
2. **One kit update with A3 + A4 + A5.** All three are a few scalars per logging step, in one Model internals view.
3. **A6**, if the evaluation files carry score and IoU per prediction.

---

## Part B: ideas for the training codebase (SOTA, for inspiration)

Each idea follows the same pattern:

* **Signal:** the Model Forensic reading that suggests trying it.
* **Change:** what you would change in the training code.
* **Check:** what should move in the next run if it works.

Run one change at a time against the same baseline, and record it in the ledger (`mf ledger add`).

### B1 · More positive supervision for a one-to-one matched decoder

**Signal**

* Decoder classification losses fall slowly.
* Layer refinement is weak (*layers diverge / saturate*).
* High matching instability (A3).

**Change.** Add one-to-many supervision during training only, so that inference cost does not change:

* **H-DETR:** an extra branch with one-to-many matching.
* **Group DETR:** duplicate query groups, each matched one-to-one.
* **Co-DETR:** auxiliary heads with classic dense assignments (ATSS / Faster R-CNN style) on the encoder output,
  plus extra positive queries built from them.

**Check**

* Faster early drop of the decoder losses.
* Lower matching instability.
* mAP at equal epochs.

### B2 · Denoising, extended to time

**Signal**

* Matching instability (A3).
* Slow convergence of the box losses.
* Temporal queries that age poorly (the *query age* readings).

**Change.** If denoising is single-frame, Sparse4D v3's *temporal instance denoising* adds noisy GT instances that
are also propagated across frames. This gives stable positive matches and more positives. Noisy instances are
dropped at test time.

**Check**

* Denoising loss and matched-query loss converge together.
* Better tracking metrics (AMOTA) if you track.

### B3 · Make the score mean box quality

**Signal**

* Low score-vs-IoU correlation (A6).
* Loss improves but mAP or NDS does not (*loss vs eval*).

**Change.** Train the classification target to carry localisation quality:

* Varifocal loss (VarifocalNet)
* Quality Focal Loss (Generalized Focal Loss)
* IoU-aware BCE (Align-DETR)
* Sparse4D v3's quality-estimation heads (centerness, yawness), used to re-rank

**Check**

* Higher score-vs-IoU correlation.
* AP at high IoU thresholds rises more than AP at 0.5.

### B4 · Loss weighting: measure first, re-weight second

**Signal**

* A term starved or dominating for a long time (A2, *term share*).

**Change**

* First fix the cause: the term's normaliser, its target coverage, an initialisation scale.
* If re-weighting is still needed, try a fixed re-weight before an adaptive scheme.
* Adaptive options: uncertainty weighting (Kendall et al., 2018), GradNorm, PCGrad (projects conflicting
  gradients).

Kurin et al. (2022) find that a plain sum with good regularisation matches these methods. Use them only when a
fixed weight clearly does not hold.

**Check.** The rate rᵢ of the starved term approaches 1 without other terms' rates rising.

### B5 · Modality imbalance in camera + radar fusion

**Signal**

* One modality's share falls in every layer.
* A modality is abandoned by the later layers (the *features* questions).

**Change**

* **Modality dropout:** drop one modality's features for a fraction of samples, so the model cannot ignore the
  weaker one.
* **On-the-fly gradient modulation** (OGM-GE, Peng et al., 2022): slow the dominant encoder's updates in
  proportion to its lead. It needs per-modality logits, which is hard with deep fusion. Adaptive Gradient
  Modulation addresses that.

**Check**

* The modality share stabilises.
* The single-modality ablation drops less.
* Evaluation per range band (radar matters most far away).

### B6 · Temporal fusion through sparse queries

**Signal**

* Velocity and attribute errors are high.
* Per-frame detections flicker.
* Memory readings show little use of the past.

**Change.** StreamPETR propagates the top object queries across frames through a memory queue. Each stored query
keeps:

* time offset
* centre
* velocity
* ego pose

A motion-aware layer norm handles moving objects. Far3D builds long-range detection on the same idea.

**Check**

* mAVE (velocity error) falls.
* Memory attention share rises.

### B7 · Learning-rate schedule: warm-up, stable, decay

**Signal**

* A1 says most of the gains come from the decay.
* The *plateau* or *convergence* question fires before the decay.

**Change.** A warmup–stable–decay schedule:

* a long constant phase at a high rate
* a short, sharp decay at the end

Wen et al. (2025) explain why the loss stays high in the stable phase and drops sharply in the decay (the "river
valley" picture). Practical gains:

* You can extend training without restarting: continue the stable phase, then decay again.
* A1 can predict the loss after the decay before you run it.

**Check.** The final loss at equal compute, and A1's fitted L₀.

### B8 · Stability before it breaks

**Signal**

* Spikes.
* Non-finite losses.
* Growing attention logits (A4).

**Change**

* QK-layernorm (normalise queries and keys before the dot product), or cap the attention logits.
* A smaller AdamW ε: Wortsman et al. find the default too large once gradients get small.
* Gradient clipping set from the logged gradient-norm distribution, not a guessed constant.

**Check**

* The spikes disappear at the same learning rate.
* A higher learning rate becomes stable.

### B9 · Optimizer

**Signal**

* A run is compute-limited and the schedule is already sound.

**Change**

* Muon: orthogonalised momentum for weight matrices, AdamW for the rest. Moonshot report AdamW-level results with
  about half the training FLOPs for language models.
* Later studies call the gains mixed and schedule-sensitive. Try it last, and only with A1 in place to compare
  fairly.

**Check.** Loss at equal compute, and A1's fitted curve.

### B10 · Cheap robustness

* **EMA of the weights:** evaluate an exponential moving average of the weights. It often smooths late-training
  noise.
* **Checkpoint choice by validation, not by last step:** this needs validation per checkpoint (see the *convergence*
  question).

---

## Sources

**Loss dynamics and schedules**

* Tissue et al., *Scaling Law with Learning Rate Annealing* — [arXiv 2408.11029](https://arxiv.org/abs/2408.11029v2);
  batch-size limitation: [arXiv 2512.13705](https://arxiv.org/html/2512.13705v1)
* Wen et al., *Understanding Warmup-Stable-Decay Learning Rates: A River Valley Loss Landscape Perspective* —
  [arXiv 2410.05192](https://arxiv.org/abs/2410.05192v3)

**Multi-task loss balancing**

* Chen et al., *GradNorm* — [arXiv 1711.02257](https://arxiv.org/pdf/1711.02257)
* Kendall et al., *Multi-Task Learning Using Uncertainty to Weigh Losses* — [arXiv 1705.07115](https://arxiv.org/abs/1705.07115v2)
* Yu et al., *Gradient Surgery for Multi-Task Learning (PCGrad)* — [arXiv 2001.06782](https://arxiv.org/pdf/2001.06782)
* Kurin et al., *In Defense of the Unitary Scalarization for Deep Multi-Task Learning* — [arXiv 2201.04122](https://arxiv.org/pdf/2201.04122)

**Detection transformers**

* Li et al., *DN-DETR* — [arXiv 2203.01305](https://arxiv.org/pdf/2203.01305)
* Liu et al., *Detection Transformer with Stable Matching* — [arXiv 2304.04742](https://arxiv.org/pdf/2304.04742)
* Jia et al., *DETRs with Hybrid Matching (H-DETR)* — [arXiv 2207.13080](https://arxiv.org/pdf/2207.13080)
* Zong et al., *DETRs with Collaborative Hybrid Assignments Training (Co-DETR)* — [arXiv 2211.12860](https://arxiv.org/pdf/2211.12860)
* Align-DETR — [arXiv 2304.07527](https://arxiv.org/html/2304.07527v2) · Rank-DETR — [arXiv 2310.08854](https://ar5iv.labs.arxiv.org/html/2310.08854)

**Score quality in detection**

* VarifocalNet — [arXiv 2008.13367](https://arxiv.org/abs/2008.13367)
* Generalized Focal Loss — [arXiv 2006.04388](https://arxiv.org/abs/2006.04388)

**Multi-view 3D detection and tracking**

* Lin et al., *Sparse4D v3* — [arXiv 2311.11722](https://arxiv.org/abs/2311.11722)
* Wang et al., *StreamPETR* — [arXiv 2303.11926](https://arxiv.org/abs/2303.11926v2) · Far3D — [arXiv 2308.09616](https://arxiv.org/pdf/2308.09616)

**Multimodal balance**

* Peng et al., *Balanced Multimodal Learning via On-the-fly Gradient Modulation (OGM-GE)* — [arXiv 2203.15332](https://arxiv.org/pdf/2203.15332)
* Adaptive Gradient Modulation — [arXiv 2308.07686](https://arxiv.org/pdf/2308.07686)

**Stability and optimisers**

* Wortsman et al., *Small-scale proxies for large-scale Transformer training instabilities* — [arXiv 2309.14322](https://arxiv.org/abs/2309.14322)
* Kosson et al., *Rotational Equilibrium* — [ICML 2024](https://proceedings.mlr.press/v235/kosson24a.html)
* *Muon is Scalable for LLM Training* — [arXiv 2502.16982](https://arxiv.org/html/2502.16982v1)
* McCandlish et al., *An Empirical Model of Large-Batch Training* — [arXiv 1812.06162](https://arxiv.org/abs/1812.06162);
  Merrill et al., *Critical Batch Size Revisited* — [arXiv 2505.23971](https://arxiv.org/pdf/2505.23971)
* Cohen et al., *Adaptive Gradient Methods at the Edge of Stability* — [arXiv 2207.14484](https://arxiv.org/html/2207.14484v2)

**Data**

* Swayamdipta et al., *Dataset Cartography* — [arXiv 2009.10795](https://arxiv.org/abs/2009.10795)
