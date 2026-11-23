"""D7 safety net: splitting the API and CLI into modules must not lose, add or shadow anything.

The snapshots (tests/snapshots/) are the HTTP routes and the `mf` commands. Adding or removing a route / command is
fine — add or remove its line in tests/snapshots/api_routes.txt or cli_commands.txt in the same change (the failure
message lists exactly the missing and new lines).
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.routing import APIRoute
from starlette.routing import Match

SNAP = Path(__file__).resolve().parents[1] / "snapshots"


def _routes(app):  # type: ignore[no-untyped-def]
    return [r for r in app.routes if isinstance(r, APIRoute)]


def _commands(t, prefix: str = "") -> list[str]:  # type: ignore[no-untyped-def]
    out = [prefix + (c.name or c.callback.__name__.replace("_", "-")) for c in t.registered_commands]
    for g in t.registered_groups:
        out.append(prefix + g.name + " (group)")
        out += _commands(g.typer_instance, prefix + g.name + " ")
    return out


def test_every_route_is_still_there(container) -> None:  # type: ignore[no-untyped-def]
    from model_forensic.api.app import create_app

    have = {f"{','.join(sorted(r.methods))} {r.path}" for r in _routes(create_app(container))}
    want = set((SNAP / "api_routes.txt").read_text().split("\n")) - {""}
    assert have == want, (
        f"missing: {sorted(want - have)}\nnew: {sorted(have - want)} (update tests/snapshots/api_routes.txt)"
    )


def test_every_route_answers_its_own_url(container) -> None:  # type: ignore[no-untyped-def]
    """Registration order decides which route answers a URL: none may be shadowed by one registered before it."""
    from model_forensic.api.app import create_app

    routes = _routes(create_app(container))
    shadowed = []
    for r in routes:
        url = re.sub(r"\{[^}]+\}", "x1", r.path)
        for m in sorted(r.methods):
            first = next(c for c in routes if m in c.methods and c.matches({"type": "http", "path": url, "method": m,
                                                                           "root_path": ""})[0] == Match.FULL)  # fmt: skip
            if first is not r:
                shadowed.append(f"{m} {r.path} is answered by {first.path}")
    assert not shadowed, "\n".join(shadowed)


def test_every_command_is_still_there() -> None:
    from model_forensic.cli.main import app

    have = set(_commands(app))
    want = set((SNAP / "cli_commands.txt").read_text().split("\n")) - {""}
    assert have == want, (
        f"missing: {sorted(want - have)}\nnew: {sorted(have - want)} (update tests/snapshots/cli_commands.txt)"
    )
