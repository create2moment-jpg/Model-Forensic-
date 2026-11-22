"""Knowledge from experience: gather what each run in a belief's scope shows, verify, keep the verdict history.

Facts per run (effective config, final metric values, loss behaviour) are cached for finished runs; verdicts are
stored per belief with a short history so the colour's evolution is visible.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from typing import Any

import numpy as np

from model_forensic.analysis import baseline_study as bs
from model_forensic.knowledge import KnowledgeEntry, KnowledgeStore
from model_forensic.knowledge.beliefs import RunFacts, Verdict, describe, verify
from model_forensic.shared.errors import NotFoundError
from model_forensic.shared.logging import get_logger

log = get_logger(__name__)
KIND = "belief_verdict"
HISTORY = 30


def test_hash(e: KnowledgeEntry) -> str:
    body = json.dumps(e.test.model_dump() if e.test else None, sort_keys=True) + e.confidence
    return hashlib.sha256(body.encode()).hexdigest()[:12]


def _final(values: Any) -> float | None:
    v = np.asarray(values, dtype=float)
    v = v[np.isfinite(v)]
    if not v.size:
        return None
    return float(np.median(v[-max(1, v.size // 20) :]))


def behaviours(series: dict[Any, Any]) -> dict[str, dict[str, bool]]:
    """Per loss family: muted / trusted (σ, majority of its terms), plateaus early / still learning / rising."""
    out: dict[str, dict[str, bool]] = {}
    weights = bs.uncertainty_weights(series)
    by: dict[str, list[str]] = {}
    for w in weights:
        by.setdefault(w.family, []).append(w.verdict)
    for fam, vs in by.items():
        out.setdefault(fam, {})
        out[fam]["muted"] = sum(v == "self-muted" for v in vs) * 2 > len(vs)
        out[fam]["trusted"] = sum(v == "up-weighted" for v in vs) * 2 > len(vs)
    for p in bs.loss_progress(series):
        d = out.setdefault(p.family, {})
        d["plateaus_early"] = p.verdict == "plateaued" and p.t90 is not None and p.t90 <= 0.3
        d["still_learning"] = p.verdict == "still learning"
        d["rising"] = p.verdict == "rising"
    return out


class BeliefService:
    def __init__(self, svc: Any, store: KnowledgeStore, docs: Any) -> None:
        self.svc, self.store, self.docs = svc, store, docs
        self._facts: dict[str, RunFacts] = {}
        self._lock = threading.Lock()

    # --------------------------------------------------------------------------------------------- facts
    def facts(self, ref: str) -> RunFacts:
        hit = self._facts.get(ref)
        if hit is not None:
            return hit
        run = self.svc.get_run(ref)
        stages = [n for n, _ in self.svc.stages(run.ref)]
        cfg = bs.effective_config(self.svc.flat_config(run.ref), stages)
        series = {s.key: s for s in self.svc.series(run.ref)}
        finals = {str(k): v for k, s in series.items() if (v := _final(s.values)) is not None}
        facts = RunFacts(str(run.ref), run.display_name or run.name, cfg, finals, behaviours(series))
        if run.status.value not in ("running", "queued", "starting", "preparing"):
            with self._lock:
                self._facts[ref] = facts
        return facts

    def scope_runs(self, query: str, limit: int) -> list[str]:
        res = self.svc.search(query or "", limit=limit)
        runs = [r for r in res.runs if r.status.value not in ("failed", "canceled", "cancelled")]
        refs = {str(r.ref) for r in runs}
        # A pipeline stage is not an independent observation when its pipeline is in scope (same training).
        return [str(r.ref) for r in runs if not (r.parent and str(r.parent) in refs)]

    def gather(self, query: str, limit: int, progress: Any = None) -> list[RunFacts]:
        out = []
        refs = self.scope_runs(query, limit)
        for i, ref in enumerate(refs):
            if progress:
                progress(f"reading run {i + 1}/{len(refs)}")
            try:
                out.append(self.facts(ref))
            except Exception as exc:  # one unreadable run must not stop the verification
                log.warning("run skipped in belief check", run=ref, error=type(exc).__name__)
        return out

    # --------------------------------------------------------------------------------------------- beliefs
    def beliefs(self) -> list[KnowledgeEntry]:
        entries, _ = self.store.load()
        return [e for e in entries if e.kind == "belief"]

    def entry(self, bid: str) -> KnowledgeEntry:
        e = next((x for x in self.beliefs() if x.id == bid), None)
        if e is None:
            raise NotFoundError(f"belief {bid!r} not found")
        return e

    def verify(self, bid: str, progress: Any = None) -> dict[str, Any]:
        e = self.entry(bid)
        t = e.test
        runs = self.gather(t.scope, t.max_runs, progress) if t else []
        v: Verdict = verify(t, runs, e.confidence, describe)
        now = datetime.now(timezone.utc).isoformat()
        try:
            prev = self.docs.get_raw(KIND, bid)
        except NotFoundError:
            prev = {"history": []}
        hist = [*prev.get("history", []), {"at": now, "p": v.p, "colour": v.colour, "n": v.n}][-HISTORY:]
        doc = {"id": bid, "at": now, "test_hash": test_hash(e), "verdict": v.to_dict(), "history": hist}
        summ = {
            "id": bid,
            "at": now,
            "test_hash": doc["test_hash"],
            "p": v.p,
            "lo": v.lo,
            "hi": v.hi,
            "colour": v.colour,
            "n": v.n,
            "support": v.support,
            "contra": v.contra,
            "no_effect": v.no_effect,
        }
        self.docs.put(KIND, bid, doc, summary=summ)
        return doc

    def latest(self, bid: str) -> dict[str, Any] | None:
        try:
            d: dict[str, Any] = self.docs.get_raw(KIND, bid)
        except NotFoundError:
            return None
        return d

    def overview(self) -> list[dict[str, Any]]:
        """Every belief with its latest verdict summary (stale when the test changed since)."""
        verdicts = {s["id"]: s for s in self.docs.list_summaries(KIND, limit=100_000)[0]}
        out = []
        for e in self.beliefs():
            s = verdicts.get(e.id)
            out.append(
                {
                    "entry": e.model_dump(),
                    "verdict": s,
                    "stale": bool(s and s.get("test_hash") != test_hash(e)),
                    "testable": e.test is not None,
                }
            )
        return out

    def vocabulary(self, query: str = "", limit: int = 12) -> dict[str, Any]:
        """Config keys (numeric ones marked), metric keys and loss families seen in the scope — for the test builder."""
        keys: dict[str, bool] = {}
        metrics: dict[str, int] = {}
        fams: set[str] = set()
        for f in self.gather(query, limit):
            for k, v in f.config.items():
                numeric = isinstance(v, (int, float)) and not isinstance(v, bool)
                keys[k] = keys.get(k, False) or numeric
            for m in f.finals:
                metrics[m] = metrics.get(m, 0) + 1
            fams |= set(f.behaviours)
        return {
            "config_keys": [{"key": k, "numeric": n} for k, n in sorted(keys.items())],
            "metrics": [m for m, _ in sorted(metrics.items(), key=lambda kv: (-kv[1], kv[0]))][:2000],
            "families": sorted(fams),
        }
