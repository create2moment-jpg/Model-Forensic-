"""Per-sequence evaluation parquets: schema recognition, summaries, worst rows and the drill-down analysis."""

import json
from pathlib import Path

import numpy as np
import pytest

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")

from model_forensic.analysis import sequences as sq  # noqa: E402
from model_forensic.analysis.base import AnalysisContext  # noqa: E402
from model_forensic.analysis.plugins.pipeline import SEQUENCE_DRILLDOWN, SequenceParams  # noqa: E402
from tests.unit.test_architecture_analyses import data  # noqa: E402

META = {b"pandas": json.dumps({"index_columns": ["sequence_hash", "class_name"], "columns": []}).encode()}


def write(path: Path, cols: dict, meta: bool = True) -> Path:
    t = pa.table(cols)
    pq.write_table(t.replace_schema_metadata(META) if meta else t, path)
    return path


def det_cols(n_seq: int = 12, ped_fp: float = 2.0, bad: int = 3) -> dict:
    rng = np.random.default_rng(0)
    cols: dict = {k: [] for k in ("sequence_hash", "class_name", "AP", "num_gt", "num_tp", "num_fp", "num_fn")}
    for i in range(n_seq):
        for c in ("Car", "Ped", "Animal"):
            ap = (0.1 if i < bad else 0.7) + rng.normal(0, 0.01)
            gt = 100
            tp = int(gt * ap)
            fn = gt - tp if i >= bad else 90
            fp = int(gt * (ped_fp if c == "Ped" else 0.2))
            for k, v in zip(cols, (f"s{i:02d}", c, ap, gt, tp, fp, fn), strict=True):
                cols[k].append(v)
    return cols


def test_schema_recognition(tmp_path: Path) -> None:
    assert sq.load(write(tmp_path / "d.parquet", det_cols())).kind == "detection"
    trk = {"sequence_hash": ["a"], "class_name": ["Car"], "assa_alpha": [0.5], "idf1": [0.4], "mota": [0.3]}
    assert sq.load(write(tmp_path / "t.parquet", trk)).kind == "tracking"
    assert sq.load(write(tmp_path / "t2.parquet", trk, meta=False)).kind == "tracking"  # index stored as columns
    other = sq.load(write(tmp_path / "o.parquet", {"x": [1.0]}))
    assert other.kind == "unknown" and "unrecognised" in other.note
    (tmp_path / "bad.parquet").write_bytes(b"not parquet")
    assert "not readable" in sq.load(tmp_path / "bad.parquet").note
    assert "exceeds" in sq.load(tmp_path / "d.parquet", max_bytes=10).note


def test_summary_and_worst(tmp_path: Path) -> None:
    t = sq.load(write(tmp_path / "d.parquet", det_cols()))
    s = sq.summary(t)
    ped = next(r for r in s["per_class"] if r["class"] == "Ped")
    assert s["sequences"] == 12 and ped["fp_per_gt"] == pytest.approx(2.0) and 0 < ped["precision"] < 0.5
    w = sq.worst(t, "AP", 3, cls="Car")
    assert [r["sequence"] for r in w] == ["s00", "s01", "s02"] or {r["sequence"] for r in w} == {"s00", "s01", "s02"}


def test_drilldown_findings_and_stage_comparison(tmp_path: Path) -> None:
    pre = sq.load(write(tmp_path / "pre.parquet", det_cols(ped_fp=0.3, bad=0)), stage="evaluate_pretrain")
    ft = sq.load(write(tmp_path / "ft.parquet", det_cols()), stage="evaluate_finetune")
    target = data()
    target = type(target)(run=target.run, series={}, tables=[pre, ft])
    res = SEQUENCE_DRILLDOWN.run(AnalysisContext(target=target), SequenceParams(deprioritized=("animal",)))
    titles = [f.title for f in res.findings]
    assert "Precision-poor: Ped" in titles
    assert any(x.startswith("Misses concentrated in a few sequences") for x in titles)
    assert "3 scene(s) fail for every class" in titles
    assert any("regressed vs evaluate_pretrain" in x for x in titles)
    assert not any("Animal" in x for x in titles)
    skipped = SEQUENCE_DRILLDOWN.run(AnalysisContext(target=data()), SequenceParams())
    assert skipped.skipped_reason


def test_demo_pipeline_tables_via_service_and_api(container) -> None:
    from fastapi.testclient import TestClient

    from model_forensic.api.app import create_app

    tables = container.service.eval_tables("local:xf-pipe-finetune")
    assert {t.kind for t in tables} == {"detection", "tracking"} and {t.stage for t in tables} == {
        "evaluate_pretrain",
        "evaluate_finetune",
    }
    res = {r.analysis: r for r in container.service.analyze("local:xf-pipe-finetune", analyses=["sequence_drilldown"])}
    sd = res["sequence_drilldown"]
    assert any(f.title.startswith("Precision-poor: Pedestrian") for f in sd.findings), (sd.skipped_reason, sd.notes)
    body = TestClient(create_app(container)).get("/api/v1/runs/local:xf-pipe-finetune/sequences").json()
    sel = body["tables"][body["selected"]]
    assert sel["kind"] == "detection" and sel["stage"] == "evaluate_finetune" and body["worst"]
