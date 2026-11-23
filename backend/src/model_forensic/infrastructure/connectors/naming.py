"""Map raw metric names logged by training code to canonical :class:`MetricKey` s.

Three conventions are understood, so analyses work on existing runs without re-logging:

* canonical ``val/recall[range=60-inf]`` and the MLflow-safe ``val/recall__range_60-inf``;
* MMDet-style / generic ``train/loss_cls``, ``val_mAP`` …;
* the **xflow multi-task layout** (sparse-detection pipelines):

  ========================================================  ==============================================
  raw                                                       canonical
  ========================================================  ==============================================
  ``train_sparse_detection/d3_loss_cls/weighted_loss``      ``train/loss/cls[layer=3,task=sparse_detection]``
  ``…/d3_loss_cls/unweighted_loss``                         ``train/uloss/cls[layer=3,task=…]``
  ``…/d3_loss_cls/weighted_loss_no_reg``                    ``train/loss_noreg/cls[layer=3,task=…]``
  ``…/d3_loss_cls/uncertainty_weight_log_sigma``            ``train/task_log_sigma[layer=3,task=…,term=cls]``
  ``train_weighted_total_loss`` / ``train_total_loss``      ``train/loss``
  ``train_unweighted_total_loss``                           ``train/uloss``
  ``train_weighted_total_loss_no_reg``                      ``train/loss_noreg``
  ``evaluation/sparse_detection/PassengerCar``              ``val/score[class=PassengerCar,task=…]``
  ``evaluation/sparse_detection/Range0-40``                 ``val/score[range=0-40,task=…]``
  ``…/alliance_manual_labels.Car.pr_curves_detailed.        ``val/pr_auc[class=Car,dist=40,recall=0.8,task=…]``
  auc0.800000040.0000000``
  ``…/Car.working_point.f1_score0.9000000``                 ``val/wp_f1_score[class=Car,recall=0.9,task=…]``
  ``…/alliance_manual_labels.Car.bb3d_match_evaluation.     ``val/match_center_distance_rsme[class=Car,task=…]``
  center_distance_rsme``
  ``…/alliance_manual_labels/all/mean_f1_score``            ``val/mean_f1_score[task=…]``
  ``evaluation/sparse_detection/Animal/idf1``               ``val/idf1[class=Animal,task=…]``
  ``evaluation/fold-3/accuracy``                            ``val/accuracy[fold=3]``
  ========================================================  ==============================================

Weighted ``loss`` is what the optimizer minimizes; ``uloss`` is the raw task loss before the learned
(Kendall) uncertainty weighting, which is what per-layer and per-run comparisons should use. The raw
name is always kept in the series' provenance.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from model_forensic.domain.metrics import MetricKey, Split
from model_forensic.domain.naming import loss_family  # noqa: F401 - re-exported (moved to the domain)

_FAMILY = {
    "weighted_loss": "loss",
    "unweighted_loss": "uloss",
    "weighted_loss_no_reg": "loss_noreg",
}
_SIGMA = "uncertainty_weight_log_sigma"
_TOTALS = {
    "train_total_loss": "loss",
    "train_weighted_total_loss": "loss",
    "train_unweighted_total_loss": "uloss",
    "train_weighted_total_loss_no_reg": "loss_noreg",
    "train_total_loss_no_reg": "loss_noreg",
}
_TASK_PREFIX = re.compile(r"^(?P<split>train|val|validation|test)_(?P<task>[a-z0-9_]+)$")
_TERM = re.compile(r"^(?:d(?P<layer>\d+)_)?(?:loss_)?(?P<term>[a-z0-9_]+?)$")
_DN = re.compile(r"(?:^dn_|_dn$)")
_SPLIT_ALIASES = {"train": Split.TRAIN, "val": Split.VAL, "validation": Split.VAL, "test": Split.TEST}

# evaluation/<task>/...
_F7 = r"\d+\.\d{7}"
_AUC = re.compile(rf"\.pr_curves_detailed\.auc(?:(?P<r>{_F7})(?P<d>{_F7})|@(?P<r2>[\d.]+)@(?P<d2>[\d.]+))$")
_WP = re.compile(rf"\.working_point\.(?P<m>[a-z0-9_]+?)(?:(?P<r>{_F7})|@(?P<r2>[\d.]+))$")
_MATCH = re.compile(r"\.bb3d_match_evaluation\.(?P<m>[a-z_]+)$")
_RANGE = re.compile(r"^Range(?P<lo>\d+)(?:-(?P<hi>\d+))?$")
_FOLD = re.compile(r"^fold[-_]?(?P<n>\d+)$", re.IGNORECASE)
_LABEL_NS = "alliance_manual_labels"
# Subjects under evaluation/<task>/ that are metrics, not classes.
_EVAL_METRIC_SUBJECTS = {"AP", "mAP", "NDS", "AMOTA", "AMOTP", "MOTA", "MOTP", "HOTA", "IDF1", "recall", "precision"}
_SAFE_VALUE = re.compile(r"[^A-Za-z0-9_.\-]")


def _num(text: str) -> str:
    """``0.9000000`` → ``0.9``, ``40.0000000`` → ``40``."""
    v = float(text)
    return str(int(v)) if v.is_integer() else f"{v:g}"


def _val(text: str) -> str:
    return _SAFE_VALUE.sub("_", text)


@lru_cache(maxsize=65536)
def canonical_key(raw: str) -> MetricKey:
    """Best canonical key for a raw metric name. Never raises: unknown shapes keep their name."""
    name = raw.strip()
    if name in _TOTALS:
        return MetricKey(name=_TOTALS[name], split=Split.TRAIN)
    if name.startswith("lr-") or name in {"lr", "learning_rate", "train/learning_rate", "train/lr"}:
        return MetricKey(name="lr", split=Split.TRAIN)  # Lightning LearningRateMonitor: lr-AdamW
    if name.startswith("evaluation/"):
        return _evaluation(name.removeprefix("evaluation/"))
    head, sep, rest = name.partition("/")
    m = _TASK_PREFIX.match(head) if sep else None
    if m and rest:
        key = _task_metric(_SPLIT_ALIASES[m.group("split")], m.group("task"), rest)
        if key is not None:
            return key
    if head in {"eval"} and rest:  # eval/accuracy
        return _parse_or_raw(f"val/{rest}")
    if not sep:
        for prefix, split in (("train_", Split.TRAIN), ("val_", Split.VAL), ("validation_", Split.VAL)):
            if name.startswith(prefix) and len(name) > len(prefix):
                return _parse_or_raw(f"{split.value}/{name[len(prefix) :]}")
    if head == "validation" and rest:
        return _parse_or_raw(f"val/{rest}")
    if head == "train" and rest == "total_loss":
        return MetricKey(name="loss", split=Split.TRAIN)
    return _parse_or_raw(name)


def _parse_or_raw(name: str) -> MetricKey:
    try:
        return MetricKey.parse(name)
    except ValueError:
        return MetricKey(name=name)


def _task_metric(split: Split, task: str, rest: str) -> MetricKey | None:
    """``d3_loss_cls/weighted_loss`` under task ``sparse_detection``."""
    parts = rest.split("/")
    family: str | None = parts[-1]
    if family not in (*_FAMILY, _SIGMA):
        family = None
    ident = "/".join(parts[:-1]) if family else rest
    dims: dict[str, str] = {"task": task}
    term = ident
    if "/" not in ident:
        t = _TERM.match(ident)
        if t:
            term = t.group("term")
            if t.group("layer") is not None:
                dims["layer"] = t.group("layer")
        if _DN.search(term):
            term = _DN.sub("", term).removeprefix("loss_")  # d0_dn_loss_cls → cls
            dims["branch"] = "dn"
    term = term.replace("/", "_")
    if family == _SIGMA:
        return MetricKey(name="task_log_sigma", split=split, dims=tuple({**dims, "term": _val(term or task)}.items()))
    if family in _FAMILY:
        if not ident:  # train_head/weighted_loss
            return MetricKey(name=_FAMILY[family], split=split, dims=tuple(dims.items()))
        return MetricKey(name=f"{_FAMILY[family]}/{term}", split=split, dims=tuple(dims.items()))
    if ident.startswith(("loss_", "d")) and _TERM.match(ident) and ("loss" in ident):
        # Bare component (no family suffix): the raw loss before weighting.
        return MetricKey(name=f"loss_raw/{term}", split=split, dims=tuple(dims.items()))
    return MetricKey(name=_val(rest), split=split, dims=(("task", task),)) if "/" not in rest else None


def _evaluation(rest: str) -> MetricKey:
    task, _, tail = rest.partition("/")
    fold = _FOLD.match(task)
    if fold and tail:
        return _parse_or_raw(f"val/{tail}").model_copy(update={"dims": (("fold", fold.group("n")),)})
    if not tail:
        return MetricKey(name=_val(task), split=Split.VAL)
    dims: dict[str, str] = {"task": task}

    def key(name: str, **extra: str) -> MetricKey:
        return MetricKey(name=name, split=Split.VAL, dims=tuple({**dims, **extra}.items()))

    def cls_of(prefix: str) -> str:
        return _val(prefix.removeprefix(f"{_LABEL_NS}.") or "all")

    if a := _AUC.search(tail):
        r, d = a.group("r") or a.group("r2"), a.group("d") or a.group("d2")
        return key("pr_auc", **{"class": cls_of(tail[: a.start()]), "recall": _num(r), "dist": _num(d)})
    if w := _WP.search(tail):
        r = w.group("r") or w.group("r2")
        return key(f"wp_{w.group('m')}", **{"class": cls_of(tail[: w.start()]), "recall": _num(r)})
    if mt := _MATCH.search(tail):
        return key(f"match_{mt.group('m')}", **{"class": cls_of(tail[: mt.start()])})
    if tail.startswith(f"{_LABEL_NS}/"):
        agg = tail.removeprefix(f"{_LABEL_NS}/").removeprefix("all/")
        return key(_val(agg))
    if rg := _RANGE.match(tail):
        lo, hi = rg.group("lo"), rg.group("hi")
        return key("score", range=f"{lo}-{hi}" if hi else f"{lo}-inf")
    if "/" in tail:
        subject, _, metric = tail.partition("/")
        if "/" not in metric and "." not in subject:
            return key(_val(metric), **{"class": _val(subject)})
        return key(_val(tail.replace("/", ".")))
    if tail in _EVAL_METRIC_SUBJECTS:
        return key(tail)
    if "." not in tail:
        return key("score", **{"class": _val(tail)})
    return key(_val(tail))


def is_raw_name_canonical(raw: str) -> bool:
    """True when the raw name already is the canonical string (no mapping happened)."""
    return str(canonical_key(raw)) == raw


@dataclass(frozen=True)
class NamingRules:
    """A project's own metric names → names the core understands (from a project profile, ``metrics:``).

    ``aliases`` maps exact raw names; ``rules`` are ``(regex, replacement)`` pairs applied in order (``re.sub`` with
    ``\\1`` groups). Applied before the built-in canonical mapping; the raw name is kept as provenance.
    """

    aliases: tuple[tuple[str, str], ...] = ()
    rules: tuple[tuple[str, str], ...] = ()

    def apply(self, raw: str) -> str:
        for a, b in self.aliases:
            if raw == a:
                return b
        for pat, rep in self.rules:
            new, n = re.subn(pat, rep, raw)
            if n:
                return new
        return raw


class NameMapper:
    """Assigns canonical keys to the raw names of one run, keeping distinct raw series distinct.

    Two raw names that map to the same canonical key (``train_total_loss`` and ``train_weighted_total_loss``)
    must not be merged into one series: the first one seen keeps the canonical key, later ones keep their raw
    name as key.
    """

    def __init__(self, rules: NamingRules | None = None) -> None:
        self._by_raw: dict[str, MetricKey] = {}
        self._owner: dict[MetricKey, str] = {}
        self._rules = rules

    def __call__(self, raw: str) -> MetricKey:
        if raw in self._by_raw:
            return self._by_raw[raw]
        key = canonical_key(self._rules.apply(raw) if self._rules else raw)
        owner = self._owner.setdefault(key, raw)
        if owner != raw:
            key = _parse_or_raw(raw)
            if key in self._owner and self._owner[key] != raw:
                key = MetricKey(name=raw)
            self._owner.setdefault(key, raw)
        self._by_raw[raw] = key
        return key

    def raw_of(self, key: MetricKey) -> str | None:
        return self._owner.get(key)
