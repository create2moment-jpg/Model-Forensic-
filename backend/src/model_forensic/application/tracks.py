"""Continuous training tracks: a recurring line of runs — one experiment in one workspace (e.g. the continuous-training
workspace) — followed over time. Several tracks can be followed at once.

A track answers, for every new run: **is this model better than the previous one and than the best one so far?** —
per headline metric, with the metric registry's meaningful change as the noise floor (the same rule as the verdict),
plus how long the runs take and how often they fail.

Definitions live in the local document store (never in git — workspace and experiment names are site details, never in
Azure — read-only). The progress is computed from the index (deterministic; values come from the runs' summary
metrics, which ``metric_columns.fill`` loads on demand).
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from model_forensic.analysis.series_ops import direction
from model_forensic.application.verdict import metric_label
from model_forensic.domain.run import Run, RunStatus
from model_forensic.shared.errors import NotFoundError, ValidationError

KIND = "track"
MAX_RUNS = 500
EDITABLE = ("title", "workspace", "experiment", "metrics", "watch")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "track"


def changes(before: Run, after: Run) -> list[dict[str, str]]:
    """What differs between two runs of a track, from what the index already holds (no config download): code,
    data versions, environment, job parameters, compute. The config files' diff is one click away (Compare)."""
    out: list[dict[str, str]] = []

    def add(what: str, a: Any, b: Any) -> None:
        if (a or None) != (b or None):
            out.append(
                {
                    "what": what,
                    "before": "—" if a in (None, "") else str(a),
                    "after": "—" if b in (None, "") else str(b),
                }
            )

    add("code", f"{before.git.branch or ''}@{(before.git.commit or '')[:10]}".strip("@"),
        f"{after.git.branch or ''}@{(after.git.commit or '')[:10]}".strip("@"))  # fmt: skip
    data_a = {d.name: d.version for d in before.datasets}
    data_b = {d.name: d.version for d in after.datasets}
    for name in sorted(set(data_a) | set(data_b)):
        a = (data_a[name] or "latest") if name in data_a else "not used"
        b = (data_b[name] or "latest") if name in data_b else "not used"
        add(f"data {name}", a, b)
    add("environment", before.environment, after.environment)
    keys = sorted(k for k in set(before.params) | set(after.params) if not k.startswith("objective."))
    for k in keys:
        add(k, before.params.get(k), after.params.get(k))
    add(
        "compute",
        before.compute.instance_type or before.compute.target,
        after.compute.instance_type or after.compute.target,
    )
    add("nodes", before.compute.node_count, after.compute.node_count)
    return out


def available_metrics(runs: list[Run], limit: int = 60) -> list[str]:
    """The metric names the track's runs have values for, most common first (what the metric picker offers)."""
    counts: dict[str, int] = {}
    for r in runs:
        for k, v in r.summary_metrics.items():
            if v is not None:
                counts[k] = counts.get(k, 0) + 1
    return sorted(counts, key=lambda k: (-counts[k], k))[:limit]


def _secs(a: datetime | None, b: datetime | None) -> float | None:
    return (b - a).total_seconds() if a and b and b >= a else None


@dataclass
class TrackService:
    docs: Any  # DocumentStore
    svc: Any  # ForensicService

    # ── definitions ─────────────────────────────────────────────────────────────────────────────────────────────
    def all(self) -> list[dict[str, Any]]:
        return sorted(self.docs.list_raw(KIND), key=lambda t: (t.get("title") or t["id"]).lower())

    def get(self, track_id: str) -> dict[str, Any]:
        t: dict[str, Any] = self.docs.get_raw(KIND, track_id)
        return t

    def _check(self, t: dict[str, Any], own_id: str | None = None) -> None:
        ws, exp = str(t.get("workspace") or ""), str(t.get("experiment") or "")
        if not ws or not exp:
            raise ValidationError("a track needs a workspace and an experiment")
        if ws not in self.svc.sources:
            raise ValidationError(f"unknown workspace {ws!r}", details={"available": sorted(self.svc.sources)})
        for other in self.docs.list_raw(KIND):
            if other["id"] != own_id and other.get("workspace") == ws and other.get("experiment") == exp:
                raise ValidationError(f"{exp!r} in {ws!r} is already tracked as {other.get('title') or other['id']!r}")
        if not isinstance(t.get("metrics", []), list):
            raise ValidationError("metrics must be a list of metric names")

    def create(self, fields: dict[str, Any], owner: str = "") -> dict[str, Any]:
        t = {k: fields[k] for k in EDITABLE if k in fields}
        t.setdefault("metrics", [])
        t.setdefault("watch", True)
        t["title"] = str(t.get("title") or t.get("experiment") or "").strip()
        self._check(t)
        base = _slug(f"{t['workspace']}-{t['experiment']}")
        tid, n = base, 2
        while True:
            try:
                self.docs.get_raw(KIND, tid)
            except NotFoundError:
                break
            tid, n = f"{base}-{n}", n + 1
        t.update(id=tid, owner=owner, created_at=_now(), updated_at=_now(), schema_version=1)
        self.docs.put(KIND, tid, t, summary={"title": t["title"]})
        return t

    def update(self, track_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        t = self.get(track_id)
        t.update({k: fields[k] for k in EDITABLE if k in fields})
        self._check(t, own_id=track_id)
        t["updated_at"] = _now()
        self.docs.put(KIND, track_id, t, summary={"title": t.get("title")})
        return t

    def delete(self, track_id: str) -> None:
        self.get(track_id)
        self.docs.delete(KIND, track_id)

    # ── progress ────────────────────────────────────────────────────────────────────────────────────────────────
    def runs(self, t: dict[str, Any]) -> list[Run]:
        """The track's runs, oldest first: pipelines and plain runs of the experiment (stages are inside them)."""
        exp = str(t["experiment"]).replace('"', "")
        res = self.svc.search(f'experiment:"{exp}"', MAX_RUNS, 0, backend=t["workspace"], top_level=True)
        far = datetime.max.replace(tzinfo=timezone.utc)
        return sorted(res.runs, key=lambda r: (r.created_at or far, r.ref.run_id))

    def metrics_of(self, t: dict[str, Any], runs: list[Run]) -> list[str]:
        if t.get("metrics"):
            return [str(m) for m in t["metrics"]]
        from model_forensic.application.metric_columns import columns

        return list(columns(runs, list(self.svc.projects))["default"])

    def _compare(self, now: dict[str, float], ref: dict[str, float], keys: list[str]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        good = bad = noise = 0
        for k in keys:
            v, b = now.get(k), ref.get(k)
            if v is None or b is None:
                continue
            rel = (v - b) / abs(b) if b else None
            hib = direction(k, self.svc.metrics)
            floor = float(
                getattr(self.svc.metrics.lookup(k.split("[", 1)[0]), "min_meaningful_rel_change", 0.01) or 0.01
            )
            within = rel is not None and abs(rel) < floor
            g = None if rel is None or hib is None or within else (rel > 0) == hib
            good += g is True
            bad += g is False
            noise += within
            out[k] = {"delta_pct": None if rel is None else 100 * rel, "good": g, "noise": within}
        state = (
            "no_data" if not out else "better" if good and not bad else "worse" if bad and not good
            else "mixed" if good and bad else "same"
        )  # fmt: skip
        return {"state": state, "better": good, "worse": bad, "within_noise": noise, "metrics": out}

    def progress(self, track_id: str) -> dict[str, Any]:
        t = self.get(track_id)
        runs = self.runs(t)
        keys = self.metrics_of(t, runs)
        primary = keys[0] if keys else None
        now = datetime.now(timezone.utc)
        rows: list[dict[str, Any]] = []
        prev: dict[str, Any] | None = None
        best: dict[str, Any] | None = None
        prev_run: Run | None = None  # the previous finished run: what changed is told against it
        for r in runs:
            vals = {k: float(r.summary_metrics[k]) for k in keys if r.summary_metrics.get(k) is not None}
            ended = r.ended_at or (now if r.status == RunStatus.RUNNING and r.started_at else None)
            row: dict[str, Any] = {
                "ref": str(r.ref), "name": r.display_name or r.name, "status": r.status.value,
                "created_at": r.created_at.isoformat() if r.created_at else None, "user": r.user,
                "values": vals, "run_s": _secs(r.started_at, ended), "queue_s": _secs(r.created_at, r.started_at),
                "git": r.git.commit, "branch": r.git.branch,
                "data": [f"{d.name}:{d.version}" if d.version else d.name for d in r.datasets],
                "models": [f"{m.name}:{m.version}" if m.version else m.name for m in r.models],
                "vs_previous": None, "vs_best": None, "best": False,
                "changes": changes(prev_run, r) if prev_run is not None else [],
            }  # fmt: skip
            if r.status == RunStatus.COMPLETED:
                prev_run = r
            if r.status == RunStatus.COMPLETED and primary is not None and primary in vals:
                if prev is not None:
                    row["vs_previous"] = {"ref": prev["ref"], **self._compare(vals, prev["values"], keys)}
                if best is not None:
                    row["vs_best"] = {"ref": best["ref"], **self._compare(vals, best["values"], keys)}
                # the best so far changes only when a run is better on the primary metric beyond noise — a run within
                # noise of the champion does not take its place
                if best is None or (row["vs_best"] or {}).get("metrics", {}).get(primary, {}).get("good") is True:
                    best = row
                prev = row
            rows.append(row)
        if best is not None:
            best["best"] = True
        # the newest run's log errors (when its logs are here or were read before — never a download for this)
        log_note = ""
        store = getattr(self.svc, "log_patterns", None)
        if rows and store is not None:
            from model_forensic.application.log_insights import insights_if_cheap

            try:
                ins = insights_if_cheap(self.svc, store, rows[-1]["ref"])
            except Exception:
                ins = None
            errs = [p["title"] for p in (ins or {}).get("problems", []) if p["severity"] == "problem"]
            rows[-1]["log_problems"] = errs
            if errs:
                log_note = f" The logs of {rows[-1]['name']} show: {', '.join(errs[:3])}."
        run_times = [x["run_s"] for x in rows if x["status"] == "completed" and x["run_s"]]
        latest = next((x for x in reversed(rows) if x["status"] == "completed" and x["values"]), None)
        last_any = rows[-1] if rows else None
        return {
            "track": t,
            "metrics": [{"key": k, "label": metric_label(k), "higher_is_better": direction(k, self.svc.metrics)} for k in keys],
            "available_metrics": available_metrics(runs),
            "primary": primary,
            "runs": rows,
            "best": best["ref"] if best else None,
            "latest": latest["ref"] if latest else None,
            "sentence": self._sentence(rows, latest, best, primary, last_any) + log_note,
            "state": self._state(latest, last_any),
            "health": {
                "runs": len(rows),
                "completed": sum(x["status"] == "completed" for x in rows),
                "failed": sum(x["status"] == "failed" for x in rows),
                "running": sum(x["status"] in ("running", "queued") for x in rows),
                "median_run_s": statistics.median(run_times) if run_times else None,
                "latest_run_s": latest["run_s"] if latest else None,
            },
            "missing_values": [x["ref"] for x in rows if x["status"] == "completed" and not x["values"]],
        }  # fmt: skip

    @staticmethod
    def _state(latest: dict[str, Any] | None, last_any: dict[str, Any] | None) -> str:
        if last_any and last_any["status"] == "failed":
            return "failed"
        if latest is None:
            return "no_data"
        vb = latest.get("vs_best")
        if latest["best"]:
            return "best"
        return str(vb["state"]) if vb else "no_data"

    @staticmethod
    def _sentence(
        rows: list[dict[str, Any]], latest: dict[str, Any] | None, best: dict[str, Any] | None,
        primary: str | None, last_any: dict[str, Any] | None,
    ) -> str:  # fmt: skip
        if not rows:
            return "No runs in this experiment yet."
        lead = ""
        if last_any and last_any["status"] == "failed":
            lead = f"The latest run {last_any['name']} failed. "
        elif last_any and last_any["status"] in ("running", "queued"):
            lead = f"{last_any['name']} is {last_any['status']}. "
        if latest is None or primary is None:
            return lead + "No finished run has values yet — load them to follow the track."
        label = metric_label(primary)
        v = latest["values"][primary]
        vp = latest.get("vs_previous")
        part = f"{latest['name']}: {label} {v:.4g}"
        if vp and vp["state"] != "no_data":
            n = len(vp["metrics"])
            part += {"better": f", better than the previous run on {vp['better']} of {n}",
                     "worse": f", worse than the previous run on {vp['worse']} of {n}",
                     "mixed": f", vs the previous run: better on {vp['better']}, worse on {vp['worse']} of {n}",
                     "same": ", the same as the previous run (within noise)"}[vp["state"]]  # fmt: skip
        if latest["best"]:
            part += " — the best so far."
        elif best is not None:
            d = latest["vs_best"]["metrics"].get(primary, {}) if latest.get("vs_best") else {}
            gap = f" ({d['delta_pct']:+.1f}% on {label})" if d.get("delta_pct") is not None else ""
            part += f"; the best is still {best['name']}{gap}."
        else:
            part += "."
        return lead + part
