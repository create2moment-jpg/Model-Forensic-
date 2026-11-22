"""Parser / schema messages are classified, carry expected vs observed, and never hide behind a zero."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from model_forensic.analysis import evaljson, sequences
from model_forensic.application.inspect import inspect
from model_forensic.domain.messages import SEVERITIES, ParseMessage, counts, shape_of
from tests.unit.test_inspect_drift import v1_tree


def test_inspect_classifies_every_problem(tmp_path: Path) -> None:
    rep = inspect(v1_tree(tmp_path))
    by = {m.code: m for m in rep.messages}
    drift = by["eval.no_values"]
    assert drift.severity == "unsupported_schema" and drift.file and drift.file.endswith("measure=distance.json")
    assert drift.expected and drift.expected["keys"] == ["auc"]
    assert drift.observed == {"type": "object", "keys": ["area"], "key_count": 1, "value_types": ["dict"]}
    assert by["file.unreadable"].severity == "parse_failure" and "JSONDecodeError" in by["file.unreadable"].message
    pq = by.get("parquet.unreadable") or by.get("reader.unavailable")
    assert pq is not None and pq.blocking
    assert by["naming.unmapped"].severity == "recoverable_warning"
    assert by["naming.unmapped"].observed == {"examples": ["some_custom_counter"]}
    assert by["eval.unrecognised"].severity == "info" and not by["eval.unrecognised"].blocking
    d = rep.as_dict()
    assert d["message_counts"]["unsupported_schema"] >= 1 and d["message_counts"]["parse_failure"] >= 1
    assert all(m["severity"] in SEVERITIES for m in d["messages"]) and not d["ok"]
    json.dumps(d)  # serialisable for mf inspect --json and the remediation prompt


def test_missing_metrics_is_missing_data_not_zero(tmp_path: Path) -> None:
    (tmp_path / "x.txt").write_text("hello")
    rep = inspect(tmp_path)
    m = next(m for m in rep.messages if m.code == "metrics.none")
    assert m.severity == "missing_data" and rep.ok()  # missing evidence is said, not an error of the reader
    (tmp_path / "metrics.jsonl").write_text("garbage\n")
    rep = inspect(tmp_path)
    assert any(m.code == "metrics.empty" and m.severity == "missing_data" for m in rep.messages)


def test_unknown_parquet_schema_has_expected_and_observed(tmp_path: Path) -> None:
    pd = pytest.importorskip("pandas")
    p = tmp_path / "odd.parquet"
    pd.DataFrame({"scene": ["a", "b"], "score": [0.1, 0.2]}).to_parquet(p, index=False)
    t = sequences.load(p, remote_path="evaluation/odd.parquet")
    assert t.kind == "unknown" and t.message is not None
    assert t.message.severity == "unsupported_schema" and t.message.code == "parquet.schema"
    assert t.message.observed and t.message.observed["columns"] == ["scene", "score"]
    assert t.message.expected and t.message.expected["index"] == ["sequence_hash", "class_name"]
    s = sequences.summary(t)
    assert s["recognized"] is False and s["message"]["severity"] == "unsupported_schema" and "headline" not in s


def test_eval_reader_messages(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{nope")
    big = tmp_path / "big.json"
    big.write_text(json.dumps({"x": list(range(100))}))
    ev = evaljson.load(
        [("evaluation/Car/torch_metric_evaluation/bad.json", bad), ("evaluation/big.json", big)], max_bytes=50
    )
    codes = {m.code: m.severity for m in ev.messages}
    assert codes == {"eval.unreadable": "parse_failure", "eval.too_large": "recoverable_warning"}
    assert len(ev.notes) == 2 and ev.empty  # no class gets a value it did not have


def test_message_model() -> None:
    with pytest.raises(ValueError, match="severity"):
        ParseMessage("oops", "x", "y")  # type: ignore[arg-type]
    ms = [ParseMessage("info", "a", "b"), ParseMessage("critical", "c", "d", file="f.json")]
    assert counts(ms)["critical"] == 1 and ms[1].blocking and ms[1].text() == "f.json: d"
    assert shape_of([1, "a"]) == {"type": "array", "length": 2, "item_types": ["int", "str"]}


def test_sequences_api_says_when_there_are_none(container) -> None:  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from model_forensic.api.app import create_app

    api = TestClient(create_app(container))
    d = api.get("/api/v1/runs/local:lr-sweep-1e3/sequences").json()
    if not d["tables"]:
        assert d["messages"][0]["code"] == "sequences.none" and d["messages"][0]["severity"] == "missing_data"
