import math

import pytest

from model_forensic.domain.metrics import Aggregation, MetricKey, MetricSeries, Split
from model_forensic.domain.run import RunRef


def test_metric_key_roundtrip() -> None:
    k = MetricKey.parse("val/recall[range=60-inf,class=car]")
    assert k.split == Split.VAL and k.name == "recall"
    assert k.dims == (("class", "car"), ("range", "60-inf"))  # sorted
    assert MetricKey.parse(str(k)) == k
    assert k.base == MetricKey(name="recall", split=Split.VAL)


def test_metric_key_namespaced_name_without_split() -> None:
    k = MetricKey.parse("loss/cls")
    assert k.split == Split.UNKNOWN and k.name == "loss/cls"
    assert MetricKey.parse("train/loss/cls").name == "loss/cls"


def test_series_aggregate_ignores_nonfinite() -> None:
    s = MetricSeries(key=MetricKey(name="loss"), steps=(1, 2, 3), values=(1.0, float("nan"), 3.0))
    assert s.has_nonfinite
    assert s.aggregate(Aggregation.LAST) == 3.0
    assert s.aggregate(Aggregation.MEAN) == 2.0


def test_series_length_mismatch_rejected() -> None:
    with pytest.raises(ValueError):
        MetricSeries(key=MetricKey(name="x"), steps=(1, 2), values=(1.0,))


def test_empty_series_aggregate_is_nan() -> None:
    assert math.isnan(MetricSeries(key=MetricKey(name="x"), steps=(), values=()).aggregate())


def test_runref_parse() -> None:
    assert RunRef.parse("azureml:job_1") == RunRef(backend="azureml", run_id="job_1")
    assert RunRef.parse("job_1", "local") == RunRef(backend="local", run_id="job_1")
