"""Model Forensic instrumentation kit.

Add a few lines to a PyTorch training loop to log the diagnostics Model Forensic needs to answer
architecture questions ("is the radar branch learning?", "do later decoder layers help?", "what does
each modality contribute at long range?"). Metrics go to MLflow (Azure ML jobs pick them up
automatically), to a local ``metrics.jsonl``, or to any callable.

    from model_forensic_instrument import Forensic

    fx = Forensic(model, groups={"camera": "img_backbone", "radar": "radar_encoder",
                                 "fusion": "fusion", "decoder": "head.decoder"},
                  features={"radar": "radar_neck"}, every=50)
    for step, batch in enumerate(loader):
        fx.start_step(step)
        losses = model(batch)                 # dict of loss tensors
        loss = sum(losses.values())
        loss.backward()
        fx.after_backward(step)               # per-module grad norms (call after scaler.unscale_ with AMP)
        optimizer.step(); optimizer.zero_grad()
        fx.after_step(step)                   # per-module update ratios
        fx.log_losses(losses, step)           # per-term and per-decoder-layer losses
"""

from model_forensic_instrument.ablation import ablation_eval, zero_output
from model_forensic_instrument.evaluation import evaluate_by_slices, log_slices, nuscenes_breakdown
from model_forensic_instrument.forensic import Forensic
from model_forensic_instrument.losses import normalize_losses
from model_forensic_instrument.naming import decode, encode, metric_name
from model_forensic_instrument.probes import LossGradProbe
from model_forensic_instrument.sinks import CallableSink, JsonlSink, MemorySink, MlflowSink, MultiSink, default_sink
from model_forensic_instrument.temporal import SequencePositionMeter, query_source_stats, seqpos_bucket
from model_forensic_instrument.uncertainty import uncertainty_stats

__all__ = [
    "CallableSink",
    "Forensic",
    "JsonlSink",
    "LossGradProbe",
    "MemorySink",
    "MlflowSink",
    "MultiSink",
    "SequencePositionMeter",
    "ablation_eval",
    "decode",
    "default_sink",
    "encode",
    "evaluate_by_slices",
    "log_slices",
    "metric_name",
    "normalize_losses",
    "nuscenes_breakdown",
    "query_source_stats",
    "seqpos_bucket",
    "uncertainty_stats",
    "zero_output",
]
__version__ = "0.1.0"
