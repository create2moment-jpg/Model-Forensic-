"""``mf`` commands — Loss and training analysis: thresholds, calibrate …, dossier, expectations, drift, questions, facets."""

from __future__ import annotations

from pathlib import Path

import typer

from model_forensic.cli import base
from model_forensic.cli.base import ConfigOpt, app

calibrate_app = typer.Typer(help="Calibrate the health questions on real runs: template → verdicts → proposal → apply.")
app.add_typer(calibrate_app, name="calibrate")


@calibrate_app.command("template")
def calibrate_template_cmd(
    runs: str = typer.Option(..., "--runs", help="comma-separated run ids (10–20 real runs)"),
    stage: str | None = typer.Option(None, "--stage"),
    reviewer: str = typer.Option("", "--reviewer"),
    out: Path | None = typer.Option(None, "--out", help="default: <inbox>/calibration/questions-<date>.yaml"),
    config: Path | None = ConfigOpt,
) -> None:
    """Every problem the questions raise on these runs, with what was measured — only the verdicts are left to fill."""
    from datetime import date as _date

    from model_forensic.application import question_calibration as qc

    c = base.make_container(config)
    text = qc.template(c.service, [r.strip() for r in runs.split(",") if r.strip()], stage, reviewer)
    path = out or c.knowledge.folder / "calibration" / f"questions-{_date.today().isoformat()}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    n = text.count("\n- run:")
    typer.echo(f"wrote {path} ({n} alarms to judge) — fill `verdict` / `why`, then: mf calibrate check {path}")


@calibrate_app.command("check")
def calibrate_check_cmd(file: Path) -> None:
    """Is the calibration file complete (every verdict, a reason for every false / unsure)? Exit 1 with what is missing."""
    from model_forensic.application import question_calibration as qc

    errors = qc.check(file)
    for e in errors:
        typer.secho(f"✗ {e}", fg="red")
    if errors:
        raise typer.Exit(1)
    typer.secho(f"✓ {file} is complete", fg="green")


@calibrate_app.command("propose")
def calibrate_propose_cmd(
    files: list[Path],
    out: Path | None = typer.Option(None, "--out", help="default: <inbox>/calibration/proposal-<date>.yaml"),
    config: Path | None = ConfigOpt,
) -> None:
    """Thresholds that separate the false alarms from the real problems (or why none can) — a proposal to review."""
    from datetime import date as _date

    from model_forensic.application import question_calibration as qc

    c = base.make_container(config)
    bad = [f"{f}: {e}" for f in files for e in qc.check(f)]
    if bad:
        for e in bad:
            typer.secho(f"✗ {e}", fg="red")
        raise typer.Exit(1)
    p = qc.propose(files, c.service)
    for r in p["rows"]:
        new = r["proposed"] if r["proposed"] is not None else "keep"
        typer.echo(f"{r['param']:<40} {r['current']:<8g} → {new!s:<8} {r['reason']}")
    path = out or c.knowledge.folder / "calibration" / f"proposal-{_date.today().isoformat()}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(qc.proposal_yaml(p), encoding="utf-8")
    typer.echo(f"wrote {path} — review it, then: mf calibrate apply {path}")


@calibrate_app.command("apply")
def calibrate_apply_cmd(
    proposal: Path,
    yes: bool = typer.Option(False, "--yes", help="do not ask"),
) -> None:
    """Accept a proposal: merge its thresholds into config/team.local.yaml → analysis_params (a .bak copy is kept)."""
    from model_forensic.application import question_calibration as qc
    from model_forensic.interface import team

    target = team.path()
    if not yes and not typer.confirm(f"Merge the thresholds of {proposal} into {target}?"):
        raise typer.Exit(1)
    changed = qc.apply(proposal, target)
    for section, values in changed.items():
        for k, v in values.items():
            typer.echo(f"{section}.{k} = {v}")
    typer.echo(f"updated {target} — restart the server (mf stop; mf serve) to use the new thresholds")


@app.command("thresholds")
def thresholds_cmd(
    section: str | None = typer.Option(None, help="loss_questions | loss_drift | internals | expectations"),
    as_json: bool = typer.Option(False, "--json"),
    config: Path | None = ConfigOpt,
) -> None:
    """The thresholds of the analyses: value in force, default, source (default / team), meaning."""
    import json as _json

    from model_forensic.analysis import thresholds as th

    base.make_container(config)  # applies the team's overrides
    rows = [r for r in th.table() if not section or r["section"] == section]
    if as_json:
        typer.echo(_json.dumps(rows, indent=2))
        return
    for r in rows:
        mark = "*" if r["source"] == "team" else " "
        typer.echo(
            f"{mark} {r['section']}.{r['key']:<26} {r['value']:<8g} (default {r['default']:g}, alarm {r['alarm_when']}) {r['help']}"
        )
    typer.echo("* = set in config/team.local.yaml → analysis_params")


@app.command("dossier")
def dossier_cmd(
    run: str = typer.Option(..., "--run"),
    baseline: str | None = typer.Option(None, "--baseline"),
    stage: str | None = typer.Option(None, "--stage", help="Pipeline stage (default: each metric's last stage)"),
    detail: str = typer.Option("standard", "--detail", help="brief | standard | full"),
    out: Path | None = typer.Option(None, "--out", help="file to write (default: <inbox>/dossiers/<run>-<time>.md)"),
    stdout: bool = typer.Option(False, "--stdout", help="print instead of writing a file"),
    no_prompt: bool = typer.Option(False, "--no-prompt", help="leave out the instructions for the reader"),
    config: Path | None = ConfigOpt,
) -> None:
    """Analysis dossier: everything Model Forensic found about a run, for a local LLM to interpret."""
    from model_forensic.application import dossier as ds

    c = base.make_container(config)
    try:
        text = ds.build(c.service, c.knowledge, c.docs, run, baseline, stage, detail, with_prompt=not no_prompt)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e
    if stdout:
        typer.echo(text)
        return
    path = ds.save(text, c.knowledge.folder, run, out)
    typer.echo(f"wrote {path} ({len(text) // 1000} kB) — give it to your local LLM (ideally inside the training repo)")


@app.command("expectations")
def expectations_cmd(
    run: str | None = typer.Option(None, "--run", help="check this run against its expectations file"),
    check_file: Path | None = typer.Option(None, "--check", help="validate an expectations file (exit 1 on errors)"),
    stage: str | None = typer.Option(
        None, "--stage", help="Pipeline stage to check (default: each metric's last stage)"
    ),
    as_json: bool = typer.Option(False, "--json"),
    config: Path | None = ConfigOpt,
) -> None:
    """Expected vs actual losses: the codebase's expectations (written by a local LLM) checked against a run."""
    import json as _json

    import yaml

    from model_forensic.application import expectations as ex

    if check_file is not None:
        try:
            e = ex.Expectations.model_validate(yaml.safe_load(check_file.read_text(encoding="utf-8")) or {})
        except Exception as err:
            typer.secho(f"✗ {err}", fg="red")
            raise typer.Exit(1) from err
        typer.secho(f"✓ {check_file}: {len(e.terms)} terms, {len(e.config)} config rules", fg="green")
        return
    if not run:
        raise typer.BadParameter("give --run <id> or --check <file>")

    c = base.make_container(config)
    out = ex.evaluate(
        c.service, c.knowledge.folder, run, series={str(s.key): s for s in c.service.stage_series(run, stage)}
    )
    if as_json:
        typer.echo(_json.dumps(out, indent=2, default=str))
        return
    if not out["found"]:
        typer.echo(
            f"no expectations for {run} yet.\n  1. {out['brief_command']}\n  2. give brief.md to the coding "
            f"agent in the training repo\n  3. it writes the YAML to {out['where']}/"
        )
        return
    typer.echo(f"{out['file']}  ({out['counts']['bad']} problems, {out['counts']['warn']} to look at)")
    mark = {"bad": ("✗", "red"), "warn": ("!", "yellow"), "ok": ("✓", "green")}
    for ch in out["checks"]:
        sym, color = mark.get(ch["status"], ("·", None))
        typer.secho(f"{sym} [{ch['hypothesis']}] {ch['text']}", fg=color)


@app.command("drift")
def drift_cmd(
    baseline: str = typer.Option(..., "--baseline"),
    candidate: str = typer.Option(..., "--candidate"),
    top: int = typer.Option(15, "--top", help="rows to print"),
    as_json: bool = typer.Option(False, "--json"),
    config: Path | None = ConfigOpt,
) -> None:
    """Loss drift: how every shared loss progresses in the candidate vs the baseline, and what that says."""
    import json as _json

    from model_forensic.analysis.loss_drift import loss_drift

    svc = base.make_container(config).service
    b, c = svc.ref(baseline), svc.ref(candidate)
    out = loss_drift(
        {str(s.key): s for s in svc.series(b)},
        {str(s.key): s for s in svc.series(c)},
        ref_lr=svc.lr_series(b),
        cand_lr=svc.lr_series(c),
        changes=tuple(svc.diff_configs(b, c).changes),
    )
    if as_json:
        typer.echo(_json.dumps(out, indent=2, default=str))
        return
    typer.secho(out["summary"], bold=True)
    for r in out["readings"]:
        typer.echo(f"  • {r}")
    typer.echo("")
    for r in out["rows"][:top]:
        at = f"{r['diverge_fraction']:.0%}" if r["diverge_fraction"] is not None else "—"
        gap = f"{r['final_gap']:+.1%}" if r["final_gap"] is not None else ""
        color = {"behind": "red", "crossed": "yellow", "ahead": "green", "closed": "cyan"}.get(r["outcome"])
        typer.secho(f"{r['outcome']:<8} at {at:>4}  end {gap:>7}  {r['key']}", fg=color)
        for n in r["shape_notes"]:
            typer.echo(f"{'':20}{n}")


@app.command("dynamics")
def dynamics_cmd(
    run: str = typer.Option(..., "--run", help="The run (<workspace>:<run id>)"),
    baseline: str | None = typer.Option(None, "--baseline", help="Compare on one LR schedule with this run"),
    stage: str | None = typer.Option(None, "--stage", help="Read one pipeline stage (default: each metric's last)"),
    as_json: bool = typer.Option(False, "--json"),
    config: Path | None = ConfigOpt,
) -> None:
    """Loss dynamics: what the LR schedule did vs what the model learned, and how fast each loss term trains."""
    import json as _json

    from model_forensic.analysis.loss_dynamics import report

    svc = base.make_container(config).service
    r = svc.ref(run)
    cur = {str(s.key): s for s in svc.stage_series(r, stage)}
    b_series, b_lr = None, None
    if baseline:
        b = svc.ref(baseline)
        b_stage = stage if stage and stage in [n for n, _ in svc.stages(b)] else None
        b_series, b_lr = {str(s.key): s for s in svc.stage_series(b, b_stage)}, svc.lr_series(b, b_stage)
    out = report(cur, svc.lr_series(r, stage), b_series, b_lr)
    if as_json:
        typer.echo(_json.dumps(out, indent=2, default=str))
        return
    for x in out["readings"]:
        typer.echo(f"• {x}")
    if out["fits"]:
        typer.secho("\nWhat the LR schedule did (Tissue et al. 2024: L0 + A·S1^-α − C·S2)", bold=True)
        for f in out["fits"]:
            typer.echo(f"  {f['sentence']}")
    if out["effects"]:
        typer.secho("\nAgainst the baseline, on one schedule", bold=True)
        for e in out["effects"]:
            color = {"mostly_schedule": "yellow", "masked": "yellow", "holds": "green"}.get(e["verdict"])
            typer.secho(f"  {e['sentence']}", fg=color)
    if out["rates"]:
        typer.secho("\nTraining rate per loss (r = L(t)/L(0) over its group's mean; > 1 slower)", bold=True)
        for x in sorted(out["rates"], key=lambda x: -x["rate_end"]):
            color = {"lagging": "yellow", "not_learning": "red", "leading": "cyan"}.get(x["status"])
            bnote = f"  (baseline {x['base_rate_end']:.2f})" if x["base_rate_end"] is not None else ""
            typer.secho(f"  r {x['rate_end']:.2f}  {x['status']:<12} {x['key']}  [{x['group']}]{bnote}", fg=color)


@app.command("questions")
def questions_cmd(
    run: str = typer.Option(..., "--run", help="The run (<workspace>:<run id>)"),
    baseline: str | None = typer.Option(None, "--baseline", help="Also compare against this run"),
    only_problems: bool = typer.Option(False, "--problems", help="Only questions answered bad / warn"),
    stage: str | None = typer.Option(
        None, "--stage", help="Pipeline stage to analyse (default: each metric's last stage)"
    ),
    as_json: bool = typer.Option(False, "--json"),
    config: Path | None = ConfigOpt,
) -> None:
    """Training health questions for a run: is the loss diverging, the layer refinement, a task muted, … and what to try."""
    import json as _json

    from model_forensic.analysis.loss_questions import ask
    from model_forensic.application.stages import stage_view

    svc = base.make_container(config).service
    base_series = {str(s.key): s for s in stage_view(svc.series(baseline))} if baseline else None
    answers = ask({str(s.key): s for s in svc.stage_series(run, stage)}, base_series)
    if only_problems:
        answers = [a for a in answers if a.status in ("bad", "warn")]
    if as_json:
        typer.echo(_json.dumps([a.view() for a in answers], indent=2))
        return
    mark = {"bad": ("✗", "red"), "warn": ("!", "yellow"), "ok": ("✓", "green"), "na": ("·", None)}
    for a in answers:
        sym, color = mark.get(a.status, ("?", None))
        typer.secho(f"{sym} {a.question}", fg=color, bold=a.status == "bad")
        typer.echo(f"    {a.answer}")
        if a.status in ("bad", "warn") and a.ideas:
            typer.echo("    try: " + " · ".join(a.ideas[:3]))


@app.command("facets")
def facets_cmd(
    run: str | None = typer.Option(None, "--run", help="Show what this run's metric names carry"),
    brief: bool = typer.Option(False, "--brief", help="Print a prompt for a local LLM (needs --run)"),
    check: Path | None = typer.Option(None, "--check", help="Validate a facets file (exit 1 on problems)"),
    as_json: bool = typer.Option(False, "--json"),
    config: Path | None = ConfigOpt,
) -> None:
    """Filter facets (class, modality, distance, …): defaults + config/facets.local.yaml, and a brief to tune them."""
    import json as _json

    from model_forensic.application import facets as fc

    if check is not None:
        problems = fc.validate(check)
        for e in problems:
            typer.secho(f"✗ {e}", fg="red")
        if not problems:
            typer.secho(f"✓ {check} is a valid facets file", fg="green")
        raise typer.Exit(1 if problems else 0)
    cfg = fc.load()
    names = [str(s.key) for s in base.make_container(config).service.series(run)] if run else []
    if brief:
        if not run:
            raise typer.BadParameter("--brief needs --run <run id>")
        from model_forensic.application.harness import harness_dir

        prompt = harness_dir().parent / "setup-agent" / "prompts" / "filter_facets.md"
        typer.echo(fc.brief(names, cfg, prompt.read_text(encoding="utf-8") if prompt.is_file() else ""))
        return
    out: dict[str, object] = cfg.model_dump()
    if run:
        out["observed"] = fc.observed(names, cfg)
    if as_json:
        typer.echo(_json.dumps(out, indent=2))
        return
    if cfg.notice:
        typer.secho(f"! {cfg.notice}", fg="yellow")
    typer.echo(f"read from: {', '.join(cfg.sources)}")
    for f in cfg.order + [f for f in cfg.facets if f not in cfg.order]:
        spec = cfg.facets.get(f)
        if spec:
            typer.echo(
                f"{f:<10} aliases: {', '.join(spec.aliases) or '—'}; values: {', '.join(spec.values) or 'from the names'}"
            )
    if run:
        obs = fc.observed(names, cfg)
        typer.echo("")
        for f, vals in obs["facets"].items():
            typer.echo(f"{f:<10} " + ", ".join(f"{v} ({n})" for v, n in vals.items()))
        for d, vs in obs["unclaimed_dims"].items():
            typer.secho(f"{d:<10} (no facet) " + ", ".join(vs[:20]), fg="yellow")
