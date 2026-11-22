"""Prefetch: a run's metadata cached (memory + disk for finished runs) so its page opens fast; `experiment:mine`."""

from __future__ import annotations

import gzip
import time
from pathlib import Path

from fastapi.testclient import TestClient

from model_forensic.container import build
from model_forensic.domain.metrics import MetricKey, MetricSeries
from model_forensic.infrastructure.persistence.meta_cache import MetaCache
from model_forensic.settings import BackendConfig, Settings

RUN = "local:xf-pipe-finetune"


def _series(n: int) -> MetricSeries:
    k = MetricKey.parse("train/loss")
    return MetricSeries(key=k, steps=tuple(float(i) for i in range(n)), values=tuple(float(i) for i in range(n)))


def test_meta_cache_roundtrip_corruption_and_budget(tmp_path: Path) -> None:
    c = MetaCache(tmp_path, budget_bytes=10**9)
    c.put("local:a", "series", [_series(10)])
    got = c.get("local:a", "series", MetricSeries)
    assert got is not None and got[0].values[-1] == 9.0
    assert c.get("local:b", "series", MetricSeries) is None
    p = c._path("local:a", "series")
    p.write_bytes(b"not gzip")
    assert c.get("local:a", "series", MetricSeries) is None  # corrupt = a miss, never an error
    with gzip.open(p, "wt") as f:
        f.write('{"v": 0, "run": "local:a", "items": []}')
    assert c.get("local:a", "series", MetricSeries) is None  # old format = a miss
    small = MetaCache(tmp_path / "small", budget_bytes=6_000)
    for i in range(8):
        small.put(f"local:r{i}", "series", [_series(400)])
        time.sleep(0.01)
    assert small.size() <= 6_000 and small.get("local:r7", "series", MetricSeries) is not None
    assert small.get("local:r0", "series", MetricSeries) is None  # least recently used went first


def _container(data_dir: Path, demo_root: Path, remote: bool = False):  # type: ignore[no-untyped-def]
    c = build(
        Settings(
            data_dir=data_dir,
            backends=[BackendConfig(kind="local", name="local", options={"root": str(demo_root)})],
            artifact_min_free_gb=0.0,
            knowledge_dir=data_dir / "knowledge",
        )
    )
    c.service.sources["local"].remote = remote  # True: behave like a remote workspace (disk-cacheable)
    c.service.sync()
    return c


def test_prefetch_survives_a_restart_and_skips_the_backend(tmp_path: Path, demo_root: Path) -> None:
    c1 = _container(tmp_path / ".mf", demo_root, remote=True)  # local folders themselves are never disk-cached
    assert not c1.service.is_warm(RUN)
    out = c1.service.warm_up(RUN)
    assert out["stages"] == ["train_finetune", "evaluate_pretrain", "evaluate_finetune"]
    assert out["metrics"] > 0 and out["files"] > 0 and out["on_disk"] is True
    assert c1.service.is_warm(RUN)
    # a restarted server reads the run from disk — even with the backend unreachable
    c2 = _container(tmp_path / ".mf", demo_root, remote=True)
    src = c2.service.sources["local"]

    def boom(*_a: object, **_k: object) -> object:
        raise ConnectionError("backend unreachable")

    src.get_metric_series = boom  # type: ignore[method-assign]
    src.get_configs = boom  # type: ignore[method-assign]
    src.list_artifacts = boom  # type: ignore[method-assign]
    assert c2.service.is_warm(RUN)
    o = c2.service.overview(RUN)
    assert len(o.metric_keys) == out["metrics"] and len(o.artifacts) == out["files"]


def test_running_runs_are_never_cached_on_disk_and_force_refetches(container) -> None:
    svc = container.service
    running = next(r for r in svc.search("status:running").runs)
    svc.sources["local"].remote = True
    svc.warm_up(str(running.ref))
    assert not svc.meta_cache.has(str(running.ref))  # still changing: memory with a short TTL only
    svc.warm_up(RUN)
    calls = {"n": 0}
    orig = svc.sources["local"].get_metric_series

    def counting(*a: object, **k: object) -> object:
        calls["n"] += 1
        return orig(*a, **k)

    svc.sources["local"].get_metric_series = counting  # type: ignore[method-assign]
    svc.warm_up(RUN)
    assert calls["n"] == 0  # cached
    svc.warm_up(RUN, force=True)
    assert calls["n"] >= 1  # force re-reads from the backend


def test_my_experiments_filter_and_list(container) -> None:
    svc = container.service
    svc.me = lambda: ["dana"]
    mine = svc.search("experiment:mine", limit=500)
    exps = {r.experiment for r in mine.runs}
    dana_exps = {r.experiment for r in svc.search("user:me", limit=500).runs}
    assert exps == dana_exps and mine.total >= len(svc.search("user:me", limit=500).runs)
    assert any(r.user != "dana" for r in mine.runs) or len(exps) == 1  # other people's runs in my experiments too
    listing = svc.experiments()
    assert listing[0]["mine"] and {e["name"] for e in listing if e["mine"]} == dana_exps
    assert all(e["runs"] >= 1 and "users" in e for e in listing)


def test_api_prefetch_job_status_and_experiments(container) -> None:
    from model_forensic.api.app import create_app

    container.service.me = lambda: ["dana"]
    c = TestClient(create_app(container))
    assert c.get("/api/v1/runs/warm", params={"runs": [RUN, "local:nope"]}).json() == {
        RUN: False,
        "local:nope": False,
    }
    job = c.post(f"/api/v1/runs/{RUN}/warm").json()["job_id"]
    for _ in range(200):
        j = c.get(f"/api/v1/jobs/{job}").json()
        if j["status"] != "running":
            break
        time.sleep(0.05)
    assert j["status"] == "done" and j["kind"] == "warm-up", j
    assert c.get("/api/v1/runs/warm", params={"runs": [RUN]}).json() == {RUN: True}
    ex = c.get("/api/v1/experiments").json()
    assert ex and ex[0]["mine"] is True
    assert c.get("/api/v1/runs", params={"q": "experiment:mine"}).json()["total"] > 0


def test_local_folders_are_never_disk_cached(tmp_path: Path) -> None:
    """A local run folder can change after it finished; a restart must show the change."""
    import shutil

    from model_forensic.infrastructure.demo.synthetic import generate_demo

    root = tmp_path / "runs"
    generate_demo(root)
    c1 = _container(tmp_path / ".mf", root)
    c1.service.warm_up(RUN)
    assert not c1.service.meta_cache.has(RUN)
    extra = root / "xf-pipe-finetune" / "added_later.txt"
    extra.write_text("new evidence")
    c2 = _container(tmp_path / ".mf", root)
    assert "added_later.txt" in {a.path for a in c2.service.artifacts(RUN)}
    shutil.rmtree(root)
