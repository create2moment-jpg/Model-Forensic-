"""HTTP routes: /compare, /studies."""

from __future__ import annotations

import math
from typing import Any

from fastapi import FastAPI, HTTPException

from model_forensic.api.routes.context import RouteContext
from model_forensic.application.ports import is_offline
from model_forensic.application.service import ForensicService
from model_forensic.container import Container
from model_forensic.domain.metrics import MetricKey
from model_forensic.shared import identity
from model_forensic.shared.downsample import m4_indices


def register(app: FastAPI, ctx: RouteContext) -> None:
    container: Container = ctx.container
    p = ctx.p
    sdump = ctx.sdump
    studies = ctx.studies
    svc: ForensicService = ctx.svc

    @app.get(f"{p}/compare/verdict")
    def compare_verdict(baseline: str, candidate: str, stage: str | None = None) -> dict[str, Any]:
        """The comparison in one sentence: better / worse on how many metrics, the largest moves, what changed."""
        from model_forensic.application.verdict import compare_verdict as cv

        return cv(svc, baseline, candidate, stage or None)

    @app.get(f"{p}/compare")
    def compare(baseline: str, candidate: str, replicates: bool = True, stage: str | None = None) -> dict[str, Any]:
        """Metric deltas and the config diff. ``stage``: compare what that pipeline stage logged (each run that has
        it; a run without it keeps its headline values) — stages are never mixed."""
        return svc.compare(baseline, candidate, use_replicates=replicates, stage=stage or None).model_dump(mode="json")

    def _curves(
        runs: list[str],
        keys: list[str] | None,
        max_points: int = 600,
        stage_map: dict[str, str] | None = None,
        stage: str | None = None,
    ) -> dict[str, Any]:
        """``stage_map``: run → pipeline stage whose metrics to use (default: the headline = last stage). ``stage``:
        the same stage for every run that has it (an explicit ``stage_map`` entry wins)."""
        from model_forensic.analysis.divergence import compare_curves

        if not 2 <= len(runs) <= 10:
            raise HTTPException(400, "compare 2 to 10 runs")
        info: list[dict[str, Any]] = []
        labels: dict[str, str] = {}
        stage_map = {str(svc.ref(k)): v for k, v in (stage_map or {}).items() if v}
        chosen: dict[str, str] = {}
        for i, r in enumerate(runs):
            run = svc.get_run(r)
            stages_r = [n for n, _ in svc.stages(run.ref)]
            st_r = stage_map.get(str(run.ref), "") or (stage if stage and stage in stages_r else "")
            if st_r and st_r not in stages_r:
                raise HTTPException(400, f"{run.name} has no stage {st_r!r} (stages: {', '.join(stages_r) or 'none'})")
            lab = run.display_name or run.name
            lab = f"{lab} · {st_r}" if st_r else lab
            lab = lab if lab not in labels.values() else f"{lab} ({r})"
            labels[r] = lab
            chosen[r] = st_r
            info.append(
                {
                    "ref": str(run.ref),
                    "label": lab,
                    "status": run.status.value,
                    "alias": f"R{i + 1}",
                    "stages": stages_r,
                    "stage": st_r,
                }
            )

        def pick_series(r: str, mk: MetricKey) -> Any:
            st_r = chosen.get(r, "")
            if not st_r:
                got = svc.series(r, [mk])
                return got[0] if got else None
            for s_ in svc.series(r):  # the chosen stage's copy (stage=…), or the unique series that stage logged
                d = s_.key.dim_dict
                if s_.key.name != mk.name or s_.key.split != mk.split:
                    continue
                if tuple((a, b) for a, b in s_.key.dims if a != "stage") != mk.dims:
                    continue
                if d.get("stage") == st_r or ("stage" not in d and s_.provenance.get("stage") == st_r):
                    return s_
            return None

        if not keys:
            if any(chosen.values()):  # the keys every run has in the stage it shows (never another stage's)
                views = [{str(x.key) for x in svc.stage_series(r, chosen[r] or None)} for r in runs]
                both = set.intersection(*views)
                # plain names first; an evaluation stage often logs only sliced ones — then the least sliced
                common = sorted(k for k in both if "[" not in k) or sorted(
                    (k for k in both if k.count("=") == 1), key=lambda k: (not k.startswith(("val/", "test/")), k)
                )
            else:
                counts = svc.metric_keys(runs)
                common = [k for k, n in counts.items() if n == len(runs) and "[" not in k]
            pick = [k for k in ("train/loss",) if k in common]
            pick += [
                k
                for k in common
                if k.startswith(("val/", "test/"))
                and svc.metrics.lookup(MetricKey.parse(k).name).higher_is_better is not None
            ][: 4 - len(pick)]
            keys = pick or common[:4]
        ref_run = runs[0]
        lrs = {}
        for r in runs:
            try:
                lrs[labels[r]] = svc.lr_series(r)
            except Exception:  # LR is optional context
                lrs[labels[r]] = None
        changes = {}
        for r in runs[1:]:
            try:
                changes[labels[r]] = svc.diff_configs(ref_run, r).changes
            except Exception:
                changes[labels[r]] = ()
        # Seeds of the reference (same `group` tag, not already compared) give a noise floor for "is it real?".
        try:
            seed_refs = [x for x in svc.replicates(ref_run) if str(x) not in {str(svc.ref(r)) for r in runs}][:9]
        except Exception:
            seed_refs = []
        out_keys = []
        for key in keys[:6]:
            mk = MetricKey.parse(key)
            series, charts = {}, []
            for r in runs:
                one = pick_series(r, mk)
                if one is None:
                    continue
                series[labels[r]] = one
                st, v = one.arrays()
                idx = m4_indices(v, max_points)
                charts.append(
                    {
                        "run": labels[r],
                        "ref": r,
                        "steps": st[idx].tolist(),
                        "values": [x if math.isfinite(x) else None for x in v[idx].tolist()],
                    }
                )
            seeds = []
            for rep in seed_refs:
                got_r = svc.series(rep, [mk])
                if got_r:
                    seeds.append(got_r[0])
            cmp = compare_curves(
                key, labels[ref_run], series, registry=svc.metrics, lrs=lrs, changes=changes, reference_seeds=seeds
            )
            out_keys.append({**cmp.to_dict(), "series": charts})
        return {
            "reference": labels[ref_run],
            "runs": info,
            "keys": out_keys,
            "reference_seeds": [str(x) for x in seed_refs],
        }

    @app.get(f"{p}/compare/metric-names")
    def compare_metric_names(baseline: str, candidate: str) -> dict[str, Any]:
        """Metrics only one of the two runs has, and suggested pairs (the same metric under another name — e.g. a
        local Lightning run's ``train_loss_step`` and an Azure ML run's ``train/loss``)."""
        from model_forensic.application.metric_matching import suggest
        from model_forensic.interface import metric_aliases

        ref_keys = [str(s.key) for s in svc.series(baseline)]
        cand = {str(s.key): str(s.provenance.get("raw_name") or s.key) for s in svc.series(candidate)}
        return {**suggest(ref_keys, cand), "aliases": metric_aliases.load(), "path": str(metric_aliases.path())}

    @app.put(f"{p}/settings/metric-aliases")
    def save_metric_aliases(body: dict[str, Any]) -> dict[str, Any]:
        """Save metric-name aliases (config/metric-aliases.local.yaml) — applied to every workspace at once; the
        runs named in ``runs`` are re-read with the new names."""
        from model_forensic.container import apply_naming
        from model_forensic.interface import metric_aliases

        add = {str(k): str(v) for k, v in (body.get("add") or {}).items()}
        drop = {str(k) for k in (body.get("remove") or [])}
        cur = {k: v for k, v in metric_aliases.load().items() if k not in drop}
        try:
            path = metric_aliases.save({**cur, **add})
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        apply_naming(svc, getattr(svc, "projects", []))
        for r in body.get("runs") or []:
            svc.drop_cached(str(r))  # re-read with the new names
        container.docs.audit("api", "settings.metric_aliases", {"added": len(add), "removed": len(drop)})
        return {"aliases": metric_aliases.load(), "path": str(path)}

    @app.post(f"{p}/compare/curves")
    def compare_curves_api(body: dict[str, Any]) -> dict[str, Any]:
        """Where and why 2–10 runs diverge from the first (reference) run, per metric. ``stages`` maps a pipeline run
        to the stage whose metrics are compared (explicit mapping; differently named stages are never guessed)."""
        keys = [str(k) for k in body.get("keys") or []] or None
        return _curves(
            [str(r) for r in body.get("runs", [])],
            keys,
            stage_map=dict(body.get("stages") or {}),
            stage=str(body.get("stage") or "") or None,
        )

    @app.post(f"{p}/compare/ask")
    def compare_ask(body: dict[str, Any]) -> dict[str, Any]:
        """A grounded answer about a curves comparison; the evidence is its deterministic reading."""
        from dataclasses import asdict

        from model_forensic.ai.copilot import ask_chart as ask

        keys = [str(k) for k in body.get("keys") or []] or None
        res = _curves([str(r) for r in body.get("runs", [])], keys)
        lines: list[str] = []
        for k in res["keys"]:
            lines.append(k["summary"])
            for r in k["rows"]:
                lines.append(f"{k['key']}: {r['sentence']}" + (f" ({r['lr_note']})" if r["lr_note"] else ""))
                if r["fits"] and r["outcome"] not in ("same", "insufficient"):
                    lines.append(
                        f"{k['key']}: config changes of {r['run']} that fit the timing: " + "; ".join(r["fits"])
                    )
        evidence = [{"id": f"E{i + 1}", "text": t} for i, t in enumerate(lines)]
        if is_offline(container.llm) or not lines:
            recs = []
            for k in res["keys"]:
                for r in k["rows"]:
                    if r["outcome"] in ("ahead", "behind", "crossed") and r["fits"]:
                        recs.append(
                            f"To test why {r['run']} is {r['outcome']} on {k['key']}: re-run the reference with "
                            f"only '{r['fits'][0]}' changed."
                        )
            return {
                "summary": "No LLM is configured, so here is the rule-based reading: "
                + (lines[0] if lines else "nothing to compare."),
                "claims": [
                    {
                        "type": "RECOMMENDATION",
                        "text": t,
                        "evidence": ["E1"],
                        "confidence": None,
                        "verified": True,
                        "problems": [],
                    }
                    for t in dict.fromkeys(recs)
                ][:5],
                "evidence": evidence,
                "model": container.llm.model,
            }
        ans = ask(container.llm, "comparison", str(body.get("question", "")), lines)
        container.docs.audit(identity.current().id, "compare_ask", {"runs": body.get("runs", [])})
        assert ans is not None
        return {
            "summary": ans.summary,
            "claims": [asdict(c) for c in ans.claims],
            "verified_ratio": ans.verified_ratio,
            "evidence": evidence,
            "model": container.llm.model,
        }

    @app.post(f"{p}/studies/{{sid}}/comparisons")
    def study_add_comparison(sid: str, body: dict[str, Any]) -> dict[str, Any]:
        """Save a curves comparison (recomputed here, never taken from the client) into a study as evidence."""
        keys = [str(k) for k in body.get("keys") or []] or None
        reading = _curves([str(r) for r in body.get("runs", [])], keys, stage_map=dict(body.get("stages") or {}))
        return sdump(studies.add_comparison(sid, reading, str(body.get("note", ""))))

    @app.delete(f"{p}/studies/{{sid}}/comparisons/{{cid}}")
    def study_delete_comparison(sid: str, cid: str) -> dict[str, Any]:
        return sdump(studies.delete_comparison(sid, cid))
