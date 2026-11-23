"""Model internals: where the decoder's cross-attention looks (per layer × modality) and how the queries live.

Reads what the instrumentation kit logs (``instrument/…/attention.py``, ``temporal.py``):
``attn_mass[layer,modality]``, ``attn_entropy[layer]`` / ``[layer,modality]``, ``attn_top1[layer]``,
``q_from_memory_frac``, ``q_pos_frac[source]``, ``q_age_mean[source]``, ``q_age_frac[age]``, and the sparse-head
statistics (``positive_query_ratio``, ``duplicate_rate``), the matching instability (``match_instability[layer]``,
``match_layer_change[layer]``) and the logit size (``attn_logit_max[layer]``, ``logit_max`` / ``logit_mean``). Deterministic; values are the smoothed end of training
and of its first quarter, so a shift over training is visible. Notes say what looks wrong and what to check.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from model_forensic.analysis import signal, thresholds
from model_forensic.domain.metrics import MetricSeries

Series = Mapping[str, MetricSeries]
_ATTN = re.compile(r"^(?:(train|val)/)?attn_(mass|entropy|top1)\[(.*)\]$")
QUERY_KEYS = ("q_from_memory_frac", "q_pos_frac", "q_age_mean", "q_age_frac", "positive_query_ratio", "duplicate_rate")


def _t(key: str) -> float:
    return thresholds.t("internals", key)


def _dims(inner: str) -> dict[str, str]:
    return dict(p.split("=", 1) for p in inner.split(",") if "=" in p)


def _val(s: MetricSeries, frac: float = 1.0) -> float | None:
    _, v = s.finite()
    if not v.size:
        return None
    sm = signal.ema(v, 0.3)
    return float(sm[min(len(sm) - 1, int(frac * (len(sm) - 1)))])


def _layer_key(x: str) -> tuple[int, str]:
    return (int(x), "") if x.lstrip("-").isdigit() else (10**6, x)


def attention_table(series: Series) -> dict[str, Any]:
    """{layers, modalities, mass[layer][mod] = {end, start}, entropy[layer], entropy_mod[layer][mod], top1[layer],
    keys[layer][mod]} from the kit's attention series (training split preferred)."""
    mass: dict[str, dict[str, dict[str, float | None]]] = {}
    ent: dict[str, float | None] = {}
    ent_mod: dict[str, dict[str, float | None]] = {}
    top1: dict[str, float | None] = {}
    keys: dict[str, dict[str, str]] = {}
    for k, s in series.items():
        m = _ATTN.match(k)
        if not m or (m.group(1) == "val" and f"train/{k[4:]}" in series):
            continue
        kind, d = m.group(2), _dims(m.group(3))
        layer = d.get("layer")
        if layer is None:
            continue
        mod = d.get("modality")
        if kind == "mass" and mod:
            mass.setdefault(layer, {})[mod] = {"end": _val(s), "start": _val(s, 0.25)}
            keys.setdefault(layer, {})[mod] = k
        elif kind == "entropy":
            if mod:
                ent_mod.setdefault(layer, {})[mod] = _val(s)
            else:
                ent[layer] = _val(s)
                keys.setdefault(layer, {})["_entropy"] = k
        elif kind == "top1":
            top1[layer] = _val(s)
    layers = sorted(set(mass) | set(ent) | set(top1), key=_layer_key)
    mods = sorted({mo for row in mass.values() for mo in row})
    return {"layers": layers, "modalities": mods, "mass": mass, "entropy": ent, "entropy_mod": ent_mod,
            "top1": top1, "keys": keys}  # fmt: skip


def query_table(series: Series) -> list[dict[str, Any]]:
    """The query statistics the run logs: key, end value, value at a quarter of training."""
    out = []
    for k, s in sorted(series.items()):
        base = k.split("[", 1)[0].split("/")[-1]
        if base in QUERY_KEYS:
            out.append({"key": k, "end": _val(s), "start": _val(s, 0.25)})
    return out


_PER_LAYER = re.compile(
    r"^(?:(train|val)/)?(match_instability|match_layer_change|attn_logit_max|logit_max|logit_rms|logit_mean|q_norm|k_norm)\[(.*)\]$"
)


def stability_table(series: Series) -> dict[str, Any]:
    """Matching instability (kit ``matching.py``) and logit size (``stability.py``) per decoder layer:
    ``{kind: {layer: {"start", "end", "key", "rising"}}}`` — start is the value at a quarter of training."""
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for k, s in series.items():
        m = _PER_LAYER.match(k)
        if not m:
            continue
        layer = _dims(m.group(3)).get("layer", "all")
        _, v = s.finite()
        rising = v.size >= 8 and float(signal.ema(v, 0.3)[-1]) > float(signal.ema(v, 0.3)[int(0.75 * (v.size - 1))])
        out.setdefault(m.group(2), {})[layer] = {"start": _val(s, 0.25), "end": _val(s), "key": k, "rising": rising}
    return {kind: dict(sorted(rows.items(), key=lambda x: _layer_key(x[0]))) for kind, rows in out.items()}


def stability_notes(st: dict[str, Any]) -> list[dict[str, str]]:
    """What looks wrong in the matching and the logits (status warn / bad), each with the check that follows."""
    out: list[dict[str, str]] = []
    mi = st.get("match_instability", {})
    bad = [la for la, r in mi.items() if r["end"] is not None and r["end"] > _t("match_unstable")]
    if bad:
        stuck = [la for la in bad if mi[la]["start"] and mi[la]["end"] > _t("match_settle_ratio") * mi[la]["start"]]
        worst = max(bad, key=lambda la: mi[la]["end"])
        out.append({"status": "bad" if stuck else "warn", "id": "matching_unstable",
                    "text": f"The matching does not settle: late in training {mi[worst]['end']:.0%} of the objects "
                            f"change their matched query between measurements (layer {worst})"
                            + (f"; it barely falls over training in layers {', '.join(stuck)}" if stuck else "") + ".",
                    "check": "Denoising queries (are they on, and matched to their own objects?), the matching costs "
                             "(class vs box weights), one-to-many auxiliary supervision; DN-DETR / Stable-DINO "
                             "(docs/guide/learning-from-the-loss.md B1, B2)."})  # fmt: skip
    lc = st.get("match_layer_change", {})
    jump = [la for la, r in lc.items() if r["end"] is not None and r["end"] > _t("layer_change_max")]
    if jump:
        out.append({"status": "warn", "id": "matching_layers",
                    "text": f"Decoder layers chase different objects: in layers {', '.join(jump)} more than "
                            f"{_t('layer_change_max'):.0%} of the objects are matched to another query than in the "
                            "layer before.",
                    "check": "The refinement is not refining: position-supervised / position-modulated matching "
                             "costs (Stable matching), shared reference points between layers."})  # fmt: skip
    for kind, what in (("attn_logit_max", "attention"), ("logit_max", "output")):
        rows = st.get(kind, {})
        grow = [(la, r["end"] / r["start"]) for la, r in rows.items()
                if r["start"] and r["end"] is not None and r["start"] > 0 and r["rising"]
                and r["end"] / r["start"] > _t("logit_growth")]  # fmt: skip
        if grow:
            la, g = max(grow, key=lambda x: x[1])
            out.append({"status": "warn", "id": f"logit_growth:{kind}",
                        "text": f"The largest {what} logit keeps growing (×{g:.1f} since the first quarter, layer "
                                f"{la}) — a precursor of loss spikes and divergence (Wortsman et al., 2024).",
                        "check": "QK-layernorm or a cap on the attention logits, a smaller AdamW ε, a lower LR for "
                                 "that block; watch the spike / divergence questions."})  # fmt: skip
    return out


def notes(attn: dict[str, Any], queries: list[dict[str, Any]]) -> list[dict[str, str]]:
    """What looks wrong in the internals (status bad / warn) — each with the check that follows."""
    out: list[dict[str, str]] = []
    layers, mods, mass = attn["layers"], attn["modalities"], attn["mass"]
    for mo in mods:
        ends = [mass[la][mo]["end"] for la in layers if mo in mass.get(la, {}) and mass[la][mo]["end"] is not None]
        if ends and max(ends) < _t("starved_max"):
            out.append({"status": "bad", "id": f"starved:{mo}",
                        "text": f"The decoder barely reads {mo}: < 5% of the cross-attention in every layer "
                                f"(max {max(ends):.1%}).",
                        "check": f"Is {mo} dropped by design? its tokens' position embedding / normalisation; "
                                 f"compare the {mo} ablation (val/…[ablation=no_{mo}])."})  # fmt: skip
        firsts = [mass[la][mo] for la in layers if mo in mass.get(la, {})]
        if (
            len(firsts) >= 2
            and firsts[0]["end"]
            and firsts[-1]["end"] is not None
            and firsts[-1]["end"] < _t("abandoned_ratio") * firsts[0]["end"]
            and firsts[0]["end"] > _t("min_share")
        ):
            out.append({"status": "warn", "id": f"abandoned:{mo}",
                        "text": f"Later layers abandon {mo}: {firsts[0]['end']:.0%} of the attention in the first "
                                f"layer, {firsts[-1]['end']:.0%} in the last.",
                        "check": "Intended (early layers localise, later refine)? If the modality carries the "
                                 "far range, the refinement no longer sees it."})  # fmt: skip
        drops = [(la, mass[la][mo]) for la in layers if mo in mass.get(la, {})]
        fell = [la for la, v in drops if v["start"] and v["end"] is not None and v["end"] < _t("faded_ratio") * v["start"]
                and v["start"] > _t("min_share")]  # fmt: skip
        if fell:
            out.append({"status": "warn", "id": f"faded:{mo}",
                        "text": f"Attention on {mo} faded over training in layers {', '.join(fell)} "
                                f"(< 40% of its early share).",
                        "check": f"Does {mo}'s feature health fall at the same time (feat_zero_frac, grad_dead_frac)? "
                                 "Modality dropout / LR multiplier of that branch."})  # fmt: skip
    for la in layers:
        e = attn["entropy"].get(la)
        if e is not None and e < _t("collapse_entropy"):
            out.append({"status": "warn", "id": f"collapse:{la}",
                        "text": f"Cross-attention in layer {la} has collapsed onto single tokens (entropy {e:.2f}).",
                        "check": "Attention temperature / scaling, position-embedding scale; one token dominating "
                                 "(e.g. a padding or memory token)."})  # fmt: skip
        elif e is not None and e > _t("diffuse_entropy"):
            out.append({"status": "warn", "id": f"diffuse:{la}",
                        "text": f"Cross-attention in layer {la} is almost uniform (entropy {e:.2f}): it does not "
                                "focus.",
                        "check": "Reference points / position embeddings reach the attention? Is the layer "
                                 "trained (aux loss on it)?"})  # fmt: skip
    q = {r["key"].split("/")[-1]: r for r in queries}
    mem = q.get("q_from_memory_frac")
    if mem and mem["end"] is not None and mem["end"] < _t("memory_min"):
        out.append({"status": "warn", "id": "memory_unused",
                    "text": f"Only {mem['end']:.0%} of the matched queries come from the temporal memory.",
                    "check": "Memory length / top-k propagated, ego-motion alignment of propagated queries, "
                             "sequences long enough in training."})  # fmt: skip
    prop, new = q.get("q_pos_frac[source=propagated]"), q.get("q_pos_frac[source=new]")
    if prop and new and prop["end"] is not None and new["end"] and prop["end"] < _t("memory_stale_ratio") * new["end"]:
        out.append({"status": "warn", "id": "memory_stale",
                    "text": f"Propagated queries are matched less often than new ones ({prop['end']:.1%} vs "
                            f"{new['end']:.1%}): the memory carries stale objects.",
                    "check": "Score threshold / top-k for propagation; motion compensation; query age "
                             "(q_age_mean)."})  # fmt: skip
    return out


def internals(series: Series, baseline: Series | None = None) -> dict[str, Any]:
    attn = attention_table(series)
    queries = query_table(series)
    stab = stability_table(series)
    out: dict[str, Any] = {"attention": attn, "queries": queries, "stability": stab,
                           "notes": notes(attn, queries) + stability_notes(stab)}  # fmt: skip
    if baseline is not None:
        out["baseline"] = {"attention": attention_table(baseline), "queries": query_table(baseline),
                           "stability": stability_table(baseline)}  # fmt: skip
    out["logged"] = bool(attn["layers"] or queries or stab)
    return out
