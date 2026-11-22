"""``mf`` command-line interface."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

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
    c = _c(config)
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


knowledge_app = typer.Typer(help="Team knowledge (run-independent): list, validate, import.")
app.add_typer(knowledge_app, name="knowledge")
harness_app = typer.Typer(help="Harness adaptation: briefs for a coding agent in your training project.")
app.add_typer(harness_app, name="harness")


@knowledge_app.command("list")
def knowledge_list_cmd(config: Path | None = ConfigOpt) -> None:
    """All knowledge entries with their status."""
    c = _c(config)
    entries, notes = c.knowledge.load()
    for n in notes:
        typer.secho(f"! {n}", fg="yellow")
    for e in entries:
        color = {"active": "green", "proposed": "yellow", "retired": None}[e.status]
        typer.secho(f"{e.status:<9} {e.kind:<10} {e.id:<36} {e.statement}", fg=color)
    if not entries:
        typer.echo(f"no knowledge yet — see {c.knowledge.folder}/README.md")


@knowledge_app.command("validate")
def knowledge_validate_cmd(file: Path) -> None:
    """Check a knowledge YAML file against the schema (exit 1 on problems)."""
    from model_forensic.knowledge.store import parse

    entries, errors = parse(file.read_text(encoding="utf-8"))
    for e in errors:
        typer.secho(f"✗ {e}", fg="red")
    typer.echo(f"{len(entries)} valid entr{'y' if len(entries) == 1 else 'ies'}")
    raise typer.Exit(1 if errors else 0)


@knowledge_app.command("import")
def knowledge_import_cmd(
    file: Path,
    via: str = typer.Option("harness", help="Recorded as the entries' source.via"),
    config: Path | None = ConfigOpt,
) -> None:
    """Import entries as *proposed* — they apply only after someone accepts them (Knowledge page)."""
    c = _c(config)
    added, errors = c.knowledge.import_text(file.read_text(encoding="utf-8"), via=via)
    for e in errors:
        typer.secho(f"! {e}", fg="yellow")
    typer.secho(
        f"{len(added)} entr{'y' if len(added) == 1 else 'ies'} added as proposed → review them in the "
        f"Knowledge page ({c.knowledge.main})",
        fg="green",
    )
    raise typer.Exit(1 if errors and not added else 0)


@knowledge_app.command("sources")
def knowledge_sources_cmd(config: Path | None = ConfigOpt) -> None:
    """Where knowledge and documentation are read from (inbox first), re-indexed now."""
    c = _c(config)
    reports = {r.source: r for r in c.docindex.refresh(c.knowledge.sources)}
    c.knowledge.load()
    per: dict[str, int] = {}
    for origin in c.knowledge.origins.values():
        per[origin.split(":", 1)[0]] = per.get(origin.split(":", 1)[0], 0) + 1
    for s in c.knowledge.sources:
        r = reports.get(s.name)
        root = s.resolved()
        ok = bool(root and root.is_dir())
        typer.secho(
            f"{'✓' if ok else '✗'} {s.name:<16} {'inbox ' if s.inbox else ''}{s.kind:<9} trust={s.trust:<10} "
            f"{per.get(s.name, 0):>4} entries {r.files if r else 0:>5} docs  {root or s.path}"
            + (f"  (from ${s.env})" if s.env else ""),
            fg="green" if ok else "red",
        )
        if r and r.error:
            typer.secho(f"    {r.error}", fg="yellow")
        for n in (r.notes if r else [])[:5]:
            typer.secho(f"    {n}", fg="yellow")
    typer.echo("add more: knowledge_sources: in model-forensic.yaml, or MF_KNOWLEDGE_SOURCE_<NAME>=<folder>")


@knowledge_app.command("where")
def knowledge_where_cmd(config: Path | None = ConfigOpt) -> None:
    """The inbox folder, and the instruction to give a coding agent so knowledge always lands there."""
    c = _c(config)
    inbox = c.knowledge.folder
    typer.echo(f"inbox: {inbox}")
    typer.echo(
        "\nTell your coding agent (e.g. in .github/copilot-instructions.md of each codebase):\n\n"
        f"  Write what you learn about the training code as Model Forensic knowledge YAML to\n"
        f"  {inbox}/<topic>-<yyyy-mm-dd>.yaml (schema: harness/knowledge.schema.json; status: proposed;\n"
        "  cite file:line in source.code_refs) and longer explanations as Markdown to\n"
        f"  {inbox}/docs/<codebase>/<topic>.md. Never write elsewhere.\n"
    )


docs_app = typer.Typer(help="Documentation from every knowledge source: index and search.")
app.add_typer(docs_app, name="docs")


@docs_app.command("index")
def docs_index_cmd(config: Path | None = ConfigOpt) -> None:
    """(Re)index changed documentation files of every source."""
    c = _c(config)
    for r in c.docindex.refresh(c.knowledge.sources):
        typer.echo(
            f"{r.source:<16} {r.files:>6} files  {r.changed:>5} changed  {r.removed:>4} removed  {r.seconds:>6.2f}s"
            + (f"  ! {r.error}" if r.error else "")
        )


@docs_app.command("search")
def docs_search_cmd(
    query: str,
    source: str | None = typer.Option(None, help="Only this source"),
    limit: int = typer.Option(10),
    config: Path | None = ConfigOpt,
) -> None:
    """Search the documentation (sections, best first)."""
    c = _c(config)
    c.docindex.refresh_if_stale(c.knowledge.sources)
    hits = c.docindex.search(query, source, limit)
    for h in hits:
        typer.secho(f"{h.source}:{h.path}{'#' + h.anchor if h.anchor else ''}  — {h.heading}", bold=True)
        typer.echo(f"    {h.snippet}")
    if not hits:
        typer.echo("no matches")


@harness_app.command("tasks")
def harness_tasks_cmd() -> None:
    """The available harness tasks."""
    from model_forensic.application.harness import harness_dir, tasks

    for name, text in tasks().items():
        typer.echo(f"{name:<28} {text.splitlines()[0].lstrip('# ')}")
    typer.echo(f"(from {harness_dir() / 'tasks'})")


@harness_app.command("brief")
def harness_brief_cmd(
    run: str,
    task: str | None = typer.Option(None, help="Append a task (see `mf harness tasks`)"),
    out: Path | None = typer.Option(None, help="Write to this file instead of stdout"),
    config: Path | None = ConfigOpt,
) -> None:
    """What the run shows (config, losses, metrics, datasets, knowledge) + a task, for a coding agent."""
    from model_forensic.application.harness import brief

    c = _c(config)
    c.docindex.refresh_if_stale(c.knowledge.sources)
    text = brief(c.service, c.knowledge, run, task, docindex=c.docindex)
    if out:
        out.write_text(text, encoding="utf-8")
        typer.echo(
            f"wrote {out} — give it to your coding agent inside the training project, then "
            "`mf knowledge import <answer.yaml>`"
        )
    else:
        typer.echo(text)


@harness_app.command("schema")
def harness_schema_cmd(out: Path = typer.Option(Path("harness/knowledge.schema.json"))) -> None:
    """Write the knowledge JSON schema (kept in the repo; a test checks it is current)."""
    from model_forensic.application.harness import schema_text
    from model_forensic.shared.paths import home_path

    out = home_path(out)

    out.write_text(schema_text(), encoding="utf-8")
    typer.echo(f"wrote {out}")


study_app = typer.Typer(help="Studies: hypotheses, evidence and verification for one experiment or a small group.")
app.add_typer(study_app, name="study")


@study_app.command("list")
def study_list(config: Path | None = ConfigOpt) -> None:
    """List studies."""
    for st in _c(config).studies.all():
        counts = {k: sum(1 for h in st.hypotheses if h.status.value == k) for k in ("open", "supported", "rejected")}
        typer.echo(
            f"{st.id}  {st.title}  ({len(st.runs)} runs; {counts['open']} open, {counts['supported']} supported, "
            f"{counts['rejected']} rejected)"
        )


@study_app.command("new")
def study_new(
    title: str,
    run: list[str] = typer.Option(None, "--run"),
    baseline: str | None = None,
    goal: str = "",
    config: Path | None = ConfigOpt,
) -> None:
    """Create a study: mf study new "Why no radar gain?" --run cand-radar-8f --baseline baseline-cl4f-s0"""
    st = _c(config).studies.create(title, goal, run or [], baseline)
    typer.echo(f"created {st.id} — open it at /studies/{st.id}")


@study_app.command("hypothesis")
def study_hypothesis(
    study_id: str,
    statement: str,
    change: str = typer.Option("", help="Proposed architecture change"),
    config: Path | None = ConfigOpt,
) -> None:
    """Add a hypothesis to a study."""
    st = _c(config).studies.add_hypothesis(study_id, statement, proposed_change=change)
    typer.echo(f"added {st.hypotheses[-1].id}")


@study_app.command("verify")
def study_verify(
    study_id: str, hypothesis_id: str, test_run: str | None = None, config: Path | None = ConfigOpt
) -> None:
    """Compare a hypothesis' test run against its reference run (optionally linking the test run first)."""
    c = _c(config)
    if test_run:
        c.studies.update_hypothesis(study_id, hypothesis_id, test_run=test_run)
    h, cmp, suggestion = c.studies.verify(study_id, hypothesis_id)
    typer.secho(h.statement, bold=True)
    typer.echo(suggestion)
    for d in sorted(
        (d for d in cmp.deltas if d.verdict.value in ("improved", "regressed")), key=lambda d: -abs(d.relative or 0)
    )[:12]:
        typer.secho(
            f"  {d.metric:34} {d.relative:+.1%}" if d.relative is not None else f"  {d.metric}",
            fg="green" if d.verdict.value == "improved" else "red",
        )


@study_app.command("book")
def study_book_cmd(
    study_id: str,
    ai: bool = typer.Option(False, "--ai", help="Add a grounded AI narrative (needs a configured LLM)"),
    out: Path | None = typer.Option(None, "--out", "-o", help="Copy the book to this directory"),
    config: Path | None = ConfigOpt,
) -> None:
    """Generate the study book: verdict, checks P1–P8, shared ruler, convergence, loss ledger, failures, next steps."""
    import shutil

    c = _c(config)
    html_path, summary_path = c.study_book(study_id, ai=ai, progress=lambda m: typer.echo(f"  … {m}"))
    if out:
        out.mkdir(parents=True, exist_ok=True)
        shutil.copy(html_path, out / "index.html")
        shutil.copy(summary_path, out / "study_summary.json")
        html_path = out / "index.html"
    s = json.loads(summary_path.read_text())
    typer.echo(f"verdict: {s['verdict']}")
    typer.secho(f"✓ {html_path}", fg="green")


@study_app.command("export")
def study_export(
    study_id: str, out: Path | None = typer.Option(None, "--out", "-o"), config: Path | None = ConfigOpt
) -> None:
    """Export the study journal as Markdown."""
    md = _c(config).studies.to_markdown(study_id)
    if out:
        out.write_text(md)
        typer.echo(f"wrote {out}")
    else:
        typer.echo(md)


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
    from model_forensic.infrastructure.artifacts.store import DownloadProfile

    c = _c(config)
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


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8765, config: Path | None = ConfigOpt) -> None:
    """Start the HTTP API."""
    import uvicorn

    from model_forensic.api.app import create_app
    from model_forensic.application import server_registry as reg

    for s in reg.servers():
        if int(s["port"]) == port:
            typer.secho(
                f"already running at {s['url']} (pid {s['pid']}) — open it, or stop it with: mf stop --port {port}",
                fg="yellow",
            )
            raise typer.Exit(1)
    if reg.port_in_use(host, port):
        typer.secho(f"port {port} is in use by another program — choose another: mf serve --port {port + 1}", fg="red")
        raise typer.Exit(2)
    c = _c(config)
    app_ = create_app(c)
    if c.settings.server.mode == "shared":
        if c.settings.server.auth == "none" and host not in {"127.0.0.1", "localhost"}:
            typer.secho(
                "refusing to serve a shared instance without auth on a public interface (set server.auth: header|oidc)",
                fg="red",
            )
            raise typer.Exit(2)
        app_.state.scheduler.start()
        typer.echo(f"shared mode · auth {c.settings.server.auth} · sync every {c.settings.server.sync_minutes} min")
    reg.register(host, port, c.settings.data_dir)
    import os as _os

    from model_forensic.api.app import web_dist_candidates

    shown = reg.registered_url(host, port)
    web = getattr(app_.state, "web_dist", None)
    if web is None:
        typer.secho(
            "! the web UI is not built — the page will say so. Build it: cd web && npm ci && npx vite build",
            fg="yellow",
        )
        typer.echo("  looked in: " + ", ".join(str(p) for p in web_dist_candidates()))
    else:
        typer.echo(f"web UI: {web}")
        b = getattr(app_.state, "build", None)
        if b is not None and (b.stale or (b.source_rev and not b.web_rev)):
            what = f"built from {b.web_rev}, code is {b.source_rev}" if b.web_rev else "built before version tracking"
            typer.secho(
                f"! the web UI build is older than the code ({what}) — you will see old screens. Rebuild it:\n"
                "    cd web && npm ci && npx vite build     (then reload the page)",
                fg="yellow",
            )
    if _os.environ.get("SSH_CONNECTION") and host in ("127.0.0.1", "localhost"):
        typer.secho(
            f"! you are on SSH: {shown} is this machine, not your laptop. Forward the port from your laptop:\n"
            f"    ssh -L {port}:127.0.0.1:{port} <you>@<this machine>   (or VS Code → Ports → Forward {port})\n"
            f"  then open http://127.0.0.1:{port} on the laptop.",
            fg="yellow",
        )
    typer.echo(f"Model Forensic at {shown} — stop with Ctrl+C or: mf stop")
    try:
        uvicorn.run(app_, host=host, port=port)
    finally:
        reg.unregister(port)


@app.command()
def status() -> None:
    """Running Model Forensic servers: URL, pid, uptime, memory."""
    from model_forensic.application import server_registry as reg

    found = reg.servers()
    if not found:
        typer.echo("no Model Forensic server is running (start one with: mf serve)")
        return
    for s in found:
        up = s["uptime_s"]
        mem = f"{s['memory_mb']:.0f} MB" if s.get("memory_mb") is not None else "memory unknown"
        state = "answering" if s["answering"] else "not answering yet"
        typer.echo(
            f"{s['url']}  pid {s['pid']}  up {up // 3600} h {up % 3600 // 60} min  {mem}  {state}  data {s['data_dir']}"
        )


@app.command()
def stop(
    port: int | None = typer.Option(None, "--port", help="Only the server on this port (default: every running one)"),
    force: bool = typer.Option(False, "--force", help="Kill it if it does not stop within 10 s"),
) -> None:
    """Stop running Model Forensic servers (graceful; running prefetch tasks stay resumable). Frees their memory."""
    from model_forensic.application import server_registry as reg

    done = reg.stop(port, force)
    if not done:
        typer.echo("no running Model Forensic server found" + (f" on port {port}" if port else ""))
        typer.echo(
            "started before this version? find it with: lsof -i :8765 (or ss -ltnp | grep 8765), then kill <pid>"
        )
        raise typer.Exit(1)
    for s in done:
        mem = f", freed ~{s['memory_mb']:.0f} MB" if s.get("memory_mb") and s["result"] in ("stopped", "killed") else ""
        typer.echo(f"{s['url']} (pid {s['pid']}): {s['result']}{mem}")
    if any(s["result"].startswith("still") for s in done):
        raise typer.Exit(1)


@app.command()
def backup(
    out: Path = typer.Option(..., "--out", "-o", help="Archive to write (.tar.gz)"),
    config: Path | None = ConfigOpt,
) -> None:
    """Consistent backup of the local databases (studies, findings, index) and generated books."""
    import sqlite3
    import tarfile
    import tempfile

    s = Settings.load(config)
    with tempfile.TemporaryDirectory() as tmp, tarfile.open(out, "w:gz") as tar:
        for db in sorted(s.data_dir.glob("*.sqlite")):
            snap = Path(tmp) / db.name
            with sqlite3.connect(db) as src, sqlite3.connect(snap) as dst:
                src.backup(dst)  # online, consistent snapshot
            tar.add(snap, arcname=f"data/{db.name}")
        books = s.paths()["work"]
        if books.is_dir():
            tar.add(books, arcname="data/books")
    typer.secho(f"✓ {out} — restore: stop the server, extract into the data dir", fg="green")


@app.command()
def init(
    subscription: str | None = typer.Option(None, help="Subscription id (default: the active `az` subscription)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Add every detected workspace without asking"),
    path: Path | None = typer.Option(None, help="Interface file (default config/azure.local.yaml or MF_AZURE_CONFIG)"),
) -> None:
    """Detect your Azure ML workspaces from `az login` and write the local (git-ignored) interface file."""
    from model_forensic.interface import azure

    try:
        found = azure.detect(subscription)
    except azure.InterfaceError as exc:
        typer.secho(f"✗ {exc}", fg="red")
        typer.echo("Fill config/azure.local.yaml by hand instead (template: config/azure.example.yaml).")
        raise typer.Exit(1) from None
    user = found["user"].get("name") or found["user"].get("upn") or "?"
    typer.echo(f"signed in as {user} · subscription {found['subscription_name']} ({found['subscription_id']})")
    try:
        iface = azure.load(path)
    except azure.InterfaceError:
        iface = azure.AzureInterface()
    iface.workspaces = [w for w in iface.workspaces if not w.is_placeholder]
    if not iface.defaults.me:  # `user:me` / "Mine" in the Runs table
        iface.defaults.me = [str(v) for v in found["user"].values() if v]
    if found.get("tenant_id") and not iface.defaults.tenant_id:
        iface.defaults.tenant_id = found["tenant_id"]
    have = {(w.subscription_id, w.workspace) for w in iface.workspaces}
    if not found["workspaces"]:
        typer.secho("no Azure ML workspaces visible in this subscription (try --subscription)", fg="yellow")
    for w in found["workspaces"]:
        if (w["subscription_id"], w["workspace"]) in have:
            typer.echo(f"  = {w['workspace']} (already configured)")
            continue
        if yes or typer.confirm(
            f"  add {w['workspace']}  (resource group {w['resource_group']}, {w.get('location')})?", default=True
        ):
            name = w["name"]
            while name in {x.name for x in iface.workspaces}:
                name = f"{name}-2"
            iface.workspaces.append(
                azure.AzureWorkspace(
                    name=name,
                    title=w["title"],
                    subscription_id=w["subscription_id"],
                    resource_group=w["resource_group"],
                    workspace=w["workspace"],
                    default=not iface.workspaces,
                )
            )
    if not iface.workspaces:
        typer.secho("nothing to write", fg="yellow")
        raise typer.Exit(1)
    out = azure.save(iface, path)
    typer.secho(
        f"✓ wrote {out} with {len(iface.workspaces)} workspace(s) — git-ignored; next: mf doctor --live", fg="green"
    )


@app.command("calibrate")
def calibrate_cmd(
    out: Path | None = typer.Option(None, help="Write the markdown report here (e.g. docs/calibration.md)"),
    min_score: float = typer.Option(0.0, help="Exit 1 if a default threshold falls below this precision/recall"),
    seed: int = typer.Option(0),
) -> None:
    """Score every detector on the labelled synthetic benchmark (precision, recall, threshold sweeps)."""
    from model_forensic.calibration import markdown, run_benchmark

    b = run_benchmark(seed=seed)
    md = markdown(b)
    if out:
        out.write_text(md, encoding="utf-8")
        typer.echo(f"wrote {out}")
    else:
        typer.echo(md)
    bad = [
        f"{name} {r.threshold}: precision {r.precision:.0%}, recall {r.recall:.0%}"
        for name, rows in b["results"].items()
        for r in rows
        if _is_default(r.threshold, b["defaults"]) and min(r.precision, r.recall) < min_score
    ]
    for line in bad:
        typer.secho(f"below {min_score:.0%}: {line}", fg="red")
    raise typer.Exit(1 if bad else 0)


def _is_default(threshold: str, defaults: dict[str, Any]) -> bool:
    knob, _, val = threshold.partition("=")
    return threshold == "default" or (knob in defaults and float(val) == float(defaults[knob]))


@app.command("ai-eval")
def ai_eval_cmd(
    out: Path | None = typer.Option(None, "--out", help="Write the Markdown table here (e.g. docs/ai-eval.md)"),
    min_grounded: float = typer.Option(0.0, help="Exit 1 when the grounded share of claims is below this (0..1)"),
    config: Path | None = ConfigOpt,
) -> None:
    """Ask fixed questions about the demo runs and score the answers on grounding (any configured model)."""
    from model_forensic.ai.eval_harness import markdown, run_eval, summary

    c = _c(config)
    rows = run_eval(c)
    s = summary(rows)
    model = getattr(c.llm, "model", type(c.llm).__name__)
    text = markdown(str(model), rows)
    if out:
        out.write_text(text)
        typer.echo(f"wrote {out}")
    typer.echo(
        f"{model}: {int(s['answered'])}/{int(s['questions'])} answered, {s['grounded_share']:.0%} grounded, "
        f"{int(s['bad_numbers'])} hallucinated numbers"
    )
    raise typer.Exit(1 if s["grounded_share"] < min_grounded else 0)


@app.command("check")
def check_cmd(
    run: str | None = typer.Argument(None, help="Run id (default: the newest completed run)"),
    as_json: bool = typer.Option(False, "--json"),
    config: Path | None = ConfigOpt,
) -> None:
    """Check my run: at most five plain-language cards — what is wrong, why it matters, what to do next."""
    from model_forensic.application.check_run import check_run

    res = check_run(_c(config).service, run)
    if as_json:
        typer.echo(json.dumps(res, indent=2, default=str))
        return
    color = {"red": "red", "amber": "yellow", "green": "green"}
    dot = {"red": "●", "amber": "●", "green": "●"}
    r = res["run"]
    typer.secho(f"{dot[res['overall']]} {res['summary']}", fg=color[res["overall"]], bold=True)
    typer.echo(f"  {r['name']} ({r['ref']})" + (f" · baseline {res['baseline']}" if res["baseline"] else ""))
    for c in res["cards"]:
        typer.echo("")
        typer.secho(f"{dot[c['light']]} {c['title']}", fg=color[c["light"]], bold=True)
        typer.echo(f"  {c['meaning']}")
        if c["why"]:
            typer.echo(f"  Why it matters: {c['why']}")
        typer.secho(f"  Next: {c['todo']}", fg="cyan")
    if res["more"]:
        typer.echo(f"\n  … {res['more']} more finding(s): mf analyze {r['ref']}")
    terms = res.get("glossary") or {}
    if terms:
        typer.echo("\nGlossary")
        for t, d in terms.items():
            typer.echo(f"  {t}: {d}")
    raise typer.Exit(1 if res["overall"] == "red" else 0)


@app.command("gate")
def gate_cmd(
    config_file: Path,
    against: str | None = typer.Option(None, "--against", help="Reference run: its ruler and logged metric names"),
    metrics: Path | None = typer.Option(None, "--metrics", help="Text file with the metric names the job will log"),
    as_json: bool = typer.Option(False, "--json"),
    config: Path | None = ConfigOpt,
) -> None:
    """Launch-time checks on a training config before submitting it (exit 1 on FAIL)."""
    from model_forensic.application.gate import gate
    from model_forensic.shared import yamlsafe

    cfg = yamlsafe.load(config_file.read_text())
    if not isinstance(cfg, dict):
        typer.secho("not a YAML mapping", fg="red")
        raise typer.Exit(2)
    logged: list[str] = []
    ref_cfg = None
    if metrics:
        logged = [ln.strip() for ln in metrics.read_text().splitlines() if ln.strip()]
    if against:
        c = _c(config)
        logged += [s.provenance.get("raw_name") or str(s.key) for s in c.service.series(against)]
        logged += [str(s.key) for s in c.service.series(against)]
        ref_cfg = {"ref": c.service.flat_config(against)}
    res = gate(cfg, logged or None, ref_cfg)
    if as_json:
        typer.echo(json.dumps([r.__dict__ for r in res], indent=2))
    else:
        color = {"FAIL": "red", "WARN": "yellow", "PASS": "green", "SKIP": "white"}
        for r in res:
            typer.secho(f"  {r.status:<4} {r.id} {r.title}: {r.detail}", fg=color[r.status])
    raise typer.Exit(1 if any(r.status == "FAIL" for r in res) else 0)


@app.command("noise")
def noise_cmd(
    run: list[str] = typer.Option(..., "--run", help="Replicate runs (repeatable)"),
    candidate: str | None = typer.Option(None, help="Compare this run against the replicates' noise floor"),
    config: Path | None = ConfigOpt,
) -> None:
    """Noise floor across replicate runs (seeds) and, with --candidate, effect sizes against it."""
    from model_forensic.application.noise import effects, noise_floor

    c = _c(config)
    if candidate:
        for e in effects(c.service, run, candidate)[:40]:
            z = f"z {e.z:+.1f}" if e.z is not None else "z  n/a"
            typer.echo(
                f"  {'*' if e.beyond_noise else ' '} {e.metric:<60} {e.baseline_mean:.4g}±{e.baseline_std:.2g} → "
                f"{e.candidate:.4g}  {z}"
            )
        return
    for r in noise_floor(c.service, run)[:60]:
        typer.echo(f"  {r.metric:<60} mean {r.mean:.4g}  std {r.std:.2g}  cv {r.cv:.1%}  n={r.n}")


@app.command("plugins")
def plugins_cmd(config: Path | None = ConfigOpt) -> None:
    """List analyses: built-in and your own (config/plugins/*.py)."""
    c = _c(config)
    for p in c.service.analyses.all():
        own = " (local)" if p.name in c.service.local_plugins else ""
        typer.echo(f"  {p.name:<22} {p.category:<12} {p.title}{own}")
    for n in c.settings.notices:
        if n.startswith("Plugin:"):
            typer.secho(f"  ! {n}", fg="yellow")


setup_app = typer.Typer(help="Target-setup protocol support (setup-agent/): the compatibility report.")
app.add_typer(setup_app, name="setup")


@setup_app.command("check")
def setup_check_cmd(
    live: bool = typer.Option(False, "--live", help="Also run the adapter contract against every project workspace"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable report"),
    no_record: bool = typer.Option(False, "--no-record", help="Do not write config/setup/compatibility.local.json"),
    config: Path | None = ConfigOpt,
) -> None:
    """Every precondition, satisfied / missing / incompatible / unknown / optional, with the fix and where it is
    allowed. Changes nothing (except recording the report locally). Exit 1 if something required is missing."""
    from model_forensic.application.setup_check import check, record, text

    c = _c(config)
    rep = check(c.settings, c.service, live=live)
    if not no_record:
        path = record(rep)
    if as_json:
        typer.echo(json.dumps(rep.to_dict(), indent=2, default=str))
    else:
        typer.echo(text(rep))
        if not no_record:
            typer.echo(f"recorded: {path}")
    raise typer.Exit(1 if rep.blocking else 0)


projects_app = typer.Typer(help="Project profiles: many target projects through one core (config/projects/).")
app.add_typer(projects_app, name="projects")


@projects_app.command("list")
def projects_list_cmd(config: Path | None = ConfigOpt) -> None:
    """Loaded project profiles, their workspaces and naming, and any problems."""
    c = _c(config)
    for n in c.settings.notices:
        if n.startswith("Project"):
            typer.secho(f"! {n}", fg="yellow")
    for p in c.service.projects:
        miss = [b for b in p.backends if b not in c.service.sources]
        typer.secho(f"{p.id:<20} {p.title or ''}", bold=True)
        typer.echo(
            f"    workspaces: {', '.join(p.backends) or '—'}"
            + (f"  (not available: {', '.join(miss)})" if miss else "")
        )
        typer.echo(
            f"    experiments: {', '.join(p.experiments) or 'all'} · metric mappings: {len(p.metrics.aliases) + len(p.metrics.rules)}"
        )
    if not c.service.projects:
        typer.echo(f"no project profiles — copy one of {c.settings.projects_dir}/*.example.yaml to <id>.local.yaml")


@projects_app.command("check")
def projects_check_cmd(
    project: str,
    as_json: bool = typer.Option(False, "--json"),
    max_runs: int = typer.Option(3, help="Runs read per workspace"),
    config: Path | None = ConfigOpt,
) -> None:
    """Run the adapter contract against the project's workspaces (read-only); exit 1 if a required check fails."""
    from model_forensic.projects.contract import check_run_source, summary

    c = _c(config)
    p = next((x for x in c.service.projects if x.id == project), None)
    if p is None:
        typer.secho(
            f"unknown project {project!r}; loaded: {', '.join(x.id for x in c.service.projects) or 'none'}", fg="red"
        )
        raise typer.Exit(2)
    report: dict[str, Any] = {"project": p.id, "workspaces": {}}
    ok = True
    for b in p.backends:
        src = c.service.sources.get(b)
        if src is None:
            report["workspaces"][b] = {"ok": False, "error": c.backend_errors.get(b, "not configured")}
            ok = False
            continue
        s = summary(check_run_source(src, max_runs))
        report["workspaces"][b] = s
        ok = ok and bool(s["ok"])
    report["ok"] = ok
    if as_json:
        typer.echo(json.dumps(report, indent=2))
    else:
        for b, s in report["workspaces"].items():
            typer.secho(f"{b}: {'ok' if s.get('ok') else 'FAILED'}", fg="green" if s.get("ok") else "red", bold=True)
            if "error" in s:
                typer.echo(f"    {s['error']}")
            for r in s.get("results", []):
                mark = "✓" if r["ok"] else ("✗" if r["required"] else "!")
                typer.echo(f"  {mark} {r['id']} {r['title']} — {r['detail']}")
    raise typer.Exit(0 if ok else 1)


@app.command("paths")
def paths_cmd(as_json: bool = typer.Option(False, "--json")) -> None:
    """Where relative paths resolve: the project root (PROJECT_PATH) and Model Forensic's own folder (MF_HOME)."""
    from model_forensic.shared.paths import describe

    d = describe()
    if as_json:
        typer.echo(json.dumps(d, indent=2))
        return
    typer.echo(f"project root  {d['project_root']}   ({d['project_root_from']})")
    typer.echo("              ← your data: mf inspect <folder>, local run folders, knowledge sources")
    typer.echo(f"MF home       {d['mf_home']}   ({d['mf_home_from']})")
    typer.echo("              ← Model Forensic's files: config/, .mf/, harness/, model-forensic.yaml")
    if d["project_root_from"] == "current directory":
        typer.secho(
            "PROJECT_PATH is not set — relative paths depend on where you run mf. See README → Setup.", fg="yellow"
        )


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

    c = _c(config)
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


@app.command()
def checks(
    config: Path | None = ConfigOpt,
    as_json: bool = typer.Option(False, "--json", help="Machine-readable (feed it to the repair prompt)"),
) -> None:
    """Configuration checks — each with severity, current vs expected, the fix, the repair prompt and how to validate."""
    from model_forensic.application.config_checks import run, summary
    from model_forensic.application.test_space import TestSpace
    from model_forensic.shared.paths import mf_home

    c = _c(config)
    c.test_space = TestSpace(
        c.service, c.tasks, c.docs, mf_home(), Path(__file__).parents[1] / "defaults" / "tasks.yaml"
    )
    rep = summary(run(c))
    if as_json:
        typer.echo(json.dumps(rep, indent=2))
        raise typer.Exit(0 if rep["ok"] else 1)
    mark = {"pass": ("✓", "green"), "fail": ("✗", "red"), "unknown": ("?", "yellow"), "skip": ("-", None)}
    for ch in rep["checks"]:
        m, col = mark[ch["status"]]
        typer.secho(
            f"{m} {ch['id']:<11} [{ch['severity']}] {ch['title']}: {ch['current']}",
            fg=col if ch["status"] == "fail" else None,
        )
        if ch["status"] == "fail":
            typer.echo(
                f"    expected: {ch['expected']}\n    fix: {ch['remediation']}\n    prompt: {ch['prompt_path']}\n    validate: {ch['validation']}"
            )
    raise typer.Exit(0 if rep["ok"] else 1)


@app.command()
def doctor(
    live: bool = typer.Option(
        False, "--live", help="Call each Azure workspace: credential, listing speed, metrics, files"
    ),
    run: str | None = typer.Option(None, help="Job to probe with --live (default: newest completed job)"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable --live results"),
    bundle: Path | None = typer.Option(
        None, "--bundle", help="With --live: write a redacted support bundle (JSON) to share or feed to a coding LLM"
    ),
    config: Path | None = ConfigOpt,
) -> None:
    """Validate environment and configuration (add --live to test real Azure access and speed)."""
    import os

    s = Settings.load(config)
    if live:
        _doctor_live(s, run, as_json, bundle)
        return
    ok = True
    typer.echo(f"python {sys.version.split()[0]}  data_dir {s.data_dir.resolve()}")
    for n in s.notices:
        typer.secho(f"  ! {n}", fg="yellow")
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


def _doctor_live(s: Settings, run: str | None, as_json: bool, bundle: Path | None = None) -> None:
    import logging
    import platform

    from model_forensic import __version__
    from model_forensic.infrastructure.connectors import create_connector
    from model_forensic.infrastructure.connectors.azureml.diagnose import classify, preflight, redact
    from model_forensic.infrastructure.connectors.azureml.live import BUDGETS_S, run_checks

    logging.getLogger("azure").setLevel(logging.ERROR)  # the SDK logs every failed credential attempt
    azure = [b for b in s.backends if b.kind == "azureml"]
    pre = preflight([{"name": b.name, **b.options} for b in azure])
    report: dict[str, list[dict[str, Any]]] = {}
    ok = all(p.ok for p in pre) and bool(azure)
    for b in azure:
        try:
            src = create_connector(b.kind, {"name": b.name, **b.options})
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            report[b.name] = [
                {"name": "connect", "ok": False, "detail": detail, "diagnosis": classify(detail).to_dict()}
            ]
            ok = False
            continue
        checks = run_checks(src, run)
        ok = ok and all(c.ok for c in checks)
        report[b.name] = [
            {
                "name": c.name,
                "ok": c.ok,
                "seconds": c.seconds,
                "slow": c.slow,
                "detail": c.detail,
                **({"data": c.data} if c.data else {}),
                **({"diagnosis": classify(c.detail).to_dict()} if not c.ok else {}),
            }
            for c in checks
        ]
    doc = {
        "version": __version__,
        "python": platform.python_version(),
        "platform": platform.platform(terse=True),
        "ok": ok,
        "preflight": [p.__dict__ for p in pre],
        "workspaces": report,
    }
    if bundle is not None:
        bundle.write_text(redact(json.dumps(doc, indent=2, default=str)), encoding="utf-8")
    if as_json:
        typer.echo(json.dumps(doc, indent=2, default=str))
    else:
        typer.echo("preflight")
        for p in pre:
            typer.secho(f"  {'✓' if p.ok else '✗'} {p.name:<28} {p.detail}", fg="green" if p.ok else "red")
            if not p.ok and p.fix:
                typer.secho(f"      fix: {p.fix}", fg="yellow")
        if not azure:
            typer.secho("no Azure workspaces configured — run `mf init` or fill config/azure.local.yaml", fg="yellow")
        for ws, checks_ in report.items():
            typer.echo(f"workspace {ws}")
            for c in checks_:
                mark, color = (
                    ("✓", "green") if c["ok"] and not c.get("slow") else (("!", "yellow") if c["ok"] else ("✗", "red"))
                )
                t = f"{c['seconds']:.2f}s" if c.get("seconds") is not None else ""
                budget = f" (budget {BUDGETS_S[str(c['name'])]:.0f}s)" if c.get("slow") else ""
                typer.secho(f"  {mark} {c['name']:<15} {t:>7}{budget}  {c['detail']}", fg=color)
                if "diagnosis" in c:
                    d = c["diagnosis"]
                    typer.secho(f"      cause: {d['cause']} — {d['summary']}", fg="yellow")
                    for fx in d["fixes"]:
                        typer.secho(f"      fix:   {fx}", fg="yellow")
        if bundle is not None:
            typer.echo(f"support bundle (ids and e-mails redacted): {bundle}")
        if not ok:
            typer.echo("more help: docs/troubleshooting.md")
    raise typer.Exit(0 if ok else 1)


def main() -> None:
    try:
        app()
    except ForensicError as exc:
        typer.secho(f"error [{exc.code}]: {exc.message}", fg="red", err=True)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
