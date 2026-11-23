"""Shared pieces of the ``mf`` CLI: the Typer app every area registers on, the container factory and common options.

Split out of main.py (design review D7). Commands look the factory up as ``base.make_container`` at call time, so
tests can replace it in one place.
"""

from __future__ import annotations

from pathlib import Path

import typer

from model_forensic.container import Container, build
from model_forensic.settings import Settings
from model_forensic.shared.logging import configure_logging

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Model Forensic: ML experiment forensics.")
ICON = {"critical": "[CRIT]", "high": "[HIGH]", "medium": "[MED ]", "low": "[LOW ]", "info": "[INFO]"}


def make_container(config: Path | None = None) -> Container:
    s = Settings.load(config)
    configure_logging(s.log_level, json_output=False)
    return build(s)


ConfigOpt = typer.Option(None, "--config", "-c", help="Path to model-forensic.yaml")
