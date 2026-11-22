"""Remote workspaces: only logs, configs and Lightning metrics.csv are fetched automatically (≤ 50 MB a request);
evaluation files, plots and checkpoints come only on request — views read what is cached and say what is not."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from model_forensic.container import build
from model_forensic.infrastructure.artifacts.store import DownloadProfile
from model_forensic.settings import BackendConfig, Settings

RUN = "ws:xf-pipe-finetune"


def _remote(tmp: Path, root: Path):  # type: ignore[no-untyped-def]
    c = build(Settings(data_dir=tmp / ".mf", backends=[BackendConfig(kind="local", name="ws", options={"root": str(root)})],
                       artifact_min_free_gb=0.0, knowledge_dir=tmp / "kn"))  # fmt: skip
    src = c.service.sources["ws"]
    src.remote = True  # type: ignore[misc]  # behaves like Azure ML
    c.service.sync()
    fetched: list[str] = []
    orig = src.open_artifact

    def spy(run_id: str, path: str, offset: int = 0):  # type: ignore[no-untyped-def]
        fetched.append(path)
        return orig(run_id, path, offset)

    src.open_artifact = spy  # type: ignore[method-assign]
    return c, fetched


def test_views_never_download_evaluation_files_by_themselves(tmp_path: Path, demo_root: Path) -> None:
    from model_forensic.api.app import create_app

    c, fetched = _remote(tmp_path, demo_root)
    api = TestClient(create_app(c))
    pend = c.service.pending(RUN, DownloadProfile.EVAL_JSON)
    assert pend["files"] > 0
    pr = api.get(f"/api/v1/runs/{RUN}/precision-recall").json()
    assert {p["profile"] for p in pr["pending"]} == {"eval_json", "eval_parquet"} and pr["pending"][0]["files"] > 0
    seq = api.get(f"/api/v1/runs/{RUN}/sequences").json()
    assert seq["pending"]["profile"] == "eval_parquet" and not any(
        m["code"] == "sequences.none" for m in seq["messages"] if seq["pending"]["files"]
    )
    chk = api.get("/api/v1/check", params={"run": RUN})
    if chk.status_code == 200:
        assert chk.json()["pending"][0]["profile"] == "eval_json"
    evalish = [p for p in fetched if "evaluation" in p or p.endswith((".parquet", ".png", ".ckpt"))]
    assert evalish == [], evalish  # nothing heavy was downloaded without a request
    # on request: downloaded, then read
    assert c.service.eval_json(RUN, fetch=True)
    assert c.service.pending(RUN, DownloadProfile.EVAL_JSON)["files"] == 0
    assert c.service.eval_json(RUN)  # now from the cache, no new download
    n = len(fetched)
    c.service._evals_cache.drop(RUN)
    c.service.eval_json(RUN)
    assert len(fetched) == n


def test_logs_are_fetched_automatically_within_the_budget(tmp_path: Path, demo_root: Path) -> None:
    import re

    from model_forensic.application.run_logs import scan_logs

    c, _fetched = _remote(tmp_path, demo_root)
    c.service.auto_fetch_max_bytes = 1  # nothing fits
    scan = scan_logs(c.service, c.service.ref(RUN), [re.compile("x")])
    skipped = [f for f in scan.files if "automatic limit" in (f.get("error") or "")]
    assert scan.files and len(skipped) == len([f for f in scan.files if (f["size_bytes"] or 0) > 1])
    c.service.auto_fetch_max_bytes = 50 * 1024**2
    scan = scan_logs(c.service, c.service.ref(RUN), [re.compile("x")])
    assert not any("automatic limit" in (f.get("error") or "") for f in scan.files)


def test_cache_clear_keeps_light_files_and_frees_the_rest(tmp_path: Path, demo_root: Path) -> None:
    import re

    from typer.testing import CliRunner

    from model_forensic.cli.main import LIGHT_FILES, app

    light = re.compile(LIGHT_FILES, re.I)
    assert all(light.search(p) for p in ("logs/azureml/executionlogs.txt", "user_logs/std_log.txt",
                                         "stage_output/summaries/train/composed_config.yaml", "summaries/x/metrics.csv"))  # fmt: skip
    assert not any(light.search(p) for p in ("evaluation/Car/torch_metric_evaluation/a.json", "checkpoints/best.ckpt",
                                             "evaluation/seq.parquet", "visualizations/plot.png"))  # fmt: skip
    cfg = tmp_path / "mf.yaml"
    cfg.write_text(f"data_dir: {tmp_path / '.mf'}\nbackends:\n  - kind: local\n    name: local\n    options: {{root: {demo_root}}}\n"
                   "artifact_min_free_gb: 0\n")  # fmt: skip
    from model_forensic.container import build
    from model_forensic.settings import Settings

    c = build(Settings.load(cfg))
    c.service.sync()
    c.service.eval_json("local:xf-pipe-finetune", fetch=True)
    before = c.service.artifact_store.usage_bytes()
    assert before > 0
    r = CliRunner().invoke(app, ["cache-clear", "--run", "local:xf-pipe-finetune", "--yes", "-c", str(cfg)])
    assert r.exit_code == 0 and "removed" in r.output, r.output
    assert build(Settings.load(cfg)).service.artifact_store.usage_bytes() < before
