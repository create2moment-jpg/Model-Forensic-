"""``mf`` commands — Run checks and AI quality: calibrate (AI), ai-eval, check, gate, noise, plugins."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from model_forensic.cli import base
from model_forensic.cli.base import ConfigOpt, app


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
        if is_default_threshold(r.threshold, b["defaults"]) and min(r.precision, r.recall) < min_score
    ]
    for line in bad:
        typer.secho(f"below {min_score:.0%}: {line}", fg="red")
    raise typer.Exit(1 if bad else 0)


def is_default_threshold(threshold: str, defaults: dict[str, Any]) -> bool:
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

    c = base.make_container(config)
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

    res = check_run(base.make_container(config).service, run)
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
        c = base.make_container(config)
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

    c = base.make_container(config)
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
    c = base.make_container(config)
    for p in c.service.analyses.all():
        own = " (local)" if p.name in c.service.local_plugins else ""
        typer.echo(f"  {p.name:<22} {p.category:<12} {p.title}{own}")
    for n in c.settings.notices:
        if n.startswith("Plugin:"):
            typer.secho(f"  ! {n}", fg="yellow")
