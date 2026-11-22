"""Real Azure job listings carry no pipeline children; stages must still be resolved (once) for indexed runs."""

from pathlib import Path

from model_forensic.application.ports import ListRunsQuery, Page
from model_forensic.container import build
from model_forensic.infrastructure.connectors import register_connector
from model_forensic.infrastructure.connectors.local.connector import LocalRunSource
from model_forensic.settings import BackendConfig, Settings


class ListingLikeAzure(LocalRunSource):
    """Listing without children (like the ARM job list); get_run with them (like runhistory)."""

    index_configs_on_sync = False  # like Azure: no per-run config reads during a sync
    calls = 0
    fail = False
    listing = False

    def list_runs(self, query: ListRunsQuery) -> Page:
        ListingLikeAzure.listing = True
        try:
            page = super().list_runs(query)
        finally:
            ListingLikeAzure.listing = False
        return Page(
            items=[r.model_copy(update={"children": (), "backend_extras": {}}) for r in page.items],
            next_cursor=page.next_cursor,
        )

    def get_run(self, run_id: str):  # type: ignore[no-untyped-def]
        if ListingLikeAzure.listing:
            return super().get_run(run_id)
        if run_id == "xf-pipe-finetune":
            ListingLikeAzure.calls += 1
            if ListingLikeAzure.fail:
                raise RuntimeError("runhistory unavailable")
        run = super().get_run(run_id)
        if run.children:
            stages = ",".join(f"{c.run_id}={c.run_id.split('-')[-1]}" for c in run.children)
            run = run.model_copy(update={"backend_extras": {**run.backend_extras, "stages": stages}})
        return run


def _container(tmp_path: Path, demo_root: Path):  # type: ignore[no-untyped-def]
    register_connector("azure-like", lambda o: ListingLikeAzure(name=o["name"], root=o["root"]))
    s = Settings(
        data_dir=tmp_path / ".mf",
        backends=[BackendConfig(kind="azure-like", name="ws", options={"root": str(demo_root)})],
        artifact_min_free_gb=0.0,
    )
    c = build(s)
    c.service.sync()
    return c


def test_indexed_pipeline_resolves_stages_once_and_keeps_them(tmp_path, demo_root) -> None:
    ListingLikeAzure.calls, ListingLikeAzure.fail = 0, False
    c = _container(tmp_path, demo_root)
    svc = c.service
    assert not svc.index.get(svc.ref("ws:xf-pipe-finetune")).children  # as listed
    stages = svc.stages("ws:xf-pipe-finetune")
    assert len(stages) == 3
    assert any(a.stage for a in svc.artifacts("ws:xf-pipe-finetune"))  # stage files are listed
    assert len(svc.series("ws:xf-pipe-finetune")) > 0  # stage metrics are merged
    n = ListingLikeAzure.calls
    svc.stages("ws:xf-pipe-finetune")
    assert ListingLikeAzure.calls == n  # resolved once, then from the index
    svc.sync()  # a listing update must not drop the resolved stages
    assert len(svc.index.get(svc.ref("ws:xf-pipe-finetune")).children) == 3


def test_stage_resolution_failure_keeps_the_run_usable(tmp_path, demo_root) -> None:
    ListingLikeAzure.calls, ListingLikeAzure.fail = 0, True
    c = _container(tmp_path, demo_root)
    run = c.service.get_run("ws:xf-pipe-finetune")
    assert run.name and not run.children
    ListingLikeAzure.fail = False
