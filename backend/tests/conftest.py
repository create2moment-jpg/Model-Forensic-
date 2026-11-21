from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from model_forensic.container import Container, build
from model_forensic.infrastructure.demo.synthetic import generate_demo
from model_forensic.settings import BackendConfig, Settings

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="session")
def demo_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("demo-runs")
    generate_demo(root, now=NOW)
    return root


@pytest.fixture()
def container(tmp_path: Path, demo_root: Path) -> Container:
    s = Settings(
        data_dir=tmp_path / ".mf",
        backends=[BackendConfig(kind="local", name="local", options={"root": str(demo_root)})],
        artifact_min_free_gb=0.0,
    )
    c = build(s)
    c.service.sync()
    return c
