"""``mf track`` — continuous training: follow an experiment of a workspace over time (several at once)."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from model_forensic.cli import base
from model_forensic.cli.base import ConfigOpt, app

track_app = typer.Typer(help="Continuous training tracks: an experiment of a workspace, followed run after run.")
app.add_typer(track_app, name="track")


def _tracks(config: Path | None):  # type: ignore[no-untyped-def]
    from model_forensic.application.tracks import TrackService

    c = base.make_container(config)
    return TrackService(c.docs, c.service)


@track_app.command("list")
def track_list(config: Path | None = ConfigOpt) -> None:
    """The tracks you follow, with their latest state."""
    ts = _tracks(config)
    items = ts.all()
    for t in items:
        pr = ts.progress(t["id"])
        typer.echo(f"{t['id']:<32} {t['workspace']:<16} {t['experiment']:<28} {pr['state']:<8} {pr['sentence'][:90]}")
    typer.echo(f"{len(items)} track(s)")


@track_app.command("add")
def track_add(
    workspace: str = typer.Option(..., "--workspace", help="the workspace name (as in mf backends)"),
    experiment: str = typer.Option(..., "--experiment"),
    title: str = typer.Option("", "--title"),
    metric: list[str] = typer.Option([], "--metric", help="repeat; default: the project's headline metrics"),
    config: Path | None = ConfigOpt,
) -> None:
    """Follow an experiment of a workspace (e.g. the continuous-training workspace)."""
    t = _tracks(config).create({"workspace": workspace, "experiment": experiment, "title": title, "metrics": metric})
    typer.echo(f"tracking {t['experiment']} in {t['workspace']} as {t['id']}")


@track_app.command("remove")
def track_remove(track_id: str, config: Path | None = ConfigOpt) -> None:
    """Stop following a track (its runs stay where they are)."""
    _tracks(config).delete(track_id)
    typer.echo(f"removed {track_id}")


@track_app.command("show")
def track_show(track_id: str, as_json: bool = typer.Option(False, "--json"), config: Path | None = ConfigOpt) -> None:
    """Every run of the track: its headline values, vs the previous run and vs the best so far."""
    pr = _tracks(config).progress(track_id)
    if as_json:
        typer.echo(json.dumps(pr, indent=2, default=str))
        return
    keys = [m["key"] for m in pr["metrics"]]
    typer.echo(pr["sentence"])
    typer.echo(f"{'run':<34} {'status':<10} " + " ".join(f"{k[:14]:>14}" for k in keys) + "  vs previous  vs best")
    for r in pr["runs"]:
        vals = " ".join(f"{r['values'][k]:>14.4g}" if k in r["values"] else f"{'—':>14}" for k in keys)
        vp = (r["vs_previous"] or {}).get("state", "")
        vb = "best" if r["best"] else (r["vs_best"] or {}).get("state", "")
        typer.echo(f"{r['name'][:34]:<34} {r['status']:<10} {vals}  {vp:<11}  {vb}")
    if pr["missing_values"]:
        typer.echo(
            f"{len(pr['missing_values'])} finished run(s) have no values yet — open the track in the app to load them"
        )


@track_app.command("check")
def track_check(track_id: str, config: Path | None = ConfigOpt) -> None:
    """Exit 1 when the latest run failed or is worse than the best so far (beyond noise) — for CI or a cron job."""
    pr = _tracks(config).progress(track_id)
    typer.echo(f"{pr['state']}: {pr['sentence']}")
    if pr["state"] in ("failed", "worse", "mixed"):
        raise typer.Exit(1)
