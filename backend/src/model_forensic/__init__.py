"""Model Forensic: a forensic workbench for ML experiments."""

from __future__ import annotations

from typing import Any

__version__ = "1.0.0rc2"


def connect(config: Any = None, sync: bool = False) -> Any:
    """Notebook bridge (pandas): see ``model_forensic.notebook``."""
    from model_forensic.notebook import connect as _connect

    return _connect(config, sync)
