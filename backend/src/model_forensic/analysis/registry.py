"""Analysis plugin registry. Built-ins register on import; third parties via entry points."""

from __future__ import annotations

from importlib.metadata import entry_points

from model_forensic.analysis.base import AnalysisPlugin
from model_forensic.shared.errors import NotFoundError
from model_forensic.shared.logging import get_logger

log = get_logger(__name__)


class AnalysisRegistry:
    def __init__(self) -> None:
        self._plugins: dict[str, AnalysisPlugin] = {}

    def register(self, plugin: AnalysisPlugin) -> None:
        if plugin.name in self._plugins:
            raise ValueError(f"duplicate analysis plugin: {plugin.name}")
        self._plugins[plugin.name] = plugin

    def get(self, name: str) -> AnalysisPlugin:
        try:
            return self._plugins[name]
        except KeyError:
            raise NotFoundError(f"unknown analysis: {name}", details={"available": sorted(self._plugins)}) from None

    def all(self) -> list[AnalysisPlugin]:
        return [self._plugins[k] for k in sorted(self._plugins)]

    def load_entry_points(self, group: str = "model_forensic.analyses") -> None:
        for ep in entry_points(group=group):
            try:
                self.register(ep.load()())
            except Exception:
                log.exception("failed to load analysis plugin", entry_point=ep.name)


def builtin_registry() -> AnalysisRegistry:
    from model_forensic.analysis.plugins import (
        architecture,
        config_attribution,
        heads,
        loss_forensics,
        pipeline,
        regression,
        sparse_detection,
        study,
        training_dynamics,
    )

    reg = AnalysisRegistry()
    for mod in (training_dynamics, loss_forensics, regression, config_attribution, sparse_detection):
        reg.register(mod.PLUGIN)
    for plugin in (*architecture.PLUGINS, *heads.PLUGINS, *pipeline.PLUGINS, *study.PLUGINS):
        reg.register(plugin)
    return reg
