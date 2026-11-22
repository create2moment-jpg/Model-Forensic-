"""Checking knowledge from experience (beliefs) against runs — deterministic, probabilistic, explained.

A belief is not a fact: it is checked. Each piece of evidence is one comparison (a pair of runs that differ in the
belief's config key, or one run whose loss behaviour is read). Clean comparisons (nothing else changed) weigh 1,
confounded ones less, hopeless ones are skipped and counted. Evidence updates a Beta posterior whose prior is the
person's own confidence (a few pseudo-observations, never enough on its own for dark green). The verdict says how
likely the belief holds *in its scope*, how sure that is, which comparisons argue against it, and which single
experiment would settle it.
"""

from __future__ import annotations

import fnmatch
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from model_forensic.knowledge.model import BeliefTest

PRIOR = {"low": 0.0, "medium": 0.5, "high": 1.0}  # pseudo-observations in favour: never more than one clean pair
MAX_OTHER_DIFFS = 20
NOISE_KEYS = ("seed", "random", "output", "dir", "path", "name", "id", "time", "date", "user", "tag", "url", "host")
COLOURS = ("dark_green", "green", "yellow", "red", "grey")


@dataclass
class RunFacts:
    """What verification needs from one run — plain values, so the logic is testable without a backend."""

    run: str
    label: str
    config: dict[str, Any]  # effective (training-stage, composed) config, flattened
    finals: dict[str, float]  # metric key → final value (median of the last 5 %)
    behaviours: dict[str, dict[str, bool]] = field(default_factory=dict)  # family → behaviour → holds


@dataclass
class Evidence:
    kind: str  # pair | run
    runs: list[str]
    outcome: str  # supports | contradicts | no_effect | skipped
    weight: float
    detail: str
    other_diffs: int = 0
    change: str = ""
    delta: float | None = None  # relative metric change (pairs)


@dataclass
class Verdict:
    p: float  # posterior mean that the belief holds in its scope
    lo: float  # 10th percentile
    hi: float  # 90th percentile
    colour: str
    support: float  # summed weights
    contra: float
    no_effect: int
    skipped: int
    n: int  # comparisons used
    threshold: float  # relative change counted as an effect
    summary: str
    next_experiment: str
    evidence: list[Evidence] = field(default_factory=list)
    runs_considered: int = 0
    p_data: float = 0.5  # the same estimate from the runs alone (no prior from the person's confidence)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ------------------------------------------------------------------------------------------------ helpers
def _num(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def find_key(cfg: Mapping[str, Any], key: str) -> str | None:
    """The config key matching ``key`` exactly or as a dotted suffix (shortest match wins)."""
    if key in cfg:
        return key
    hits = [k for k in cfg if k.endswith("." + key)]
    return min(hits, key=len) if hits else None


def _is_noise(k: str) -> bool:
    low = k.lower()
    return any(t in low.rsplit(".", 1)[-1] for t in NOISE_KEYS)


def other_diffs(a: Mapping[str, Any], b: Mapping[str, Any], skip: str) -> int:
    """How many meaningful settings besides ``skip`` differ (missing on one side counts)."""
    n = 0
    for k in set(a) | set(b):
        if k == skip or _is_noise(k):
            continue
        if str(a.get(k)) != str(b.get(k)):
            n += 1
    return n


def find_metric(finals: Mapping[str, float], metric: str) -> float | None:
    """Exact key; else the same key in the last pipeline stage that logged it (``stage=`` dimension)."""
    if metric in finals:
        return finals[metric]
    if "[" in metric:
        name, dims = metric.split("[", 1)
        want = set(dims.rstrip("]").split(","))
    else:
        name, want = metric, set()
    cands = []
    for k, v in finals.items():
        kn, _, kd = k.partition("[")
        if kn != name:
            continue
        have = {d for d in kd.rstrip("]").split(",") if d}
        if want <= have and all(d.startswith("stage=") for d in have - want):
            cands.append((k, v))
    return sorted(cands)[-1][1] if cands else None


def seed_noise(runs: Sequence[RunFacts], metric: str) -> float | None:
    """Relative std of the metric across runs whose configs differ only in noise keys (seed replicates)."""
    groups: dict[str, list[float]] = {}
    for r in runs:
        v = find_metric(r.finals, metric)
        if v is None:
            continue
        sig = repr(sorted((k, str(x)) for k, x in r.config.items() if not _is_noise(k)))
        groups.setdefault(sig, []).append(v)
    rels = [float(np.std(vs, ddof=1) / abs(np.mean(vs))) for vs in groups.values() if len(vs) >= 2 and np.mean(vs) != 0]
    return float(np.median(rels)) if rels else None


# ------------------------------------------------------------------------------------------------ evidence
def config_effect_evidence(t: BeliefTest, runs: Sequence[RunFacts], threshold: float) -> list[Evidence]:
    keyed = []
    for r in runs:
        k = find_key(r.config, t.key)
        m = find_metric(r.finals, t.metric)
        if k is not None and m is not None:
            keyed.append((r, k, r.config[k], m))
    cands: list[tuple[int, int, int]] = []  # (other diffs, i, j) with i = "before", j = "after"
    for i in range(len(keyed)):
        for j in range(len(keyed)):
            if i == j:
                continue
            (ra, ka, va, _), (rb, _kb, vb, _) = keyed[i], keyed[j]
            if t.change == "set":
                if str(va) == t.value or str(vb) != t.value:
                    continue  # i without the value → j with it
            else:
                xa, xb = _num(va), _num(vb)
                if xa is None or xb is None or xb <= xa:
                    continue  # i lower → j higher (direction handled below)
            cands.append((other_diffs(ra.config, rb.config, ka), i, j))
    cands.sort()
    used: set[int] = set()
    out: list[Evidence] = []
    skipped = 0
    for d, i, j in cands:
        if i in used or j in used:
            continue
        if d > MAX_OTHER_DIFFS:
            skipped += 1
            continue
        used |= {i, j}
        (ra, ka, va, ma), (rb, _, vb, mb) = keyed[i], keyed[j]
        rel = (mb - ma) / abs(ma) if ma else math.nan
        # Expected sign of the metric change when going from i to j.
        if t.change == "decrease":  # "decreasing the key moves the metric <expect>" ⇔ increasing moves it the other way
            pred = {"up": -1, "down": 1, "no_change": 0}[t.expect]
        else:
            pred = {"up": 1, "down": -1, "no_change": 0}[t.expect]
        if not math.isfinite(rel):
            outcome = "skipped"
        elif abs(rel) < threshold:
            outcome = "supports" if pred == 0 else "no_effect"
        elif pred == 0:
            outcome = "contradicts"
        else:
            outcome = "supports" if (rel > 0) == (pred > 0) else "contradicts"
        w = 1.0 / (1.0 + d) if d else 1.0
        change = f"{ka}: {va} → {vb}"
        out.append(
            Evidence(
                "pair",
                [ra.run, rb.run],
                outcome,
                round(max(w, 0.05), 3),
                f"{ra.label} → {rb.label}: {change}; {t.metric} {ma:.4g} → {mb:.4g} ({rel:+.1%})"
                + (f"; {d} other setting(s) differ" if d else "; nothing else differs"),
                d,
                change,
                round(rel, 5) if math.isfinite(rel) else None,
            )
        )
    if skipped:
        out.append(
            Evidence("pair", [], "skipped", 0.0, f"{skipped} pair(s) differ in more than {MAX_OTHER_DIFFS} settings")
        )
    return out


def loss_behaviour_evidence(t: BeliefTest, runs: Sequence[RunFacts]) -> list[Evidence]:
    out = []
    for r in runs:
        fams = [f for f in r.behaviours if f == t.family or fnmatch.fnmatchcase(f, t.family)]
        held = [r.behaviours[f][t.behaviour] for f in fams if t.behaviour in r.behaviours[f]]
        if not held:
            continue
        ok = sum(held) * 2 >= len(held)
        out.append(
            Evidence(
                "run",
                [r.run],
                "supports" if ok else "contradicts",
                1.0,
                f"{r.label}: {t.family} {'is' if ok else 'is not'} {t.behaviour.replace('_', ' ')}"
                + (f" ({sum(held)}/{len(held)} families)" if len(held) > 1 else ""),
            )
        )
    return out


# ------------------------------------------------------------------------------------------------ verdict
def colour_of(p: float, lo: float, weight: float, n: int) -> str:
    if n == 0:
        return "grey"
    if p >= 0.85 and lo >= 0.6 and weight >= 4:
        return "dark_green"
    if p >= 0.65:
        return "green"
    if p <= 0.35:
        return "red"
    return "yellow"


def verify(
    t: BeliefTest | None,
    runs: Sequence[RunFacts],
    confidence: str = "medium",
    describe: Callable[[BeliefTest], str] | None = None,
) -> Verdict:
    if t is None:
        return Verdict(
            0.5,
            0.0,
            1.0,
            "grey",
            0,
            0,
            0,
            0,
            0,
            0.0,
            "Not testable yet — add a test (a config key and a metric, or a loss behaviour) so it can be checked.",
            "",
            [],
            len(runs),
        )
    thr = t.min_effect
    noise = seed_noise(runs, t.metric) if t.type == "config_effect" else None
    if noise is not None:
        thr = max(thr, 2 * noise)
    ev = config_effect_evidence(t, runs, thr) if t.type == "config_effect" else loss_behaviour_evidence(t, runs)
    sup = sum(e.weight for e in ev if e.outcome == "supports")
    con = sum(e.weight for e in ev if e.outcome == "contradicts")
    noeff = sum(1 for e in ev if e.outcome == "no_effect")
    skipped = sum(1 for e in ev if e.outcome == "skipped")
    used = [e for e in ev if e.outcome in ("supports", "contradicts", "no_effect")]
    a = 1.0 + PRIOR.get(confidence, 1.0) + sup
    b = 1.0 + con + 0.5 * sum(e.weight for e in ev if e.outcome == "no_effect")  # no effect weakens a claimed effect
    draws = np.random.default_rng(7).beta(a, b, 20000)
    p, lo, hi = a / (a + b), float(np.quantile(draws, 0.1)), float(np.quantile(draws, 0.9))
    p_data = (a - PRIOR.get(confidence, 1.0)) / (a - PRIOR.get(confidence, 1.0) + b)
    colour = colour_of(p, lo, sup + con, len(used))
    what = describe(t) if describe else ""
    if not used:
        summary = (
            f"No usable comparison among {len(runs)} run(s) in scope"
            + (f" ({skipped} too confounded)" if skipped else "")
            + " — the belief is neither supported nor contradicted yet."
        )
    else:
        summary = (
            f"{sum(1 for e in ev if e.outcome == 'supports')} comparison(s) support it, "
            f"{sum(1 for e in ev if e.outcome == 'contradicts')} contradict it"
            + (f", {noeff} show no effect beyond ±{thr:.1%}" if noeff else "")
            + f". Probability it holds in this scope: {p:.0%} (80 % interval {lo:.0%}–{hi:.0%})"
            + (
                f" — the runs alone say {p_data:.0%}; the rest is your stated confidence"
                if abs(p - p_data) >= 0.1
                else ""
            )
            + (f"; the noise floor from seed replicates is ±{noise:.1%}." if noise is not None else ".")
        )
        worst = max((e for e in ev if e.outcome == "contradicts"), key=lambda e: e.weight, default=None)
        if worst is not None:
            summary += f" Strongest counterexample: {worst.detail}."
    nxt = ""
    clean = sum(1 for e in used if e.other_diffs == 0)
    if colour != "dark_green":
        if t.type == "config_effect":
            nxt = (
                f"Run one baseline twice more with only {t.key} changed "
                f"({'to ' + t.value if t.change == 'set' else 'up and down'}), same seed and data: a clean pair weighs "
                f"as much as several confounded ones ({clean} clean pair(s) so far)."
            )
        else:
            nxt = f"Check more runs in scope ({len(runs)} so far) — each run with σ / loss logging is one observation."
    if what:
        summary = f"{what}. {summary}"
    return Verdict(
        round(p, 4),
        round(lo, 4),
        round(hi, 4),
        colour,
        round(sup, 3),
        round(con, 3),
        noeff,
        skipped,
        len(used),
        round(thr, 4),
        summary,
        nxt,
        ev,
        len(runs),
        round(p_data, 4),
    )


def describe(t: BeliefTest) -> str:
    if t.type == "config_effect":
        verb = {"increase": "increasing", "decrease": "decreasing", "set": f"setting it to {t.value}"}[t.change]
        exp = {"up": "raises", "down": "lowers", "no_change": "does not change"}[t.expect]
        return f"Tested as: {verb + ' ' + t.key if t.change != 'set' else t.key + ' ' + verb} {exp} {t.metric}"
    return f"Tested as: loss family {t.family} is {t.behaviour.replace('_', ' ')}"


def pair_outcome(t: BeliefTest, before: RunFacts, after: RunFacts) -> Evidence | None:
    """The belief checked on one comparison (used by the study book), either direction."""
    for a, b in ((before, after), (after, before)):
        ev = [e for e in config_effect_evidence(t, [a, b], t.min_effect) if e.outcome != "skipped"]
        if ev:
            return ev[0]
    return None
