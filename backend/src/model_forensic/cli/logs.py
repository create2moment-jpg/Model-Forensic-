"""``mf logs`` — what a run's logs say: insights (facts, problems, events, curves) and the patterns that read them."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from model_forensic.cli import base
from model_forensic.cli.base import ConfigOpt, app

logs_app = typer.Typer(help="Log insights: facts, problems, events and curves pulled out of a run's logs by patterns.")
app.add_typer(logs_app, name="logs")


def _store(config: Path | None):  # type: ignore[no-untyped-def]
    from model_forensic.application.log_insights import PatternStore

    c = base.make_container(config)
    return PatternStore(c.docs, c.knowledge.folder), c


@logs_app.command("insights")
def logs_insights(
    run: str = typer.Option(..., "--run"),
    stage: str | None = typer.Option(None, "--stage"),
    as_json: bool = typer.Option(False, "--json"),
    config: Path | None = ConfigOpt,
) -> None:
    """Problems first, then facts and events; the lines no pattern explains yet."""
    from model_forensic.application.log_insights import insights

    s, c = _store(config)
    ins = insights(c.service, s, run, stage)
    if as_json:
        typer.echo(
            json.dumps(
                {k: v for k, v in ins.items() if k != "series"} | {"series": [x["key"] for x in ins["series"]]},
                indent=2,
                default=str,
            )
        )
        return
    cov = ins["coverage"]
    typer.echo(
        f"{cov['lines']} lines in {len(ins['files'])} log file(s); {cov['matched']} explained by {ins['patterns']['active']} patterns"
    )
    for pr in ins["problems"]:
        first = pr["first"] or {}
        typer.echo(
            f"  [{pr['severity']}] {pr['title']} ×{pr['count']} — first: {first.get('file')}:{first.get('line')} {str(first.get('text', ''))[:100]}"
        )
    for f in ins["facts"]:
        typer.echo(f"  {f['title']}: {f['field']} = {f['value']}")
    for s_ in ins["series"]:
        typer.echo(f"  curve {s_['title']} · {s_['label']}: {len(s_['y'])} points")
    if ins["uncovered"]:
        typer.echo(f"{len(ins['uncovered'])} kinds of line no pattern explains — `mf logs propose --run {run}`:")
        for u in ins["uncovered"][:10]:
            typer.echo(f"  {u['count']:>5}× {u['example'][:110]}")


@logs_app.command("propose")
def logs_propose(
    run: str = typer.Option(..., "--run"),
    stage: str | None = typer.Option(None, "--stage"),
    config: Path | None = ConfigOpt,
) -> None:
    """Patterns for the unexplained lines (the local LLM, grounded; else from the lines' numbers) — stored as proposed."""
    from model_forensic.application.log_insights import propose

    s, c = _store(config)
    out = propose(c.service, s, c.llm, run, stage)
    typer.echo(f"{len(out['proposed'])} proposed by {out['model']}; {len(out['rejected'])} rejected")
    for p in out["proposed"]:
        typer.echo(f"  {p['id']:<28} {p['kind']:<7} {p['title'][:60]}  (example: {str(p['example'])[:60]})")
    for r in out["rejected"]:
        typer.echo(f"  rejected: {r['reason']}  {r['regex'][:60]}")
    typer.echo("accept with `mf logs accept <id>`")


@logs_app.command("patterns")
def logs_patterns(config: Path | None = ConfigOpt) -> None:
    """Every pattern: built in, from the codebase (inbox log_patterns/), proposed by the LLM, written by you."""
    s, _ = _store(config)
    for p in s.all():
        typer.echo(f"{p.id:<30} {p.source:<8} {p.status:<9} {p.kind:<7} {p.category:<12} {p.title[:60]}")


@logs_app.command("accept")
def logs_accept(pid: str, config: Path | None = ConfigOpt) -> None:
    """Accept a proposed pattern: from now on it reads every run's logs."""
    s, _ = _store(config)
    typer.echo(f"{s.decide(pid, 'active').id} is active")


@logs_app.command("reject")
def logs_reject(pid: str, config: Path | None = ConfigOpt) -> None:
    s, _ = _store(config)
    typer.echo(f"{s.decide(pid, 'rejected').id} rejected")
