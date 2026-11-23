"""``mf`` commands — Work orders for operator models: mf work …."""

from __future__ import annotations

from pathlib import Path

import typer

from model_forensic.cli import base
from model_forensic.cli.base import ConfigOpt, app

work_app = typer.Typer(
    help="Work orders for an operator model (Copilot · Luna · local Qwen): list, show, brief, check, escalate."
)


app.add_typer(work_app, name="work")


def _order(oid: str):  # type: ignore[no-untyped-def]
    from model_forensic.application import workorders as wo

    orders = wo.load()
    if oid not in orders:
        raise typer.BadParameter(f"unknown work order {oid!r}; `mf work list` shows them")
    return orders[oid]


@work_app.command("list")
def work_list_cmd(tier: str | None = typer.Option(None, help="local | mid | top")) -> None:
    """Every work order: id, tier, when to use it."""
    from model_forensic.application import workorders as wo

    for w in wo.load().values():
        if tier and w.tier != tier:
            continue
        flag = " (planned)" if w.status == "planned" else ""
        typer.echo(f"{w.id:<28} {w.tier:<5} {w.title}{flag}\n{'':34}when: {w.trigger}")


@work_app.command("show")
def work_show_cmd(oid: str) -> None:
    """The work order as the operator follows it."""
    from model_forensic.application import workorders as wo

    typer.echo(wo.card(_order(oid)))


@work_app.command("brief")
def work_brief_cmd(
    oid: str,
    run: str = typer.Option(..., "--run"),
    out: Path | None = typer.Option(None, "--out"),
    config: Path | None = ConfigOpt,
) -> None:
    """The card + the run's brief (what the task needs first, then everything), ready to give to the operator."""
    from model_forensic.application import workorders as wo
    from model_forensic.application.harness import brief

    w = _order(oid)
    if not w.task:
        raise typer.BadParameter(f"{oid} has no run brief; its inputs: {', '.join(w.inputs) or 'see the card'}")
    c = base.make_container(config)
    text = wo.card(w) + "\n---\n\n" + brief(c.service, c.knowledge, run, task=w.task, focus=w.focus or None)
    if out:
        out.write_text(text, encoding="utf-8")
        typer.echo(f"wrote {out}")
    else:
        typer.echo(text)


@work_app.command("check")
def work_check_cmd(
    oid: str,
    file: Path,
    dossier: Path | None = typer.Option(None, "--dossier", help="for WO-interpret-dossier: the dossier it answers"),
) -> None:
    """Validate an operator's output against the work order's contract (exit 1 with the errors to repair)."""
    from model_forensic.application import workorders as wo

    w = _order(oid)
    errors = wo.check(w, file, dossier)
    for e in errors:
        typer.secho(f"✗ {e}", fg="red")
    if errors:
        typer.echo("repair from these errors once; if it still fails: mf work escalate " + oid + f" --file {file}")
        raise typer.Exit(1)
    typer.secho(f"✓ {file} meets the {oid} contract", fg="green")
    if w.output.kind in ("config", "plot-specs", "calibration") and w.validate_:
        typer.echo(f"also run: {w.validate_}")


@work_app.command("escalate")
def work_escalate_cmd(
    oid: str,
    file: Path | None = typer.Option(None, "--file", help="the operator's output"),
    note: str = typer.Option("", "--note", help="what was tried, why it is stuck"),
    dossier: Path | None = typer.Option(None, "--dossier"),
    config: Path | None = ConfigOpt,
) -> None:
    """Write the escalation packet (≈2k tokens) for an expensive model; prints where it is."""
    from model_forensic.application import workorders as wo

    text = wo.escalate(_order(oid), file, note, dossier)
    p = wo.save_packet(text, base.make_container(config).knowledge.folder, oid)
    typer.echo(f"wrote {p} (≈{len(text) // 4} tokens) — give it to Claude / Opus; nothing else is needed")
