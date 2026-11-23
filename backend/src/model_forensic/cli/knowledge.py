"""``mf`` commands — Knowledge, documentation and harness briefs: mf knowledge …, mf docs …, mf harness …."""

from __future__ import annotations

from pathlib import Path

import typer

from model_forensic.cli import base
from model_forensic.cli.base import ConfigOpt, app

knowledge_app = typer.Typer(help="Team knowledge (run-independent): list, validate, import.")
app.add_typer(knowledge_app, name="knowledge")


harness_app = typer.Typer(help="Harness adaptation: briefs for a coding agent in your training project.")
app.add_typer(harness_app, name="harness")


@knowledge_app.command("list")
def knowledge_list_cmd(config: Path | None = ConfigOpt) -> None:
    """All knowledge entries with their status."""
    c = base.make_container(config)
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
    c = base.make_container(config)
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
    c = base.make_container(config)
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
    c = base.make_container(config)
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
    c = base.make_container(config)
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
    c = base.make_container(config)
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

    c = base.make_container(config)
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
    from model_forensic.application.expectations import schema_text as expectations_schema

    exp = out.with_name("expectations.schema.json")
    exp.write_text(expectations_schema(), encoding="utf-8")
    typer.echo(f"wrote {exp}")
