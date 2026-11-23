"""``mf`` commands — Studies and the experiment ledger: mf study …, mf ledger …."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from model_forensic.cli import base
from model_forensic.cli.base import ConfigOpt, app

study_app = typer.Typer(help="Studies: hypotheses, evidence and verification for one experiment or a small group.")
app.add_typer(study_app, name="study")


@study_app.command("list")
def study_list(config: Path | None = ConfigOpt) -> None:
    """List studies."""
    for st in base.make_container(config).studies.all():
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
    st = base.make_container(config).studies.create(title, goal, run or [], baseline)
    typer.echo(f"created {st.id} — open it at /studies/{st.id}")


@study_app.command("hypothesis")
def study_hypothesis(
    study_id: str,
    statement: str,
    change: str = typer.Option("", help="Proposed architecture change"),
    config: Path | None = ConfigOpt,
) -> None:
    """Add a hypothesis to a study."""
    st = base.make_container(config).studies.add_hypothesis(study_id, statement, proposed_change=change)
    typer.echo(f"added {st.hypotheses[-1].id}")


@study_app.command("verify")
def study_verify(
    study_id: str, hypothesis_id: str, test_run: str | None = None, config: Path | None = ConfigOpt
) -> None:
    """Compare a hypothesis' test run against its reference run (optionally linking the test run first)."""
    c = base.make_container(config)
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

    c = base.make_container(config)
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
    md = base.make_container(config).studies.to_markdown(study_id)
    if out:
        out.write_text(md)
        typer.echo(f"wrote {out}")
    else:
        typer.echo(md)


ledger_app = typer.Typer(help="Experiment ledger: hypothesis → change → outcome → decision.")
app.add_typer(ledger_app, name="ledger")


def _ledger(config: Path | None):  # type: ignore[no-untyped-def]
    from model_forensic.application.ledger import LedgerService

    c = base.make_container(config)
    return LedgerService(c.docs, c.service), c


@ledger_app.command("list")
def ledger_list_cmd(
    status: str | None = typer.Option(None, help="planned | running | analysed | decided"),
    text: str | None = typer.Option(None, help="search title / runs"),
    config: Path | None = ConfigOpt,
) -> None:
    """Entries, newest first."""
    led, _ = _ledger(config)
    items, total = led.find(status, text, 200)
    for e in items:
        typer.echo(
            f"{e['id']:<14} {e['status']:<9} {(e.get('decision') or '—'):<13} {e['title'][:60]:<60} {e.get('headline') or ''}"
        )
    typer.echo(f"{total} entr{'y' if total == 1 else 'ies'}")


@ledger_app.command("add")
def ledger_add_cmd(
    title: str = typer.Option(..., "--title"),
    baseline: str | None = typer.Option(None, "--baseline"),
    candidate: list[str] = typer.Option([], "--candidate", help="repeat for several candidates"),
    hypothesis: str = typer.Option("", "--hypothesis"),
    change: str = typer.Option("", "--change", help="what was changed (empty: read from the config diff)"),
    tag: list[str] = typer.Option([], "--tag"),
    config: Path | None = ConfigOpt,
) -> None:
    """New entry; with --baseline and --candidate the outcome is computed at once."""
    led, _ = _ledger(config)
    e = led.create(
        "cli",
        {
            "title": title,
            "baseline": baseline,
            "candidates": candidate,
            "hypothesis": hypothesis,
            "change": change,
            "tags": tag,
        },
    )
    typer.echo(f"{e['id']} {e['status']}  {led._headline(e)}")


@ledger_app.command("show")
def ledger_show_cmd(entry_id: str, config: Path | None = ConfigOpt) -> None:
    """One entry as Markdown."""
    led, _ = _ledger(config)
    typer.echo(led.markdown([entry_id]))


@ledger_app.command("decide")
def ledger_decide_cmd(
    entry_id: str,
    decision: str = typer.Argument(..., help="adopt | reject | inconclusive | follow-up"),
    note: str = typer.Option("", "--note"),
    config: Path | None = ConfigOpt,
) -> None:
    """Record the decision (the entry becomes *decided*)."""
    led, _ = _ledger(config)
    e = led.update(entry_id, "cli", {"decision": decision, "decision_note": note})
    typer.echo(f"{e['id']} {e['status']}: {e['decision']}")


@ledger_app.command("refresh")
def ledger_refresh_cmd(entry_id: str, config: Path | None = ConfigOpt) -> None:
    """Re-compute the outcome."""
    led, _ = _ledger(config)
    e = led.refresh(entry_id, "cli")
    typer.echo(f"{e['id']} {e['status']}  {led._headline(e)}")


@ledger_app.command("export")
def ledger_export_cmd(out: Path | None = typer.Option(None, "--out"), config: Path | None = ConfigOpt) -> None:
    """The whole ledger as Markdown (stdout or --out)."""
    led, _ = _ledger(config)
    text = led.markdown()
    if out:
        out.write_text(text, encoding="utf-8")
        typer.echo(f"wrote {out}")
    else:
        typer.echo(text)


@ledger_app.command("promote")
def ledger_promote_cmd(entry_id: str, config: Path | None = ConfigOpt) -> None:
    """A decided entry → a proposed knowledge entry (accept it in Knowledge)."""
    led, c = _ledger(config)
    added, errors = c.knowledge.import_text(led.knowledge_yaml(entry_id), via=f"ledger:{entry_id}")
    for e in errors:
        typer.secho(f"! {e}", fg="yellow")
    for k in added:
        typer.echo(f"proposed {k.id} ({k.kind})")
    raise typer.Exit(0 if added else 1)
