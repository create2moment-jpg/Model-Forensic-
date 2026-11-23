"""``mf`` commands — Runs: demo data, sync, search, show, stages, compare, analyze, report, download, inspect, cache."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from model_forensic.cli import base
from model_forensic.cli.base import ConfigOpt, app
from model_forensic.container import build
from model_forensic.settings import Settings


@app.command()
def demo(config: Path | None = ConfigOpt) -> None:
    """Generate deterministic demo runs and index them."""
    from model_forensic.infrastructure.demo.synthetic import generate_demo

    s = Settings.load(config)
    first_local = next((b for b in s.backends if b.kind == "local"), None)
    root = Path(first_local.options.get("root", s.data_dir / "demo-runs")) if first_local else s.data_dir / "demo-runs"
    ids = generate_demo(root)
    c = build(s)
    n = c.service.sync()
    typer.echo(f"wrote {len(ids)} demo runs to {root}; indexed {n}")
    title = "GT finetune vs online baseline"
    local = next((b for b in s.backends if b.kind == "local"), None)
    if local is not None and not any(st.title == title for st in c.studies.all()):
        backend = local.name
        st = c.studies.create(
            title,
            goal="Is the GT pretrain + finetune pipeline really behind the online baseline (0.40 vs 0.55)?",
            runs=[f"{backend}:xf-pipe-finetune"],
            baseline=f"{backend}:xf-online-baseline",
        )
        typer.echo(f"demo study {st.id}: mf study book {st.id}")
    demo_expectations(c.knowledge.folder)
    typer.echo("try:  mf search 'failed training jobs from last week'")
    typer.echo("      mf compare baseline-cl4f-s0 cand-radar-8f")


def demo_expectations(knowledge_folder: Path) -> None:
    """Expectations for the demo detector runs, as a local LLM would write them from the (imaginary) code."""
    from model_forensic.application.expectations import folder

    d = folder(knowledge_folder)
    p = d / "demo-detector.yaml"
    if p.exists():
        return
    d.mkdir(parents=True, exist_ok=True)
    p.write_text(
        """# Demo: what the code of the demo detector says its losses should do (written as by the loss_expectations task).
when_config: {"loss.cls_weight": "*"}
codebase: demo-detector
generated_by: demo
generated_at: "2026-01-01"
config_source: config.yaml
terms:
  - name: train/loss/cls
    formula: sigmoid focal loss (alpha 0.25, gamma 2) / num matched
    weight: 2.0
    weight_key: loss.cls_weight
    initial: {value: 2.0, tolerance: 0.3, why: "focal loss at init with prior 0.01"}
    range: {min: 0}
    layers: 6
    layer_order: decreasing
    code_refs: ["heads/sparse_head.py:212"]
  - name: train/loss/bbox
    formula: L1 on normalised (x, y, z, w, l, h, sin, cos) / num matched
    weight: 0.25
    weight_key: loss.reg_weight
    initial: {value: 1.4, tolerance: 0.3, why: "mean |target| of normalised boxes at init"}
    range: {min: 0}
    code_refs: ["heads/sparse_head.py:230"]
  - name: train/loss/velocity
    formula: L1 on (vx, vy) of matched boxes with a velocity label
    weight: 0.25
    initial: {value: 1.45, tolerance: 0.3}
    range: {min: 0}
    code_refs: ["heads/sparse_head.py:241"]
total:
  name: train/loss
  composition: weighted_sum
  code_refs: ["detector.py:140"]
config:
  - key: model.fusion.modalities
    one_of: [["camera", "lidar"], ["camera", "lidar", "radar"], ["camera", "radar"]]
    why: the fusion module is built for these combinations
    code_refs: ["fusion.py:31"]
notes: ["velocity has no target for frames without a velocity label: its loss is 0 there"]
""",
        encoding="utf-8",
    )


@app.command()
def sync(backend: str | None = None, config: Path | None = ConfigOpt) -> None:
    """Index run metadata from configured backends."""
    typer.echo(f"indexed {base.make_container(config).service.sync(backend)} runs")


@app.command()
def search(
    query: str = typer.Argument(""),
    limit: int = 20,
    as_json: bool = typer.Option(False, "--json"),
    config: Path | None = ConfigOpt,
) -> None:
    """Search runs (DSL or natural language)."""
    res = base.make_container(config).service.search(query, limit)
    if as_json:
        typer.echo(json.dumps([r.model_dump(mode="json") for r in res.runs], indent=1))
        return
    for n in res.notes:
        typer.secho(f"  {n}", fg="cyan")
    typer.echo(f"{res.total} run(s) in {res.took_ms} ms")
    for r in res.runs:
        nds = r.summary_metrics.get("NDS")
        typer.echo(
            f"  {r.ref!s:38} {r.status.value:10} {r.experiment or '-':18} {(r.user or '-'):8} "
            f"{r.created_at:%Y-%m-%d} " + (f"NDS={nds:.4f}" if nds is not None else "")
            if r.created_at
            else str(r.ref)
        )


@app.command()
def show(run: str, baseline: str | None = None, config: Path | None = ConfigOpt) -> None:
    """Forensic overview of a run, with 'what changed' against a baseline."""
    o = base.make_container(config).service.overview(run, baseline)
    r = o.run
    typer.secho(f"{r.ref}  [{r.status.value}]  {r.display_name or ''}", bold=True)
    typer.echo(f"  experiment {r.experiment}  user {r.user}  commit {r.git.commit} ({r.git.branch})")
    typer.echo(
        f"  compute {r.compute.target or '-'}"
        + (f" {r.compute.gpu_count}x{r.compute.gpu_type}" if r.compute.gpu_count else "")
        + f"  duration {(r.duration_s or 0) / 3600:.2f} h"
    )
    if r.failure:
        typer.secho(f"  FAILED: {r.failure.message}", fg="red")
    typer.echo(f"  {len(o.metric_keys)} metric series, {len(o.artifacts)} artifacts, {len(o.configs)} config source(s)")
    for k, v in sorted(o.summary.items()):
        if "[" not in k:
            typer.echo(f"    {k:28} {v:.5g}")
    if o.what_changed:
        typer.secho(f"\n  What changed vs {o.baseline}:", bold=True)
        for ch in o.what_changed.config_diff.meaningful:
            typer.echo(f"    cfg  {ch.describe()}  [{ch.category}]")
        for d in o.what_changed.regressions[:8]:
            typer.secho(
                f"    ↓    {d.metric} {d.relative:+.1%}" if d.relative is not None else f"    ↓ {d.metric}", fg="red"
            )
        for d in o.what_changed.improvements[:8]:
            typer.secho(
                f"    ↑    {d.metric} {d.relative:+.1%}" if d.relative is not None else f"    ↑ {d.metric}", fg="green"
            )


@app.command()
def stages(run: str, config: Path | None = ConfigOpt) -> None:
    """List a pipeline's stages (child runs) with status and metric counts."""
    c = base.make_container(config)
    sts = c.service.stages(run)
    if not sts:
        typer.echo("not a pipeline (no stages)")
        return
    for name, ref in sts:
        child = c.service.get_run(ref)
        n = len(c.service.series(ref))
        typer.echo(f"  {name:<28} {child.status.value:<10} {n:>5} metrics  {ref}")


@app.command()
def compare(baseline: str, candidate: str, config: Path | None = ConfigOpt) -> None:
    """Compare a candidate run against a baseline."""
    svc = base.make_container(config).service
    cmp = svc.compare(baseline, candidate)
    basis = f"{len(cmp.baseline_replicates)} replicate(s)" if cmp.baseline_replicates else "thresholds (no replicates)"
    typer.secho(f"{baseline} → {candidate}   significance: {basis}", bold=True)
    typer.echo("\nConfig changes:")
    for ch in cmp.config_diff.meaningful:
        typer.echo(f"  {ch.describe()}  [{ch.category}]")
    typer.echo("\nMetrics:")
    for d in sorted(cmp.deltas, key=lambda d: d.metric):
        color = {"regressed": "red", "improved": "green"}.get(d.verdict.value)
        rel = f"{d.relative:+7.2%}" if d.relative is not None else "    n/a"
        z = f" z={d.z_score:+.1f}" if d.z_score is not None else ""
        typer.secho(f"  {d.metric:30} {d.baseline:10.4g} → {d.candidate:10.4g} {rel}{z}  {d.verdict.value}", fg=color)


@app.command()
def analyze(
    run: str,
    baseline: str | None = None,
    analysis: list[str] = typer.Option(None, "--analysis", "-a"),
    config: Path | None = ConfigOpt,
) -> None:
    """Run deterministic forensic analyses and print findings."""
    results = base.make_container(config).service.analyze(run, baseline, analysis or None)
    for r in results:
        if r.skipped_reason:
            typer.secho(f"- {r.analysis}: skipped ({r.skipped_reason})", dim=True)
            continue
        typer.secho(f"- {r.analysis}: {len(r.findings)} finding(s)", bold=True)
        for n in r.notes:
            typer.echo(f"    note: {n}")
        for f in sorted(r.findings, key=lambda f: -f.severity.rank):
            typer.echo(f"  {base.ICON[f.severity.value]} {f.title}  ({f.label.value}, conf={f.confidence.value})")
            typer.echo(f"         {f.statement}")
            for e in f.evidence[:4]:
                typer.secho(f"         • {e.statement}", dim=True)
            for h in f.hypotheses[:3]:
                typer.echo(f"         ? {h.statement}")
            for x in f.recommendations[:3]:
                typer.echo(f"         → [{x.priority}] {x.title} (info gain {x.information_gain:.2f})")


@app.command()
def ask(
    run: str,
    question: str | None = typer.Argument(None),
    baseline: str | None = None,
    prompt: str | None = typer.Option(None, help="Prompt library id, e.g. cmp_camera_backbone (see `mf prompts`)"),
    persona: str = "analyst",
    other: list[str] = typer.Option(None, "--other", help="Additional run to include (repeatable, max 8)"),
    var: list[str] = typer.Option(None, "--var", help="Template variable key=value (repeatable)"),
    config: Path | None = ConfigOpt,
) -> None:
    """AI investigation grounded in deterministic findings (offline heuristic if no LLM configured)."""
    from model_forensic.ai.investigator import Persona

    c = base.make_container(config)
    variables = dict(v.split("=", 1) for v in var or [] if "=" in v)
    inv = c.investigator().investigate(
        run,
        question,
        baseline=baseline,
        prompt_id=prompt,
        persona=Persona(persona),
        variables=variables,
        others=other or [],
    )
    d = inv.to_dict()
    c.docs.put("investigation", inv.id, d, [c.service.ref(run)])
    typer.secho(f"[{d['model']}] {d['summary']}", bold=True)
    for cl in d["claims"]:
        mark = "✓" if cl["verified"] else "✗"
        typer.echo(f"  {mark} {cl['type']:14} {cl['text']}  [{', '.join(cl['evidence'])}]")
        for p in cl["problems"]:
            typer.secho(f"      ! {p}", fg="yellow")
    typer.echo(f"investigation id: {inv.id}")


@app.command()
def prompts(
    category: str | None = typer.Option(None, help="Filter by category"), config: Path | None = ConfigOpt
) -> None:
    """List prompt templates (id, category, required variables)."""
    lib = base.make_container(config).prompts
    rows = [p for p in lib.all() if category is None or p.category == category]
    for cat in sorted({p.category for p in rows}):
        typer.secho(cat, bold=True)
        for p in [p for p in rows if p.category == cat]:
            extra = sorted(p.variables - {"run_id", "baseline_run_id", "run_ids"})
            flags = ("needs --baseline " if p.needs_baseline else "") + (f"vars: {', '.join(extra)}" if extra else "")
            typer.echo(f"  {p.id:34} {p.title}  {typer.style(flags, dim=True)}")


@app.command()
def report(
    run: str,
    baseline: str | None = None,
    out: Path = typer.Option(Path("report.md"), "--out", "-o"),
    investigation: str | None = None,
    config: Path | None = ConfigOpt,
) -> None:
    """Generate a forensic report (.md or .html by extension)."""
    from model_forensic.reports.markdown import build_report, to_html

    c = base.make_container(config)
    svc = c.service
    runs = [("candidate" if baseline else "target", svc.get_run(run))]
    if baseline:
        runs.insert(0, ("baseline", svc.get_run(baseline)))
    title = f"Forensic report: {run}" + (f" vs {baseline}" if baseline else "")
    md = build_report(
        title,
        runs,
        svc.analyze(run, baseline),
        svc.compare(baseline, run) if baseline else None,
        c.docs.get_raw("investigation", investigation) if investigation else None,
    )
    out.write_text(to_html(md, title) if out.suffix == ".html" else md)
    typer.echo(f"wrote {out}")


@app.command()
def download(
    run: str,
    profile: str = typer.Option(
        "fast_debug",
        help="fast_debug | logs | configs | eval_outputs | eval_json | "
        "eval_parquet | metrics_csv | evaluation_only | checkpoint_only | full_forensic | metrics",
    ),
    stage: list[str] = typer.Option([], help="Only these pipeline stages (repeatable)"),
    plan: bool = typer.Option(False, "--plan", help="Show what each profile would fetch, download nothing"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation for downloads > 5 GB"),
    config: Path | None = ConfigOpt,
) -> None:
    """Download a run's files (pipelines: all stages) into the local cache, with a provenance manifest."""
    from model_forensic.domain.downloads import DownloadProfile

    c = base.make_container(config)
    if plan:
        for name, pl in c.service.plans(run, stage or None).items():
            unk = f" (+{pl['unknown_size']} unknown size)" if pl["unknown_size"] else ""
            typer.echo(
                f"  {name:<16} {pl['files']:>5} files  {pl['bytes'] / 1e6:>10.1f} MB{unk}  cached {pl['cached']}"
            )
        return
    if profile == "metrics":
        j, cs = c.service.export_metrics(run)
        typer.echo(f"wrote {j}\nwrote {cs}")
        return
    prof = DownloadProfile(profile)
    chosen = c.service.download_plan(run, prof, stage or None)
    size = sum(a.size_bytes or 0 for a in chosen)
    if size > 5e9 and not yes and not typer.confirm(f"{len(chosen)} files, {size / 1e9:.1f} GB — download?"):
        raise typer.Exit(1)
    files, prog = c.service.download(run, prof, stages=stage or None)
    typer.echo(
        f"{len(files)} files ({prog.cached_files} already cached, {len(prog.failed)} failed) → "
        f"{c.service.artifact_store.run_root(c.service.ref(run))}"
    )
    for path, err in prog.failed[:10]:
        typer.secho(f"  ✗ {path}: {err}", fg="red")


@app.command("inspect")
def inspect_cmd(
    folder: Path,
    as_json: bool = typer.Option(False, "--json", help="Machine-readable report"),
) -> None:
    """Offline check of downloaded artefacts (e.g. the v1 tool's outputs/_azure_artefacts): what is recognised."""
    from model_forensic.application.inspect import inspect
    from model_forensic.shared.paths import describe, project_path

    folder = project_path(folder)  # relative → under PROJECT_PATH, wherever mf runs
    if not folder.is_dir():
        roots = describe()
        typer.secho(f"not a folder: {folder}", fg="red")
        typer.echo(
            f"relative paths resolve from the project root {roots['project_root']} ({roots['project_root_from']}); "
            "set PROJECT_PATH to your project folder (see README → Setup)"
        )
        raise typer.Exit(2)
    rep = inspect(folder)
    if as_json:
        typer.echo(json.dumps(rep.as_dict(), indent=2))
        raise typer.Exit(0 if rep.ok() else 1)
    typer.echo(f"{rep.files} files under {folder}")
    if rep.metric_names:
        col = "green" if not rep.unmapped else "yellow"
        typer.secho(f"  metric names: {rep.mapped}/{rep.metric_names} map to train/val", fg=col)
        for n in rep.unmapped[:15]:
            typer.echo(f"    unmapped: {n}")
    for c in rep.configs[:10]:
        typer.echo(f"  config {c['path']}: {c['keys']} keys, ruler {c['ruler'] or '-'}, monitor {c['monitor'] or '-'}")
    if rep.eval_classes:
        typer.echo(f"  evaluation JSONs: {len(rep.eval_classes)} class(es)")
        for cls, fams in list(rep.eval_classes.items())[:12]:
            typer.echo(f"    {cls}: {', '.join(fams)}")
    for path in rep.eval_unrecognised[:10]:
        typer.echo(f"    unrecognised: {path}")
    for cls, d in rep.gt_degenerate.items():
        typer.secho(
            f"  degenerate GT ({cls}): " + ", ".join(f"{a} {v:.0%} in one bin" for a, v in d.items()), fg="yellow"
        )
    for pq in rep.parquets[:10]:
        typer.echo(f"  parquet {pq['path']}: {pq['kind']} — {pq['note']}")
    colour = {"critical": "red", "parse_failure": "red", "unsupported_schema": "yellow", "missing_data": "cyan",
              "recoverable_warning": "yellow", "info": None}  # fmt: skip
    for m in [m for m in rep.messages if m.severity != "info"][:30]:
        typer.secho(f"  ! [{m.severity}] {m.text()}", fg=colour.get(m.severity))
        if m.expected or m.observed:
            typer.echo(f"      expected {json.dumps(m.expected)[:160]}")
            typer.echo(f"      observed {json.dumps(m.observed)[:160]}")
        if m.remediation:
            typer.echo(f"      fix: {m.remediation}")
    typer.secho(
        "✓ everything recognised" if rep.ok() else "! see the notes above — send them to improve the readers",
        fg="green" if rep.ok() else "yellow",
    )
    raise typer.Exit(0 if rep.ok() else 1)


LIGHT_FILES = r"(^|/)(logs?|user_logs|azureml-logs)/|\.(log|txt)$|config[^/]*\.(ya?ml|json)$|(^|/)metrics\.csv$"


@app.command("cache-clear")
def cache_clear(
    run: str | None = typer.Option(None, "--run", help="Only this run (<workspace>:<run id>); default: every run"),
    keep_light: bool = typer.Option(
        True,
        "--keep-light/--all",
        help="Keep logs, configs and Lightning metrics.csv (default); --all removes them too",
    ),
    yes: bool = typer.Option(False, "--yes", help="Do not ask"),
    config: Path | None = ConfigOpt,
) -> None:
    """Free disk: remove downloaded artifacts from the local cache (Azure is never touched; files can be fetched again)."""
    import re as _re

    c = base.make_container(config)
    targets: list[tuple[str | None, str | None]] = [(None, None)]
    if run:
        ref = c.service.ref(run)
        try:
            stages = list(c.service.get_run(ref).children)  # a pipeline's files are stored under its stages
        except Exception:
            stages = []
        targets = [(r.backend, r.run_id) for r in [ref, *stages]]
    light = _re.compile(LIGHT_FILES, _re.I)
    what = f"run {run}" if run else "every run"
    if not yes and not typer.confirm(
        f"Remove cached files of {what}{' (keeping logs, configs, metrics.csv)' if keep_light else ''}?"
    ):
        raise typer.Exit(1)
    keep = (lambda p: bool(light.search(p))) if keep_light else None
    n = freed = 0
    for backend, run_id in targets:
        a, b = c.service.artifact_store.forget(backend, run_id, keep)
        n, freed = n + a, freed + b
    typer.echo(f"removed {n} file(s), freed {freed / 1e6:.1f} MB")
