"""Log insights: patterns (built in, from the codebase, proposed by the LLM — grounded — or from the lines' numbers),
incremental extraction, and what the board shows."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from model_forensic.application.harness import brief
from model_forensic.application.log_insights import PatternStore, insights, propose
from model_forensic.container import Container, build
from model_forensic.domain.log_patterns import BUILTIN, LogPattern, check, from_line, template
from model_forensic.settings import BackendConfig, Settings


def test_builtin_patterns_are_valid_and_match_what_they_say() -> None:
    assert all(not check(p) for p in BUILTIN)
    by = {p.id: p.compiled() for p in BUILTIN}
    assert by["progress.step_loss"].search("step 50 loss=3.5030").groupdict() == {"step": "50", "loss": "3.5030"}
    assert by["error.nan"].search("step 4850 loss=nan")
    assert by["error.cuda_oom"].search("torch.OutOfMemoryError: CUDA out of memory. Tried to allocate 2 GiB")
    assert by["error.exception"].search("FloatingPointError: Loss is NaN, stopping training")
    assert (
        by["data.loading"].search("loading dataset nuscenes:v1.0-trainval,split=train").group("version")
        == "v1.0-trainval"
    )
    bad = LogPattern(id="x", title="x", regex="(", kind="event")
    assert "does not compile" in check(bad)[0]
    assert check(LogPattern(id="y", title="y", regex="abc", kind="series")) == [
        "a series pattern needs named groups (?P<name>…)"
    ]
    assert check(LogPattern(id="z", title="z", regex=r"loss=(?P<l>\d)", kind="series", example="no")) == [
        "the regex does not match its own example line"
    ]


def test_template_and_patterns_from_a_line() -> None:
    assert template("step 50 loss=3.5 at /mnt/a/b.bin") == "step <n> loss=<n> at <path>"
    p = from_line("[radar] fpn level 2 grad_norm=12.40 scale=0.02 step 50", "p")
    assert p is not None and p.kind == "series" and p.step_field == "step"
    assert p.compiled().search("[radar] fpn level 2 grad_norm=nan scale=0.02 step 900").groupdict() == {
        "grad_norm": "nan",
        "scale": "0.02",
        "step": "900",
    }
    assert from_line("nothing to see here", "q") is None


def test_the_demo_runs_log_on_the_board(container: Container) -> None:
    s = PatternStore(container.docs, container.knowledge.folder)
    ins = insights(container.service, s, "local:cand-radar-8f")
    facts = {(f["pattern"], f["field"]): f["value"] for f in ins["facts"]}
    assert facts[("env.torch", "torch")] == "2.3.1" and facts[("env.world_size", "world_size")] == "8"
    assert facts[("data.loading", "dataset")] == "nuscenes" and facts[("eval.result", "metric")] == "NDS"
    titles = [p["title"] for p in ins["problems"]]
    assert "Warning" in titles
    assert any(e["pattern"] == "checkpoint.saved" and e["count"] == 5 for e in ins["events"])
    assert {s_["key"] for s_ in ins["series"]} >= {"progress.step_loss:loss", "progress.data_time:data_time"}
    unc = [u["example"] for u in ins["uncovered"]]
    assert any("retry 1/3" in u for u in unc) and any("grad_norm=" in u for u in unc)
    # the NaN run: its problems lead with the failure
    nan = insights(container.service, s, "local:lr-sweep-1e3")
    assert nan["problems"][0]["severity"] == "problem"
    assert {p["pattern"] for p in nan["problems"]} >= {"error.nan", "error.traceback", "error.exception"}


def test_proposals_without_an_llm_then_accepted(container: Container) -> None:
    s = PatternStore(container.docs, container.knowledge.folder)
    out = propose(container.service, s, container.llm, "local:cand-radar-8f")  # the offline stand-in: deterministic
    assert out["model"] == "deterministic (no LLM connected)"
    by_example = {p["example"]: p for p in out["proposed"]}
    grad = next(p for e, p in by_example.items() if "grad_norm=" in e)
    retry = next(p for e, p in by_example.items() if "retry" in e)
    assert grad["kind"] == "series" and retry["kind"] == "event" and retry["severity"] == "attention"
    assert all(p["status"] == "proposed" for p in out["proposed"])
    before = insights(container.service, s, "local:cand-radar-8f")
    assert not any(x["label"] == "grad_norm" for x in before["series"])  # proposed: not applied yet
    s.decide(grad["id"], "active")
    s.decide(retry["id"], "active")
    after = insights(container.service, s, "local:cand-radar-8f")
    g = next(x for x in after["series"] if x["label"] == "grad_norm")
    assert g["x_label"] == "step" and len(g["y"]) == 20
    assert any(p["pattern"] == retry["id"] and p["count"] == 2 for p in after["problems"])
    assert len(after["uncovered"]) < len(before["uncovered"])
    assert propose(container.service, s, container.llm, "local:cand-radar-8f")["proposed"] == []  # nothing new to say


@dataclass
class _Resp:
    content: str


class FakeLLM:
    model = "fake-qwen"

    def complete(self, messages, tools=None, *, json_mode=False):  # type: ignore[no-untyped-def]
        lines = [json.loads(x)["line"] for x in messages[-1].content.splitlines()[1:]]
        real = next(x for x in lines if "retry" in x)
        return _Resp(json.dumps({"patterns": [
            {"id": "storage-retry", "title": "Storage read retried", "regex": r"timed out for shard (?P<shard>\d+), retry (?P<attempt>\d+)/(?P<of>\d+)",
             "category": "warning", "kind": "event", "severity": "attention", "example": real, "explain": "Slow storage."},
            {"id": "made-up", "title": "Invented", "regex": r"GPU (?P<n>\d+) is on fire", "category": "error", "kind": "event",
             "severity": "problem", "example": "GPU 3 is on fire"},
            {"id": "broken", "title": "Broken", "regex": "(unclosed", "category": "error", "kind": "event", "example": real},
        ]}))  # fmt: skip


def test_llm_proposals_are_grounded_in_the_runs_lines(container: Container) -> None:
    s = PatternStore(container.docs, container.knowledge.folder)
    out = propose(container.service, s, FakeLLM(), "local:cand-radar-8f")
    assert out["model"] == "fake-qwen"
    assert [p["id"] for p in out["proposed"]] == ["llm.storage-retry"]
    reasons = " | ".join(r["reason"] for r in out["rejected"])
    assert "not one of the run's lines" in reasons and "does not compile" in reasons


def test_patterns_from_the_codebase_arrive_proposed(container: Container) -> None:
    d = container.knowledge.folder / "log_patterns"
    d.mkdir(parents=True)
    (d / "detector.yaml").write_text(
        "codebase: detector\npatterns:\n"
        "  - id: code.radar.grad\n    title: Radar FPN gradient norm\n"
        "    regex: '\\[radar\\] fpn level (?P<level>\\d+) grad_norm=(?P<grad_norm>[\\d.]+)'\n"
        "    kind: series\n    category: progress\n    code_ref: radar/fpn.py:88\n"
    )
    s = PatternStore(container.docs, container.knowledge.folder)
    p = s.get("code.radar.grad")
    assert (p.source, p.status, p.code_ref) == ("code", "proposed", "radar/fpn.py:88")
    s.decide("code.radar.grad", "active")
    assert s.get("code.radar.grad").status == "active"  # the decision survives a re-read of the file
    ins = insights(container.service, s, "local:cand-radar-8f")
    assert any(x["pattern"] == "code.radar.grad" and x["label"] == "grad_norm" for x in ins["series"])
    assert ins["patterns"]["code"] == 1
    # the knowledge store does not mistake the folder for knowledge files
    assert not any("log_patterns" in str(pr) for pr in container.knowledge.load()[1])


def test_harness_brief_lists_the_unexplained_lines(container: Container) -> None:
    text = brief(container.service, container.knowledge, "local:cand-radar-8f", "log_patterns")
    assert "## Log lines seen in this run" in text and "retry 1/3" in text
    assert "log_patterns/<codebase>.yaml" in text and "code.<area>.<what>" in text


def test_incremental_a_growing_log_is_read_from_where_it_stopped(tmp_path: Path, demo_root: Path) -> None:
    root = tmp_path / "runs"
    shutil.copytree(demo_root / "sam-radar-live", root / "live-1")
    run = json.loads((root / "live-1" / "run.json").read_text())
    (root / "live-1" / "run.json").write_text(json.dumps({**run, "name": "live-1"}))
    log = root / "live-1" / "logs" / "std_log.txt"
    log.write_text("step 1 loss=2.0\nstep 2 loss=1.9\nstep 3 lo")
    c = build(Settings(data_dir=tmp_path / ".mf", backends=[BackendConfig(kind="local", name="local", options={"root": str(root)})],
                       artifact_min_free_gb=0.0, knowledge_dir=tmp_path / "k"))  # fmt: skip
    c.service.sync()
    s = PatternStore(c.docs, c.knowledge.folder)
    first = insights(c.service, s, "local:live-1")
    pts = next(x for x in first["series"] if x["key"] == "progress.step_loss:loss")
    assert pts["y"] == [2.0, 1.9]  # the unfinished third line waits
    read = first["files"][0]["read_bytes"]
    with log.open("a") as fh:
        fh.write("ss=1.8\nstep 4 loss=nan\n")
    second = insights(c.service, s, "local:live-1")
    pts = next(x for x in second["series"] if x["key"] == "progress.step_loss:loss")
    assert pts["y"][:3] == [2.0, 1.9, 1.8] and len(pts["y"]) == 4
    assert second["files"][0]["read_bytes"] > read
    assert any(p["pattern"] == "error.nan" for p in second["problems"])


def test_work_order_checks_a_patterns_file(tmp_path: Path) -> None:
    from model_forensic.application.workorders import check as check_order
    from model_forensic.application.workorders import load

    w = load()["WO-log-patterns"]
    good = tmp_path / "good.yaml"
    good.write_text(
        "patterns:\n  - id: code.train.step\n    title: Step\n    regex: 'step (?P<step>\\d+) loss=(?P<loss>[\\d.]+)'\n"
        "    kind: series\n    step_field: step\n    example: 'step 5 loss=1.2'\n    code_ref: train.py:10\n"
    )
    assert check_order(w, good) == []
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "patterns:\n  - id: code.x\n    title: X\n    regex: 'x=(?P<x>\\d+)'\n    kind: fact\n    example: 'y=1'\n"
    )
    errs = check_order(w, bad)
    assert any("does not match its own example" in e for e in errs) and any("no code_ref" in e for e in errs)
