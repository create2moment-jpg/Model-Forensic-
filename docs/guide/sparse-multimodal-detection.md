# Studying a sparse, query-based multimodal 3D detector

A guide for reading runs of a **sparse** detector: a fixed set of object queries, a transformer decoder that
cross-attends to camera / radar / lidar features, temporal query propagation (StreamPETR-style) — and how to study
each part in Model Forensic. Every architecture statement below is the *usual* design; what your code does is
project knowledge, collected with the harness tasks (`mf harness brief --run <id> --task <task>`, last section).

## 1. The pipeline in one picture

```
camera images ─ backbone ─ neck (FPN, scales) ─┐
radar points ── encoder / pillars ─ neck ──────┼─► tokens (+ 3D position embedding per modality)
lidar points ── voxel / pillar backbone ─ neck ┘                     │
                                                                     ▼
queries (learned + propagated from t-1, motion-compensated) ─► decoder layer 1 … L
     each layer: self-attn (queries ↔ queries, incl. memory) → cross-attn (queries → tokens) → FFN
     each layer predicts boxes + classes (auxiliary outputs); reference points refined layer by layer
                                                                     ▼
                         last layer → boxes at inference; top-k queries → memory for t+1
```

## 2. Losses: what is there and how it reaches the total

| Group | Terms (typical names) | Notes for reading runs |
|---|---|---|
| Matching | Hungarian assignment with cost = w_cls·focal + w_box·L1 + (w_iou·IoU) | costs are **not** losses; their weights can differ from the loss weights |
| Main detection | `loss_cls` (focal), `loss_bbox` (L1 on centre, size, rotation sin/cos, velocity), optional IoU / GIoU | per box component if logged (`term=` / `component=`) — velocity often has no target for some label sources |
| Auxiliary per layer | the same terms on every decoder layer except the last (`layer=0…L-2`) | **deep supervision**; the total sums all layers — the last layer is what inference uses |
| Denoising (DN) | cls + bbox on noised ground-truth queries | training only; makes early training stable; not comparable with matched losses |
| Dense / auxiliary heads | depth (camera), heatmap / centreness (2D or BEV), per-modality auxiliary heads (e.g. radar-only head) | they shape the features, not the final boxes directly; a run can improve them and not the boxes |
| Temporal | sometimes a loss on propagated queries, or none (propagation is learned through the main loss) | check whether propagated queries are matched separately |
| Regularisers | uncertainty term (+log σ per task), weight decay is in the optimizer, not the loss | the logged total may include +log σ — it can go negative |

**What to compare between runs**: when weights differ, compare the **unweighted terms** (or the no-regulariser
versions), per term and per layer — never only the total. In the Metrics view: *Losses* row → *train losses*,
*All losses*, every loss family (`train/loss/cls`, `train/loss/bbox` …) and its *term* sets; the **Section
summary** under the charts gives start / end / change per loss for the section you zoomed to.

## 3. Uncertainty weighting

Kendall-style: each task *i* has a learned log σᵢ; the weighted loss is `Σ exp(-2 log σᵢ)·Lᵢ + log σᵢ`
(variants: `½·exp(-s)·L + ½·s` with s = log σ²). Effects:

* a task with a large loss **raises its σ** and so lowers its own weight — the model "mutes" hard or noisy tasks;
* the weighted total is not a quality measure (it can fall while every task gets worse, and go below zero);
* a σ pinned at a clamp, or one task's weight share collapsing, is a neglected head.

Model Forensic: the **Uncertainty** tab (shares over time, balance, optimum gap, against a baseline) and the
*Uncertainty* tab in Compare. Knowledge: harness task `uncertainty_weighting`.

## 4. Layer-wise improvement in the decoder

Each decoder layer refines the previous layer's boxes (reference points move, classification sharpens). With
auxiliary losses logged per layer (`…[layer=k]`), read:

* **monotone decrease** from the first to the last layer = every layer helps;
* **flat after layer k** = later layers add little (fewer layers, or cross-attention gets no new information);
* **later layers worse** = over-refinement / instability (often with too high LR on the decoder, or a broken
  reference-point update);
* **per modality**: if cross-attention to radar tokens is added in some layers only, compare the layer steps
  between a camera-only and a camera + radar run: the gain should appear at those layers.

Model Forensic: every loss family with a `layer=` dimension shows **final value per layer · change from the previous
layer** under its chart (Slice breakdowns), for the run and the baseline; *Plot its family together* puts all
layers in one chart over training. Which index is the first layer is knowledge (`head_and_queries`).

## 5. Queries: what can be inferred or compared

From logged scalars (if the code logs them — the harness task `head_and_queries` lists what exists):

| Information | Tells you | Compare between runs |
|---|---|---|
| matched / positive query ratio (`positive_query_ratio`) | how many queries the matcher uses; too low = most queries idle | rises with better proposals / propagation |
| duplicate rate | queries predicting the same object; NMS-free decoders rely on self-attention to suppress them | should fall over training |
| propagated vs new query share, query age | how much the memory is used | streaming runs vs single-frame runs |
| score distribution of the top-k propagated queries | whether memory carries confident objects | calibration (ECE) per run |
| reference-point spread / distance to ego | where queries attend; far-range coverage | distance facets of AP / recall |
| denoising vs matched loss gap | how well the model denoises vs detects | DN groups / noise scale changes |

From evaluation per slice: AP / recall **per class × distance × modality** (the filter facets in the Metrics view
and in Compare) show where a change in queries or fusion helped — e.g. far-range recall rising when radar tokens
join cross-attention.

## 6. A study plan

The Metrics tab answers most of the questions below by itself (**Training health questions**); the full list,
with what to try for each: [loss study checklist](loss-study-checklist.md).


1. **Losses** — Metrics view: *Losses* row sets, Section summary for the early / middle / late sections; Compare →
   *Metrics* with the baseline dashed; unweighted terms when weights changed.
2. **Layers** — loss families with `layer=`: the per-layer readout; one chart per family.
3. **Uncertainty** — Uncertainty tab: weight shares, balance, neglected head.
4. **Slices** — filter by class, modality, distance (facets; tune them with `mf facets --brief --run <id>`);
   Compare deltas, *Plot it across the runs*.
5. **Features and gradients** — *gradients* / *features* sets (feature RMS / zero-fraction per module and scale;
   a modality going to zero = collapsed or dropped by design — check the knowledge).
6. **Configs** — Compare → *Deltas & config*: meaningful vs runtime-only differences (knowledge: `config_study`).
7. **Write it down** — a Study (study book) with the baseline part and comparison chapters.

## 7. Prompts for studying the code (harness tasks)

`mf harness brief --run <id> --task <task>` → give it to the coding agent in the training repo → it answers with
knowledge YAML (and a Markdown explanation in the inbox) → accept in Knowledge.

| Task | Studies |
|---|---|
| `backbone` | per modality: backbone, pre-training, frozen stages, LR groups, which series observe it |
| `neck_and_fusion` | neck levels / scales, where modalities fuse, dropped-modality handling, temporal memory, position embeddings |
| `head_and_queries` | query count / init / propagation, decoder layer order, reference refinement, aux outputs, denoising, per-query information |
| `loss_study` | matching costs vs loss weights, every term per layer, normalisers, denoising, weighting, what the total is |
| `config_study` | config resolution, meaningful vs runtime-only keys, coupled keys |
| `loss_composition`, `uncertainty_weighting`, `metric_definitions`, `label_targets`, `setup_profiles`, `checkpoint_and_validation` | (existing) |
