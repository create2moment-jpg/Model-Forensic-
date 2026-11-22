# model-forensic-instrument

A small, dependency-free package you add to your **training code** so Model Forensic can answer
architecture questions instead of guessing from the loss curve:

| Question | What the kit logs | Analysis in Model Forensic |
|---|---|---|
| Is every branch (camera, LiDAR, radar, fusion, decoder) learning? | per-module gradient norm, parameter norm, dead-gradient fraction, **update ratio** ‖Δθ‖/‖θ‖ | *Module learning health* |
| What does each modality contribute, and at which range? | evaluation with a branch **zeroed** (`val/NDS[ablation=no_radar]`, sliced) | *Modality contribution* |
| Are the (multi-scale) feature maps healthy? | RMS / std / zero fraction / non-finite per module and **per scale** | *Feature-map health* |
| How deep should the decoder be? | loss per term **per decoder layer** | *Decoder depth* |
| Where exactly is the model weak? | per-range / per-class evaluation (`val/recall[range=60-inf]`), nuScenes breakdowns | *Regression forensics* |

Works with plain PyTorch loops and MMEngine / MMDetection3D. In an **Azure ML job** metrics go to the
job through MLflow automatically; elsewhere to `outputs/forensic/metrics.jsonl`.

```bash
pip install -e instrument            # or copy the package into your training image; no required dependencies
```

## Plain PyTorch

```python
from model_forensic_instrument import Forensic, ablation_eval, log_slices, nuscenes_breakdown

fx = Forensic(
    model,  # unwrap DDP: model.module
    groups={
        "camera": "img_backbone",
        "lidar": "pts_backbone",
        "radar": ["radar_encoder", "radar_neck"],
        "fusion": "fusion_layer",
        "decoder": "pts_bbox_head.transformer.decoder",
    },  # parameter-name prefixes
    features={"radar_fpn": "radar_neck", "camera_fpn": "img_neck", "bev": "fusion_layer"},  # module paths
    every=50,  # grads/updates every 50 steps, features every 200
)

for step, batch in enumerate(loader):
    fx.start_step(step)
    losses = model(**batch)  # dict of loss tensors (MMDet "d0.loss_cls" naming understood)
    loss = sum(losses.values())
    scaler.scale(loss).backward()
    scaler.unscale_(optimizer)  # with AMP: unscale first so gradient norms are real
    fx.after_backward(step)
    scaler.step(optimizer)
    scaler.update()
    optimizer.zero_grad()
    fx.after_step(step)  # actual update size of the optimizer step, per module
    fx.log_losses(losses, step)  # train/loss/cls[layer=k], train/loss/bbox, train/loss ...

# at evaluation
summary = json.load(open("metrics_summary.json"))  # nuScenes devkit output
fx.log(nuscenes_breakdown(summary), step)  # NDS, mAP, AP[class=car], ATE[class=car], ...
log_slices(fx.sink, step, "recall", recall_by_range)  # {"0-30": .78, "30-60": .62, "60-inf": .41}
ablation_eval(
    model, run_eval, {"no_radar": "radar_neck", "no_camera": ["img_backbone", "img_neck"]}, sink=fx.sink, step=step
)  # run_eval() -> {"NDS": .., "recall[range=60-inf]": ..}
```

A runnable version is in [`examples/train_loop.py`](examples/train_loop.py).

## MMEngine / MMDetection3D

```python
custom_imports = dict(imports=["model_forensic_instrument.integrations.mmengine_hook"], allow_failed_imports=False)
custom_hooks = [
    dict(
        type="ForensicHook",
        groups=dict(
            camera="img_backbone",
            radar="radar_encoder",
            fusion="fusion_layer",
            decoder="pts_bbox_head.transformer.decoder",
        ),
        features=dict(radar_fpn="radar_neck", camera_fpn="img_neck"),
        every=50,
    )
]
```

The hook wraps `optim_wrapper.step` (MMEngine runs backward, step and zero-grad inside `train_step`, so a normal
hook never sees gradients), logs the loss dict after each iteration, and the validation metrics after each
evaluation. Tested with a real MMEngine 0.10 `Runner`.

## mmcv 1.x / MMDetection3D 0.17 (StreamPETR, PETR, BEVFormer codebases)

```python
custom_imports = dict(imports=["model_forensic_instrument.integrations.mmcv1_hook"], allow_failed_imports=False)
custom_hooks = [dict(type="ForensicMmcvHook", priority="LOW",
                     groups=dict(camera="img_backbone", neck="img_neck", decoder="pts_bbox_head.transformer.decoder"),
                     features=dict(img_neck="img_neck"), every=50)]
```

`priority="LOW"` puts the hook after `OptimizerHook`, so it reads the gradients and the real update of the step.
Validation results in `runner.log_buffer` are mapped to canonical names
(`pts_bbox_NuScenes/car_AP_dist_2.0` → `val/AP[class=car,thr=2.0]`). Tested with a real mmcv 1.7 `IterBasedRunner`.

## Uncertainty heads, streaming memory, DN / aux branches, loss conflicts

```python
fx = Forensic(model, groups=..., probe_shared="img_backbone", probe_every=500)

# decoder losses: MMDet-style keys are understood as-is, including StreamPETR's
#   d0.loss_cls … loss_cls          → train/loss/cls[layer=k]
#   dn_loss_bbox, d0.dn_loss_bbox   → train/loss/bbox[branch=dn,layer=k]
#   loss_bbox2d, loss_centerness    → train/loss/bbox[branch=aux2d], train/loss/centerness[branch=aux2d]
fx.probe_losses(losses, step)                      # before backward(): per-term gradient on the shared module
loss.backward()
fx.log_losses(losses, step, seqpos=frames_since_memory_reset)   # + train/loss/<term>[seqpos=0|1-3|4-inf]
fx.log_query_sources(pos_mask, propagated_mask, step)           # are propagated memory queries matched?

# at evaluation, on matched objects (residual = pred - gt, scale = predicted b or σ, per box dimension)
fx.log_uncertainty(residual, scale, step, split="val", kind="laplace", scale_is_log=True,
                   dims=["x", "y", "z", "w", "l", "h", "vx", "vy"],
                   slices={"0-30": dist < 30, "30-60": (dist >= 30) & (dist < 60), "60-inf": dist >= 60})
```

| Metric | Meaning | Analysis |
|---|---|---|
| `unc_nerr[dim]` | mean |error| / predicted scale (≈1 when calibrated; >1 over-confident) | `uncertainty_health` |
| `unc_cov[dim,level=68\|95]` | share of errors inside the predicted interval | `uncertainty_health` |
| `unc_corr[dim]` | rank correlation between predicted scale and error | `uncertainty_health` |
| `unc_scale`, `unc_abs_err` | predicted scale and actual error (attenuation over training) | `uncertainty_health` |
| `unc_clamped_frac[dim]` | share of scales at the clamp bound | `uncertainty_health` |
| `loss/<t>[seqpos=…]`, `val/<m>[seqpos=…]` | loss / metric by frames since memory reset | `temporal_memory` |
| `q_from_memory_frac`, `q_pos_frac[source]` | memory share of matched queries, positive rate per source | `temporal_memory` |
| `loss/<t>[branch=dn,layer=k]` | denoising-query losses per layer | `decoder_layers` |
| `term_grad_norm[term]`, `term_grad_cos[a,b]` | per-term gradient size and conflict on the shared module | `loss_interactions` |

The probe costs one extra backward per loss term on probe steps (every 500 by default). StreamPETR's key names
vary between forks; check `normalize_losses(your_loss_dict)` once and rename keys it does not recognise before logging.

## Decoder internals: where cross-attention looks, how old the queries are

```python
fx = Forensic(model, groups=..., every=50,
              attention={k: f"pts_bbox_head.transformer.decoder.layers.{k}.attentions.1.attn" for k in range(6)},
              attention_every=200)       # the nn.MultiheadAttention that attends to the sensor tokens, per layer

for step, batch in enumerate(loader):
    fx.start_step(step)
    # which keys belong to which modality this step (token order as concatenated by your model)
    fx.set_token_groups({"camera": slice(0, n_cam), "radar": slice(n_cam, n_cam + n_radar)}, positive=matched_mask)
    losses = model(**batch)
    ...
    fx.after_step(step)                  # attention stats are emitted with the step's other statistics
    fx.log_query_ages(query_ages, step, positive=matched_mask)   # ages: 0 = new, propagated = previous age + 1
```

| Metric | Meaning | Where in Model Forensic |
|---|---|---|
| `attn_mass[layer,modality]` | share of the layer's cross-attention on the modality's tokens | Metrics → *Model internals* (layer × modality), question *Does every modality get the decoder's attention?* |
| `attn_entropy[layer]`, `attn_entropy[layer,modality]` | 0 = collapsed onto one token, 1 = uniform (no focus) | *Model internals* → focus |
| `attn_top1[layer]` | mean of the largest weight per query | *Model internals* → peak |
| `q_age_mean[source=all\|matched]`, `q_age_frac[age=0\|1-3\|4-inf]` | how long queries live in the memory; ages of the matched ones | *Model internals* → queries, question *Is the temporal memory used?* |

PyTorch's `nn.MultiheadAttention` returns head-averaged weights by default (`need_weights=True`); fused / flash
attention never materialises them — switch it off on recording steps, or compute the weights yourself and call
`fx.log_attention(weights, token_groups, layer, step)`. Deformable attention (sampling points per level / camera):
pass the sampling weights with the points grouped per modality — the same statistics apply. Cost: one hook per layer,
active every `attention_every` steps only.

## Picking group prefixes

```python
for name, _ in model.named_parameters():
    print(name)  # groups use parameter-name prefixes
for name, _ in model.named_modules():
    print(name)  # features / ablations use module paths
```

Unmatched trainable parameters are reported as group `other`; a group that matches nothing raises an error, so
typos are caught on the first step.

## Metric names

Canonical form `split/name[dim=value,...]`. MLflow (and so Azure ML) rejects `[ = ,` in metric names, so the
MLflow sink writes `val/recall__range_60-inf`; Model Forensic decodes both. Dimension keys are lowercase letters
(`module`, `scale`, `layer`, `ablation`, `range`, `class`, …).

## Cost

Gradient/update statistics read every parameter once every `every` steps and keep one copy of the weights
between `after_backward` and `after_step` on those steps (set `track_updates=False` to avoid the copy).
Feature hooks do nothing on other steps. Only rank 0 logs under DDP.

## Caveats

* Zeroing a branch at evaluation measures what *this trained model* relies on; it is not the same as training without
  the sensor. Models trained with modality dropout tolerate zeros better.
* Update ratios depend on the optimizer; compare modules within a run.
