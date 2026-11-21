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
    model,                                    # unwrap DDP: model.module
    groups={"camera": "img_backbone", "lidar": "pts_backbone", "radar": ["radar_encoder", "radar_neck"],
            "fusion": "fusion_layer", "decoder": "pts_bbox_head.transformer.decoder"},   # parameter-name prefixes
    features={"radar_fpn": "radar_neck", "camera_fpn": "img_neck", "bev": "fusion_layer"},  # module paths
    every=50,                                 # grads/updates every 50 steps, features every 200
)

for step, batch in enumerate(loader):
    fx.start_step(step)
    losses = model(**batch)                   # dict of loss tensors (MMDet "d0.loss_cls" naming understood)
    loss = sum(losses.values())
    scaler.scale(loss).backward()
    scaler.unscale_(optimizer)                # with AMP: unscale first so gradient norms are real
    fx.after_backward(step)
    scaler.step(optimizer); scaler.update(); optimizer.zero_grad()
    fx.after_step(step)                       # actual update size of the optimizer step, per module
    fx.log_losses(losses, step)               # train/loss/cls[layer=k], train/loss/bbox, train/loss ...

# at evaluation
summary = json.load(open("metrics_summary.json"))          # nuScenes devkit output
fx.log(nuscenes_breakdown(summary), step)                  # NDS, mAP, AP[class=car], ATE[class=car], ...
log_slices(fx.sink, step, "recall", recall_by_range)        # {"0-30": .78, "30-60": .62, "60-inf": .41}
ablation_eval(model, run_eval, {"no_radar": "radar_neck", "no_camera": ["img_backbone", "img_neck"]},
              sink=fx.sink, step=step)                      # run_eval() -> {"NDS": .., "recall[range=60-inf]": ..}
```

A runnable version is in [`examples/train_loop.py`](examples/train_loop.py).

## MMEngine / MMDetection3D

```python
custom_imports = dict(imports=["model_forensic_instrument.integrations.mmengine_hook"], allow_failed_imports=False)
custom_hooks = [dict(
    type="ForensicHook",
    groups=dict(camera="img_backbone", radar="radar_encoder", fusion="fusion_layer",
                decoder="pts_bbox_head.transformer.decoder"),
    features=dict(radar_fpn="radar_neck", camera_fpn="img_neck"),
    every=50)]
```

The hook wraps `optim_wrapper.step` (MMEngine runs backward, step and zero-grad inside `train_step`, so a normal
hook never sees gradients), logs the loss dict after each iteration, and the validation metrics after each
evaluation. Tested with a real MMEngine 0.10 `Runner`.

## Picking group prefixes

```python
for name, _ in model.named_parameters(): print(name)      # groups use parameter-name prefixes
for name, _ in model.named_modules(): print(name)         # features / ablations use module paths
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
