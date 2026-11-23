"""Local runs: TensorBoard event files (no TensorFlow) and evaluation result JSONs become metrics."""

from __future__ import annotations

import json
import os
import struct
import time
from pathlib import Path

from model_forensic.application.ports import ListRunsQuery
from model_forensic.infrastructure.connectors.folder import evalfiles, tfevents
from model_forensic.infrastructure.connectors.folder.connector import FolderRunSource, preview


def _varint(n: int) -> bytes:
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def _ld(fn: int, data: bytes) -> bytes:
    return _varint(fn << 3 | 2) + _varint(len(data)) + data


def event(step: int, tag: str, value: float, tensor: bool = False) -> bytes:
    if tensor:  # TF2: a scalar tensor (DT_FLOAT, float_val)
        t = _varint(1 << 3 | 0) + _varint(1) + _varint(5 << 3 | 5) + struct.pack("<f", value)
        val = _ld(1, tag.encode()) + _ld(8, t)
    else:  # PyTorch / Lightning: simple_value
        val = _ld(1, tag.encode()) + _varint(2 << 3 | 5) + struct.pack("<f", value)
    ev = (
        _varint(1 << 3 | 1)
        + struct.pack("<d", 1700000000.0 + step)
        + _varint(2 << 3 | 0)
        + _varint(step)
        + _ld(5, _ld(1, val))
    )
    return struct.pack("<Q", len(ev)) + b"\0" * 4 + ev + b"\0" * 4


def write_events(path: Path, records: list[bytes], torn: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = b"".join(records)
    path.write_bytes(data + (records[0][:10] if torn else b""))  # a record still being written at the end


def test_tfevents_reader(tmp_path: Path) -> None:
    f = tmp_path / "events.out.tfevents.1.host"
    write_events(
        f,
        [event(0, "train/loss", 2.0), event(10, "train/loss", 1.5), event(10, "val/mAP", 0.31, tensor=True)],
        torn=True,
    )
    got = list(tfevents.read_scalars(f))
    assert [(t, s, round(v, 3)) for t, s, v, _ in got] == [
        ("train/loss", 0.0, 2.0),
        ("train/loss", 10.0, 1.5),
        ("val/mAP", 10.0, 0.31),
    ]


def test_eval_file_rules() -> None:
    assert evalfiles.is_eval_json("eval/results.json") and evalfiles.is_eval_json("metrics_summary.json")
    assert evalfiles.is_eval_json("outputs/eval_epoch_3.json") and not evalfiles.is_eval_json("config.json")
    assert not evalfiles.is_eval_json("hparams_metric.json") and not evalfiles.is_eval_json("data.json")
    assert evalfiles.group_and_step("eval/eval_epoch_12.json") == ("eval_epoch", 12.0)
    assert evalfiles.group_and_step("metrics_summary.json") == ("metrics_summary", None)


def test_a_local_run_with_tensorboard_and_eval_files(tmp_path: Path) -> None:
    run = tmp_path / "tb-run"
    # two writer folders (train / val, the split comes from the folder) and an older launch that is ignored
    write_events(
        run / "lightning_logs" / "version_1" / "train" / "events.out.tfevents.2.h",
        [event(i * 10, "loss", 2.0 - i * 0.1) for i in range(10)],
    )
    write_events(
        run / "lightning_logs" / "version_1" / "val" / "events.out.tfevents.3.h",
        [event(i * 50, "mAP", 0.2 + i * 0.05, tensor=True) for i in range(3)],
    )
    write_events(run / "lightning_logs" / "version_0" / "events.out.tfevents.1.h", [event(0, "train/loss", 99.0)])
    for e, ap in ((1, 0.30), (2, 0.36), (3, 0.41)):
        (run / "eval").mkdir(exist_ok=True)
        (run / "eval" / f"eval_epoch_{e}.json").write_text(
            json.dumps({"mAP": ap, "per_class": {"car": {"AP": ap + 0.1}}, "ok": True})
        )
    (run / "eval" / "test_summary.json").write_text(json.dumps({"NDS": 0.5}))
    old = time.time() - 3600
    for f in run.rglob("*"):
        os.utime(f, (old, old))
    os.utime(run / "lightning_logs" / "version_0" / "events.out.tfevents.1.h", (old - 100, old - 100))
    pv = preview(run)
    assert pv["ok"] and pv["runs"][0]["tensorboard"] == 3 and pv["runs"][0]["evals"]  # type: ignore[index]
    src = FolderRunSource("local-runs", [run])
    r = src.list_runs(ListRunsQuery(page_size=10)).items[0]
    assert r.status.value == "completed" and r.kind.value == "training"
    s = {str(x.key): x for x in src.get_metric_series(r.ref.run_id)}
    assert (
        s["train/loss"].values[0] == 2.0 and len(s["train/loss"].values) == 10
    )  # newest launch only, split from folder
    assert len(s["val/mAP"].values) == 3 or any(k.startswith("val/mAP") for k in s)
    curve = next(v for k, v in s.items() if k.startswith("val/mAP[") and "file=eval_epoch" in k)
    assert curve.steps == (1.0, 2.0, 3.0) and curve.values == (0.30, 0.36, 0.41)  # eval over checkpoints
    assert any(k.startswith("val/AP[") and "scope=per_class.car" in k for k in s)
    assert any(k.startswith("test/NDS") for k in s)  # a test summary
    assert "TensorBoard" in s["train/loss"].source and "evaluation file" in s["train/loss"].source
