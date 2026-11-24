"""``mf`` commands — Serving and operations: serve, status, stop, backup, init, paths, checks, doctor, setup, projects."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import typer

from model_forensic.cli import base
from model_forensic.cli.base import ConfigOpt, app
from model_forensic.container import build
from model_forensic.settings import Settings


@app.command()
def serve(
    host: str = "127.0.0.1",
    port: int = 8765,
    remote: bool = typer.Option(
        False, "--remote", help="Open from other machines (all interfaces), protected by an access token"
    ),
    token: str | None = typer.Option(
        None, "--token", envvar="MF_ACCESS_TOKEN", help="The access token to require (default: a new random one)"
    ),
    config: Path | None = ConfigOpt,
) -> None:
    """Start the HTTP API (and the web UI). On an SSH machine: forward the port (the command is printed), or
    ``--remote`` to open it to other machines with an access token."""
    import secrets

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
    if remote and host in {"127.0.0.1", "localhost"}:
        host = "0.0.0.0"  # noqa: S104 - --remote: reachable from other machines, behind the access token below
    c = base.make_container(config)
    loopback = host in {"127.0.0.1", "localhost", "::1"}
    if c.settings.server.mode != "shared" and not loopback:
        # local mode has no sign-in: beyond this machine, nothing without the access token
        c.settings.server.access_token = token or secrets.token_urlsafe(24)
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
    links: list[str] = []
    if c.settings.server.access_token:
        links = [f"http://{a}:{port}/?token={c.settings.server.access_token}" for a in reg.lan_addresses()] or [
            f"http://<this machine>:{port}/?token={c.settings.server.access_token}"
        ]
    reg.register(host, port, c.settings.data_dir, links)
    import os as _os

    from model_forensic.shared.webdist import web_dist_candidates

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
    hint = reg.ssh_tunnel_hint(port) if loopback else None
    if hint:
        typer.secho(
            f"! you are on SSH: {shown} is this machine, not your laptop. On the laptop run:\n"
            f"    {hint}\n"
            f"  then open http://127.0.0.1:{port} there (VS Code / Cursor Remote-SSH forward {port} by themselves).\n"
            f"  Or open it to other machines with an access token: mf serve --remote",
            fg="yellow",
        )
    if links:
        typer.secho(
            "Open from another machine (the link carries the access token — share it only with yourself):", bold=True
        )
        for link in links:
            typer.echo(f"    {link}")
        typer.secho(
            "  Anyone who reaches this port without the token gets nothing. mf status prints the link again.", dim=True
        )
        if _os.environ.get("SSH_CONNECTION"):
            typer.secho(
                f"  (no direct route from your laptop? the SSH tunnel always works: {reg.ssh_tunnel_hint(port)})",
                dim=True,
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
        for link in s.get("open_links") or []:
            typer.echo(f"    from another machine: {link}")


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

    c = base.make_container(config)
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
    c = base.make_container(config)
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

    c = base.make_container(config)
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


@app.command()
def checks(
    config: Path | None = ConfigOpt,
    as_json: bool = typer.Option(False, "--json", help="Machine-readable (feed it to the repair prompt)"),
) -> None:
    """Configuration checks — each with severity, current vs expected, the fix, the repair prompt and how to validate."""
    from model_forensic.application.config_checks import run, summary
    from model_forensic.application.test_space import TestSpace
    from model_forensic.shared.paths import mf_home

    c = base.make_container(config)
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
