"""Every RunSource implementation must satisfy the same behavioural contract."""

from types import SimpleNamespace

import pytest

from model_forensic.application.ports import ListRunsQuery, RunSource
from model_forensic.domain.metrics import MetricKey, Split
from model_forensic.infrastructure.connectors.azureml.connector import AzureMLRunSource, RateLimiter
from model_forensic.infrastructure.connectors.local.connector import LocalRunSource
from model_forensic.shared.errors import NotFoundError
from model_forensic.shared.retry import RetryPolicy

ns = SimpleNamespace


class _NotFound(Exception):
    pass


_NotFound.__name__ = "ResourceNotFoundError"


class FakeJobs:
    def __init__(self, jobs):
        self._jobs = {j.name: j for j in jobs}

    def list(self, parent_job_name=None):
        return iter(self._jobs.values())

    def get(self, name):
        if name not in self._jobs:
            raise _NotFound(f"{name} not found")
        return self._jobs[name]


class FakeMlflow:
    def __init__(self):
        self.metrics = {"r1": {"train/loss": [(i, 2.0 - i * 0.1) for i in range(10)], "val/NDS": [(9, 0.61)]}}
        self.files = {"r1": {"config.yaml": b"optimizer:\n  lr: 0.001\n", "user_logs/std_log.txt": b"hello\nworld\n"}}

    def get_run(self, rid):
        if rid not in self.metrics:
            raise _NotFound("run not found")
        return ns(data=ns(metrics={k: v[-1][1] for k, v in self.metrics[rid].items()}, tags={}))

    def get_metric_history(self, rid, key):
        return [ns(step=s, value=v, timestamp=1_700_000_000_000 + s) for s, v in self.metrics[rid][key]]

    def list_artifacts(self, rid, path=None):
        files = self.files.get(rid, {})
        if path is None:
            tops = sorted({p.split("/")[0] for p in files})
            return [
                ns(
                    path=t,
                    is_dir="/" in next(p for p in files if p.startswith(t)) and not t.endswith(".yaml"),
                    file_size=None if "." not in t else len(files[t]),
                )
                for t in tops
            ]
        return [ns(path=p, is_dir=False, file_size=len(b)) for p, b in files.items() if p.startswith(path + "/")]

    def download_artifacts(self, rid, path, dst):
        import os

        out = os.path.join(dst, os.path.basename(path))
        with open(out, "wb") as fh:
            fh.write(self.files[rid][path])
        return out


def azure_source() -> AzureMLRunSource:
    job = ns(
        name="r1",
        display_name="r1",
        experiment_name="e",
        type="command",
        status="Completed",
        tags={},
        properties={},
        inputs={},
        outputs={},
        creation_context=None,
        compute="c",
        resources=None,
        parameters={"lr": "0.001"},
        command="python t.py",
        environment=None,
        description=None,
    )
    return AzureMLRunSource(
        "azureml",
        ns(jobs=FakeJobs([job])),
        FakeMlflow(),
        rate=RateLimiter(1000, 1000),
        policy=RetryPolicy(max_attempts=1),
    )


@pytest.fixture(params=["local", "azureml"])
def source_and_id(request, demo_root) -> tuple[RunSource, str]:
    if request.param == "local":
        return LocalRunSource("local", demo_root), "baseline-cl4f-s0"
    return azure_source(), "r1"


def test_is_run_source(source_and_id) -> None:
    assert isinstance(source_and_id[0], RunSource)


def test_list_and_get(source_and_id) -> None:
    src, rid = source_and_id
    page = src.list_runs(ListRunsQuery(page_size=500))
    assert rid in {r.ref.run_id for r in page.items}
    run = src.get_run(rid)
    assert run.ref.backend == src.name and run.ref.run_id == rid


def test_unknown_run_raises_not_found(source_and_id) -> None:
    with pytest.raises(NotFoundError):
        source_and_id[0].get_run("does-not-exist")


def test_metrics_are_split_aware_and_sorted(source_and_id) -> None:
    src, rid = source_and_id
    series = src.get_metric_series(rid)
    loss = next(s for s in series if s.key == MetricKey(name="loss", split=Split.TRAIN))
    assert list(loss.steps) == sorted(loss.steps) and len(loss) >= 10
    assert MetricKey(name="loss", split=Split.TRAIN) in src.list_metric_keys(rid)
    only = src.get_metric_series(rid, [MetricKey(name="loss", split=Split.TRAIN)])
    assert [str(s.key) for s in only] == ["train/loss"]


def test_configs_and_artifacts(source_and_id) -> None:
    src, rid = source_and_id
    cfgs = src.get_configs(rid)
    assert any(c.data.get("optimizer", {}).get("lr") for c in cfgs)
    arts = src.list_artifacts(rid)
    cfg = next(a for a in arts if a.path.endswith("config.yaml"))
    assert b"".join(src.open_artifact(rid, cfg.path)).startswith(b"optimizer") or b"optimizer" in b"".join(
        src.open_artifact(rid, cfg.path)
    )
    data = b"".join(src.open_artifact(rid, cfg.path, offset=3))
    assert data == b"".join(src.open_artifact(rid, cfg.path))[3:]


def test_read_log(source_and_id) -> None:
    src, rid = source_and_id
    assert src.read_log(rid)
