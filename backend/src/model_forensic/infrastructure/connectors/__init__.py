"""Backend connectors and their registry."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from model_forensic.application.ports import RunSource
from model_forensic.shared.errors import NotFoundError

ConnectorFactory = Callable[[dict[str, Any]], RunSource]
_FACTORIES: dict[str, ConnectorFactory] = {}


def register_connector(kind: str, factory: ConnectorFactory) -> None:
    _FACTORIES[kind] = factory


def create_connector(kind: str, options: dict[str, Any]) -> RunSource:
    _ensure_builtins()
    try:
        return _FACTORIES[kind](options)
    except KeyError:
        raise NotFoundError(f"unknown connector kind {kind!r}", details={"available": sorted(_FACTORIES)}) from None


def _ensure_builtins() -> None:
    if "local" not in _FACTORIES:
        from model_forensic.infrastructure.connectors.local.connector import LocalRunSource

        register_connector("local", lambda o: LocalRunSource(name=o.get("name", "local"), root=o["root"]))
    if "azureml" not in _FACTORIES:

        def _azure(o: dict[str, Any]) -> RunSource:
            from model_forensic.infrastructure.connectors.azureml.connector import AzureMLRunSource

            return AzureMLRunSource.from_options(o)

        register_connector("azureml", _azure)
