"""``mf`` command-line interface: the entry point.

The commands live by area in this package (design review D7) and register on ``base.app`` when imported here:
runs, knowledge, studies, quality, server, analysis, work.
"""

from __future__ import annotations

import typer

from model_forensic.cli import analysis, base, knowledge, quality, runs, server, studies, work
from model_forensic.cli.base import app
from model_forensic.cli.quality import is_default_threshold
from model_forensic.cli.runs import LIGHT_FILES, demo_expectations
from model_forensic.shared.errors import ForensicError

__all__ = ["LIGHT_FILES", "app", "base", "demo_expectations", "is_default_threshold", "main"]
AREAS = (runs, knowledge, studies, quality, server, analysis, work)


def main() -> None:
    try:
        app()
    except ForensicError as exc:
        typer.secho(f"error [{exc.code}]: {exc.message}", fg="red", err=True)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
