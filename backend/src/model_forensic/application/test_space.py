"""Test Space — measure task cost on sample runs, test hypotheses, propose reviewed changes to the master config.

Production task costs (``defaults/tasks.yaml`` + ``config/tasks.local.yaml``) never change from one noisy
observation. The Test Space:

* runs **experiments**: a task kind on sample runs, ``repetitions`` times, **cold** (caches of that run dropped
  first) and **warm** (straight after), timing the same work the run page does; results are kept apart from
  production timings (document kind ``test_experiment``);
* reports **p50 / p90 / p99** (and mean, n) per mode — for experiments and for the timings production recorded
  (``task_stat``);
* evaluates **hypotheses** against the numbers: ``cost_class`` (the kind is ``heavy``…), ``warm_speedup`` (warm
  p50 at least ×F faster than cold), ``under_ms`` (cold p90 below a bound);
* turns evidence into **versioned, auditable proposals** (``config/test-space/proposals/<date>-<kind>-v<N>.yaml``,
  git-ignored): current value, proposed value, the statistics, sample size, runs, thresholds, who and when.
  A proposal needs ``min_samples`` cold samples. Accepting one writes the change to ``config/tasks.local.yaml``
  (the reviewed local overlay), records who accepted it, and reloads the task specs; rejecting keeps it as history.

Isolation: the Test Space has its own settings (``config/test-space/test-space.yaml``, example committed), its own
results store and its own proposals folder; a cold measurement drops only the caches of the sample runs it measures.
"""

from __future__ import annotations

import builtins
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from model_forensic.application.tasks import COST_ORDER, TaskManager, TaskRecord, load_specs
from model_forensic.shared.errors import NotFoundError, ValidationError
from model_forensic.shared.logging import get_logger

log = get_logger(__name__)
KIND = "test_experiment"
DEFAULTS: dict[str, Any] = {
    "sample_runs": [],
    "repetitions": 3,
    "min_samples": 5,
    # cold p90 (ms) at or above which a kind is in the class
    "thresholds_ms": {"medium": 150, "heavy": 1500, "very_heavy": 15000},
    "light_mode_max_ms": 500,  # Light Mode only runs kinds whose cold p90 stays under this
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def percentiles(ms: list[float]) -> dict[str, float | int | None]:
    if not ms:
        return {"n": 0, "p50": None, "p90": None, "p99": None, "mean": None, "min": None, "max": None}
    a = np.asarray(ms, dtype=float)
    q = np.percentile(a, [50, 90, 99])
    return {"n": int(a.size), "p50": round(float(q[0]), 1), "p90": round(float(q[1]), 1),
            "p99": round(float(q[2]), 1), "mean": round(float(a.mean()), 1), "min": round(float(a.min()), 1),
            "max": round(float(a.max()), 1)}  # fmt: skip


def classify(p90_ms: float, thresholds: dict[str, float]) -> str:
    out = "lightweight"
    for c in ("medium", "heavy", "very_heavy"):
        if p90_ms >= float(thresholds.get(c, float("inf"))):
            out = c
    return out


class TestSpace:
    """Experiments, statistics, hypotheses and proposals. ``home`` is MF_HOME (config/ lives under it)."""

    __test__ = False  # not a pytest class

    def __init__(self, svc: Any, tm: TaskManager, docs: Any, home: Path, core_specs: Path) -> None:
        self.svc, self.tm, self.docs = svc, tm, docs
        self.dir = home / "config" / "test-space"
        self.proposals_dir = self.dir / "proposals"
        self.local_tasks = home / "config" / "tasks.local.yaml"
        self.core_specs = core_specs

    # ------------------------------------------------------------------------------------------ settings
    def settings(self) -> dict[str, Any]:
        p = self.dir / "test-space.yaml"
        cfg = dict(DEFAULTS)
        if p.is_file():
            try:
                cfg.update(yaml.safe_load(p.read_text()) or {})
            except yaml.YAMLError as exc:
                raise ValidationError(f"{p}: {exc}") from None
        return cfg

    # ------------------------------------------------------------------------------------------ statistics
    def production(self) -> list[dict[str, Any]]:
        """What production measured per task kind (cold / warm percentiles) next to the configured cost."""
        cfg = self.settings()
        out = []
        for kind, spec in sorted(self.tm.specs.items()):
            samples = self.tm.timings(kind)
            cold = [s["ms"] for s in samples if s.get("cache") != "warm"]
            warm = [s["ms"] for s in samples if s.get("cache") == "warm"]
            pc = percentiles(cold)
            out.append({
                "kind": kind, "title": spec.title, "cost": spec.cost, "why": spec.why, "light_mode": spec.light_mode,
                "concurrency": spec.concurrency, "depends_on": list(spec.depends_on), "cacheable": spec.cacheable,
                "cold": pc, "warm": percentiles(warm),
                "measured_class": classify(float(pc["p90"]), cfg["thresholds_ms"]) if pc["p90"] is not None else None,
            })  # fmt: skip
        return out

    # ------------------------------------------------------------------------------------------ experiments
    def start(
        self,
        kind: str,
        runs: builtins.list[str] | None = None,
        repetitions: int | None = None,
        hypothesis: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from model_forensic.application.run_loader import RUN_KINDS, _work

        cfg = self.settings()
        if kind not in RUN_KINDS and kind != "run.summary":
            raise ValidationError(f"kind must be one of {', '.join((*RUN_KINDS, 'run.summary'))}")
        runs = [str(self.svc.ref(r)) for r in (runs or cfg["sample_runs"])]
        if not runs:
            raise ValidationError("give sample runs (or set sample_runs in config/test-space/test-space.yaml)")
        reps = max(1, min(int(repetitions or cfg["repetitions"]), 50))
        if hypothesis is not None:
            _check_hypothesis(hypothesis)
        exp: dict[str, Any] = {
            "id": uuid.uuid4().hex[:12], "kind": kind, "runs": runs, "repetitions": reps, "hypothesis": hypothesis,
            "state": "running", "created_at": _now(), "samples": [], "error": "",
            "environment": sorted({self.svc.ref(r).backend for r in runs}),
        }  # fmt: skip
        self._save(exp)

        def work(rec: TaskRecord) -> dict[str, Any]:
            total = len(runs) * reps * 2
            for i in range(reps):
                for r in runs:
                    ref = self.svc.ref(r)
                    fn = (
                        (lambda _rec, ref=ref: self.svc.record_summary(ref, self.svc.series(ref)))
                        if kind == "run.summary"
                        else _work(self.svc, ref, kind)[0]
                    )
                    for mode in ("cold", "warm"):
                        if mode == "cold":
                            self.svc.drop_cached(ref)
                        rec.progress = {"step": f"{mode} · {r} · repetition {i + 1}/{reps}",
                                        "done": len(exp["samples"]), "total": total}  # fmt: skip
                        t0 = time.perf_counter()
                        err = ""
                        try:
                            fn(rec)
                        except Exception as exc:
                            err = f"{type(exc).__name__}: {str(exc)[:200]}"
                        exp["samples"].append({"run": r, "mode": mode, "rep": i + 1, "error": err,
                                               "ms": round((time.perf_counter() - t0) * 1000, 2)})  # fmt: skip
                self._save(exp)
            exp["state"] = "done"
            exp["ended_at"] = _now()
            exp.update(self._evaluate(exp))
            self._save(exp)
            return {"experiment": exp["id"]}

        try:
            self.tm.submit("test.experiment", exp["id"], work)
        except Exception as exc:
            exp["state"], exp["error"] = "failed", str(exc)
            self._save(exp)
        return exp

    def _evaluate(self, exp: dict[str, Any]) -> dict[str, Any]:
        cfg = self.settings()
        ok = [s for s in exp["samples"] if not s["error"]]
        cold = percentiles([s["ms"] for s in ok if s["mode"] == "cold"])
        warm = percentiles([s["ms"] for s in ok if s["mode"] == "warm"])
        out: dict[str, Any] = {"stats": {"cold": cold, "warm": warm}, "errors": len(exp["samples"]) - len(ok)}
        out["measured_class"] = classify(float(cold["p90"]), cfg["thresholds_ms"]) if cold["p90"] is not None else None
        out["light_mode_eligible"] = cold["p90"] is not None and float(cold["p90"]) < float(cfg["light_mode_max_ms"])
        if exp.get("hypothesis"):
            out["verdict"] = _verdict(exp["hypothesis"], cold, warm, out["measured_class"])
        return out

    def get(self, eid: str) -> dict[str, Any]:
        try:
            exp: dict[str, Any] = self.docs.get_raw(KIND, eid)
        except NotFoundError:
            raise NotFoundError(f"experiment {eid!r} not found") from None
        if exp.get("state") == "running":
            rec = self.tm.find("test.experiment", eid)
            exp["task"] = rec.view() if rec else None
        return exp

    def list(self, limit: int = 100) -> builtins.list[dict[str, Any]]:
        return list(self.docs.list_summaries(KIND, limit=limit)[0])

    def _save(self, exp: dict[str, Any]) -> None:
        self.docs.put(KIND, exp["id"], exp, summary={
            "id": exp["id"], "kind": exp["kind"], "state": exp["state"], "created_at": exp["created_at"],
            "runs": len(exp["runs"]), "samples": len(exp["samples"]), "measured_class": exp.get("measured_class"),
            "verdict": (exp.get("verdict") or {}).get("result"),
        })  # fmt: skip

    # ------------------------------------------------------------------------------------------ proposals
    def propose(self, eid: str, who: str = "local") -> dict[str, Any]:
        """A versioned proposal from an experiment's evidence (refused when the evidence is too thin or agrees)."""
        exp = self.get(eid)
        if exp.get("state") != "done":
            raise ValidationError("the experiment has not finished")
        cfg = self.settings()
        cold = exp["stats"]["cold"]
        if cold["n"] < int(cfg["min_samples"]):
            raise ValidationError(
                f"{cold['n']} cold sample(s) — at least {cfg['min_samples']} are needed for a proposal"
            )
        spec = self.tm.spec(exp["kind"])
        changes: dict[str, dict[str, Any]] = {}
        if exp["measured_class"] != spec.cost:
            changes["cost"] = {"current": spec.cost, "proposed": exp["measured_class"]}
        if spec.light_mode and not exp["light_mode_eligible"]:
            changes["light_mode"] = {"current": True, "proposed": False}
        if not changes:
            raise ValidationError(f"the measurements agree with the configuration ({spec.cost}) — nothing to propose")
        version = 1 + sum(1 for p in self.proposals() if p["kind"] == exp["kind"])
        pid = f"{datetime.now(timezone.utc):%Y-%m-%d}-{exp['kind']}-v{version}"
        doc = {
            "id": pid, "version": version, "kind": exp["kind"], "status": "proposed", "created_at": _now(),
            "created_by": who, "changes": changes,
            "evidence": {"experiment": eid, "cold": cold, "warm": exp["stats"]["warm"], "runs": exp["runs"],
                         "repetitions": exp["repetitions"], "environment": exp["environment"],
                         "thresholds_ms": cfg["thresholds_ms"], "light_mode_max_ms": cfg["light_mode_max_ms"],
                         "errors": exp.get("errors", 0)},
            "rationale": f"cold p90 {cold['p90']} ms over {cold['n']} samples → {exp['measured_class']}",
            "review": None,
        }  # fmt: skip
        self.proposals_dir.mkdir(parents=True, exist_ok=True)
        (self.proposals_dir / f"{pid}.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))
        return doc

    def proposals(self) -> builtins.list[dict[str, Any]]:
        if not self.proposals_dir.is_dir():
            return []
        out = []
        for p in sorted(self.proposals_dir.glob("*.yaml")):
            try:
                out.append(yaml.safe_load(p.read_text()) or {})
            except yaml.YAMLError:
                log.warning("unreadable proposal", file=p.name)
        return [d for d in out if d.get("id")]

    def _proposal(self, pid: str) -> tuple[Path, dict[str, Any]]:
        p = self.proposals_dir / f"{Path(pid).name}.yaml"
        if not p.is_file():
            raise NotFoundError(f"proposal {pid!r} not found")
        return p, yaml.safe_load(p.read_text()) or {}

    def review(self, pid: str, accept: bool, who: str = "local", note: str = "") -> dict[str, Any]:
        """Accept (write the change to config/tasks.local.yaml and reload) or reject; both are recorded."""
        path, doc = self._proposal(pid)
        if doc.get("status") != "proposed":
            raise ValidationError(f"proposal is already {doc.get('status')}")
        if accept:
            local: dict[str, Any] = {}
            if self.local_tasks.is_file():
                local = yaml.safe_load(self.local_tasks.read_text()) or {}
            entry = local.setdefault("tasks", {}).setdefault(doc["kind"], {})
            for k, ch in doc["changes"].items():
                entry[k] = ch["proposed"]
            entry["_from_proposal"] = doc["id"]
            self.local_tasks.parent.mkdir(parents=True, exist_ok=True)
            self.local_tasks.write_text(
                "# Reviewed task-cost changes (Test Space proposals). Overlays the shipped defaults/tasks.yaml.\n"
                + yaml.safe_dump(local, sort_keys=False)
            )
            self.tm.specs = load_specs(self.core_specs, self.local_tasks)
        doc["status"] = "accepted" if accept else "rejected"
        doc["review"] = {"by": who, "at": _now(), "note": note}
        path.write_text(yaml.safe_dump(doc, sort_keys=False))
        return doc


HYPOTHESES = ("cost_class", "warm_speedup", "under_ms")


def _check_hypothesis(h: dict[str, Any]) -> None:
    t = h.get("type")
    if t not in HYPOTHESES:
        raise ValidationError(f"hypothesis type must be one of {', '.join(HYPOTHESES)}")
    if t == "cost_class" and h.get("expect") not in COST_ORDER:
        raise ValidationError(f"cost_class expects one of {', '.join(COST_ORDER)}")
    if t in ("warm_speedup", "under_ms") and not isinstance(h.get("value"), (int, float)):
        raise ValidationError(f"{t} needs a numeric value")


def _verdict(h: dict[str, Any], cold: dict[str, Any], warm: dict[str, Any], measured: str | None) -> dict[str, Any]:
    t = h["type"]
    if cold["n"] == 0:
        return {"result": "inconclusive", "why": "no successful sample"}
    if t == "cost_class":
        ok = measured == h["expect"]
        return {"result": "supported" if ok else "refuted",
                "why": f"measured {measured} (cold p90 {cold['p90']} ms), expected {h['expect']}"}  # fmt: skip
    if t == "warm_speedup":
        if not warm["n"] or not warm["p50"]:
            return {"result": "inconclusive", "why": "no warm sample"}
        f = float(cold["p50"]) / max(float(warm["p50"]), 1e-6)
        return {"result": "supported" if f >= float(h["value"]) else "refuted",
                "why": f"warm is ×{f:.1f} faster at p50 ({cold['p50']} → {warm['p50']} ms), expected ×{h['value']}"}  # fmt: skip
    ok = float(cold["p90"]) < float(h["value"])
    return {"result": "supported" if ok else "refuted", "why": f"cold p90 {cold['p90']} ms vs bound {h['value']} ms"}
