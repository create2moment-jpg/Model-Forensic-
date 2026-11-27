"""Pipeline stages in the index: resolved during sync (within a budget) and when a pipeline is opened, so a stage
opened, compared or listed on its own has its name, status and pipeline — not an "unknown" id."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from model_forensic.application.ports import ListRunsQuery, Page
from model_forensic.container import Container
from model_forensic.domain.run import Run, RunKind, RunRef, RunStatus

T0 = datetime(2026, 11, 1, tzinfo=UTC)


class FakeAzure:
    """A workspace whose listing has pipelines without their stages (as the Azure job listing does)."""

    name = "fakeaz"
    index_configs_on_sync = False

    def __init__(self, pipes: int = 3) -> None:
        self.pipes = [f"pipe{i}" for i in range(pipes)]
        self.resolved: list[str] = []

    def _pipe(self, pid: str, children: bool) -> Run:
        ref = RunRef(backend=self.name, run_id=pid)
        kids = [RunRef(backend=self.name, run_id=f"{pid}-{s}") for s in ("train", "eval")]
        return Run(
            ref=ref, name=pid, display_name=pid, experiment="exp", kind=RunKind.PIPELINE, status=RunStatus.COMPLETED,
            created_at=T0, children=kids if children else [],
            backend_extras={"stages": ",".join(f"{k.run_id}={k.run_id.split('-')[-1]}" for k in kids)} if children else {},
        )  # fmt: skip

    def list_runs(self, query: ListRunsQuery) -> Page:
        return Page(items=[self._pipe(p, False) for p in self.pipes])

    def get_run(self, run_id: str) -> Run:
        if run_id in self.pipes:
            self.resolved.append(run_id)
            return self._pipe(run_id, True)
        raise KeyError(run_id)  # the job API does not return stages

    def stage_runs(self, run_id: str) -> list[Run]:
        pipe = self._pipe(run_id, True)
        return [
            Run(
                ref=k,
                name=k.run_id.split("-")[-1],
                experiment="exp",
                status=RunStatus.COMPLETED,
                created_at=T0,
                parent=pipe.ref,
                kind=RunKind.TRAINING,
            )
            for k in pipe.children
        ]

    def __getattr__(self, name: str) -> Any:  # metrics, files, …: not used here
        raise AttributeError(name)


def test_sync_indexes_stages_within_a_budget_and_keeps_them(container: Container) -> None:
    svc = container.service
    az = FakeAzure(pipes=3)
    svc.sources[az.name] = az  # type: ignore[assignment]
    svc.sync_stage_budget = 2
    svc.sync(az.name)
    assert sorted(az.resolved) == ["pipe0", "pipe1"]  # the budget: the third resolves when it is opened
    st = svc.index.get(RunRef(backend=az.name, run_id="pipe0-train"))
    assert st is not None and st.name == "train" and st.parent is not None and st.parent.run_id == "pipe0"
    assert svc.index.get(RunRef(backend=az.name, run_id="pipe2-train")) is None
    # a later sync keeps the stages it knows (no second call) and resolves the one left over
    az.resolved.clear()
    svc.sync(az.name)
    assert az.resolved == ["pipe2"]
    assert [c.run_id for c in svc.get_run("fakeaz:pipe0").children] == ["pipe0-train", "pipe0-eval"]
    assert svc.index.get(RunRef(backend=az.name, run_id="pipe2-eval")) is not None
    # a stage opened on its own: from the index, with its name and pipeline
    stage = svc.get_run("fakeaz:pipe1-eval")
    assert stage.name == "eval" and stage.parent is not None and stage.parent.run_id == "pipe1"


def test_opening_a_pipeline_indexes_its_stages(container: Container) -> None:
    svc = container.service
    az = FakeAzure(pipes=1)
    svc.sources[az.name] = az  # type: ignore[assignment]
    svc.sync_stage_budget = 0  # nothing during sync
    svc.sync(az.name)
    assert svc.index.get(RunRef(backend=az.name, run_id="pipe0-train")) is None
    svc.get_run("fakeaz:pipe0")  # opening it resolves the stages
    assert svc.index.get(RunRef(backend=az.name, run_id="pipe0-train")) is not None
