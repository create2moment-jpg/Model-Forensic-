"""``mf`` command-line interface."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from model_forensic.container import Container, build
from model_forensic.settings import Settings
from model_forensic.shared.errors import ForensicError
from model_forensic.shared.logging import configure_logging

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Model Forensic: ML experiment forensics.")
_ICON = {"critical": "[CRIT]", "high": "[HIGH]", "medium": "[MED ]", "low": "[LOW ]", "info": "[INFO]"}


def _c(config: Path | None = None) -> Container:
    s = Settings.load(config)
    configure_logging(s.log_level, json_output=False)
    return build(s)


ConfigOpt = typer.Option(None, "--config", "-c", help="Path to model-forensic.yaml")


@app.command()
def demo(config: Path | None = ConfigOpt) -> None:
    """Generate deterministic demo runs and index them."""
    from model_forensic.infrastructure.demo.synthetic import generate_demo

    s = Settings.load(config)
    root = Path(s.backends[0].options.get("root", s.data_dir / "demo-runs"))
    ids = generate_demo(root)
    n = build(s).service.sync()
    typer.echo(f"wrote {len(ids)} demo runs to {root}; indexed {n}")
    typer.echo("try:  mf search 'failed training jobs from last week'")
    typer.echo("      mf compare baseline-cl4f-s0 cand-radar-8f")


@app.command()
def sync(backend: str | None = None, config: Path | None = ConfigOpt) -> None:
    """Index run metadata from configured backends."""
    typer.echo(f"indexed {_c(config).service.sync(backend)} runs")


@app.command()
def search(
    query: str = typer.Argument(""),
    limit: int = 20,
    as_json: bool = typer.Option(False, "--json"),
    config: Path | None = ConfigOpt,
) -> None:
    """Search runs (DSL or natural language)."""
    res = _c(config).service.search(query, limit)
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
    o = _c(config).service.overview(run, baseline)
    r = o.run
    typer.secho(f"{r.ref}  [{r.status.value}]  {r.display_name or ''}", bold=True)
    typer.echo(f"  experiment {r.experiment}  user {r.user}  commit {r.git.commit} ({r.git.branch})")
    typer.echo(
        f"  compute {r.compute.target} {r.compute.gpu_count}x{r.compute.gpu_type}  duration {(r.duration_s or 0) / 3600:.2f} h"
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
def compare(baseline: str, candidate: str, config: Path | None = ConfigOpt) -> None:
    """Compare a candidate run against a baseline."""
    svc = _c(config).service
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
    results = _c(config).service.analyze(run, baseline, analysis or None)
    for r in results:
        if r.skipped_reason:
            typer.secho(f"- {r.analysis}: skipped ({r.skipped_reason})", dim=True)
            continue
        typer.secho(f"- {r.analysis}: {len(r.findings)} finding(s)", bold=True)
        for n in r.notes:
            typer.echo(f"    note: {n}")
        for f in sorted(r.findings, key=lambda f: -f.severity.rank):
            typer.echo(f"  {_ICON[f.severity.value]} {f.title}  ({f.label.value}, conf={f.confidence.value})")
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

    c = _c(config)
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
    lib = _c(config).prompts
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

    c = _c(config)
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
def download(run: str, profile: str = "fast_debug", config: Path | None = ConfigOpt) -> None:
    """Download artifacts with a profile: fast_debug | evaluation_only | checkpoint_only | full_forensic."""
    from model_forensic.infrastructure.artifacts.store import DownloadProfile

    files, prog = _c(config).service.download(run, DownloadProfile(profile))
    typer.echo(f"{len(files)} file(s), {prog.cached_files} from cache, {prog.done_bytes / 1e6:.1f} MB fetched")
    for f in files:
        typer.echo(f"  {f.local_path}")
    for path, err in prog.failed:
        typer.secho(f"  FAILED {path}: {err}", fg="red")


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8765, config: Path | None = ConfigOpt) -> None:
    """Start the HTTP API."""
    import uvicorn

    from model_forensic.api.app import create_app

    uvicorn.run(create_app(_c(config)), host=host, port=port)


@app.command()
def doctor(config: Path | None = ConfigOpt) -> None:
    """Validate environment and configuration."""
    import os

    s = Settings.load(config)
    ok = True
    typer.echo(f"python {sys.version.split()[0]}  data_dir {s.data_dir.resolve()}")
    for b in s.backends:
        try:
            build(Settings(**{**s.model_dump(), "backends": [b]}))
            typer.secho(f"  ✓ backend {b.name} ({b.kind})", fg="green")
        except Exception as exc:
            ok = False
            typer.secho(f"  ✗ backend {b.name} ({b.kind}): {exc}", fg="red")
    if s.llm.enabled:
        has_key = bool(os.environ.get(s.llm.api_key_env))
        typer.secho(
            f"  {'✓' if has_key else '!'} LLM {s.llm.model} @ {s.llm.base_url} key env {s.llm.api_key_env} "
            f"{'set' if has_key else 'NOT set (ok for local servers)'}",
            fg="green" if has_key else "yellow",
        )
    else:
        typer.echo("  - LLM disabled: AI features use the offline heuristic interpreter")
    raise typer.Exit(0 if ok else 1)


def main() -> None:
    try:
        app()
    except ForensicError as exc:
        typer.secho(f"error [{exc.code}]: {exc.message}", fg="red", err=True)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
