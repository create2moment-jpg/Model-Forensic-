"""Comparing a pipeline (a config file per stage) with a plain run: no false added / removed rows."""

from __future__ import annotations

from model_forensic.analysis.config_diff import ConfigDiffer, canonical, leaf
from model_forensic.domain.config import ConfigurationSnapshot
from model_forensic.domain.run import RunRef

A, B = RunRef(backend="b", run_id="plain"), RunRef(backend="b", run_id="pipe")


def snap(run: RunRef, source: str, data: dict) -> ConfigurationSnapshot:  # type: ignore[type-arg]
    return ConfigurationSnapshot(run=run, source=source, data=data)


def test_leaf_and_canonical() -> None:
    assert leaf("evaluate_finetune/config.yaml.data.batch_size") == "data.batch_size"
    assert leaf("evaluate_finetune/params.seed") == "params.seed"
    assert leaf("data.batch_size") == "data.batch_size"
    flat = {"s1/config.yaml.lr": 1, "s2/config.yaml.lr": 1, "s1/config.yaml.seed": 1, "s2/config.yaml.seed": 2}
    assert canonical(flat) == {"lr": 1, "s1/config.yaml.seed": 1, "s2/config.yaml.seed": 2}


def test_pipeline_vs_plain_run_reports_only_real_differences() -> None:
    plain = [snap(A, "config.yaml", {"data": {"batch_size": 16, "dataset": "x"}, "seed": 7})]
    pipe = [
        snap(B, "train/config.yaml", {"data": {"batch_size": 16, "dataset": "x"}, "seed": 7}),
        snap(B, "eval/config.yaml", {"data": {"batch_size": 16, "dataset": "y"}, "seed": 7}),
    ]
    d = ConfigDiffer().diff(A, plain, B, pipe)
    paths = {(c.path, c.kind.value) for c in d.changes}
    assert ("data.batch_size", "changed") not in paths and not any(
        "batch_size" in p for p, _ in paths
    )  # same everywhere
    assert not any(p == "seed" or p.endswith(".seed") for p, _ in paths)
    # the dataset differs between the pipeline's stages: compared per stage against the plain run's value
    assert paths == {("eval/config.yaml.data.dataset", "changed")}
