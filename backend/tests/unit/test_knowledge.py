"""Knowledge block: schema, matching (setup → conditions), effects with attribution, store, import as proposed, API."""

from pathlib import Path

from fastapi.testclient import TestClient

from model_forensic.api.app import create_app
from model_forensic.knowledge import KnowledgeEntry, KnowledgeStore, RunContext, resolve
from model_forensic.knowledge.store import parse

EXAMPLE = Path(__file__).resolve().parents[3] / "config" / "knowledge" / "knowledge.example.yaml"


def entry(**kw) -> KnowledgeEntry:  # type: ignore[no-untyped-def]
    return KnowledgeEntry.model_validate({"id": "K-x", "statement": "something", **kw})


def test_example_file_is_valid() -> None:
    entries, errors = parse(EXAMPLE.read_text())
    assert not errors and {"K-manual-labels-no-velocity", "K-animal-deprioritized"} <= {e.id for e in entries}


def test_conditions_and_effects() -> None:
    manual = entry(
        id="K-manual",
        when={"dataset_contains": ["manual"], "stage_contains": ["finetune"]},
        effects={"ignore_metrics": ["*velocity*", "ave"], "caveat": "no velocity GT"},
    )
    animal = entry(id="K-animal", effects={"ignore_classes": ["Animal"]})
    setup = entry(id="S-1v5r", kind="setup", when={"config": {"*num_radars": "5"}}, effects={"setup": "1v5r"})
    only_1v5r = entry(id="K-1v5r", when={"setup": ["1v5r"]}, effects={"caveat": "1v5r note"})
    retired = entry(id="K-old", status="retired", effects={"ignore_classes": ["Truck"]})
    kb = [manual, animal, setup, only_1v5r, retired]
    ft = RunContext(
        name="run gt", stage="train_finetune", config={"model.num_radars": 5}, datasets=["gt_manual_labels_v3"]
    )
    a = resolve(kb, ft)
    assert a.setups == ["1v5r"] and {e.id for e in a.entries} == {"S-1v5r", "K-manual", "K-animal", "K-1v5r"}
    assert a.metric_reason("val/match_velocity_rsme[class=Car]") == "K-manual"
    assert a.metric_reason("val/ave[class=Car]") == "K-manual" and a.metric_reason("val/ate[class=Car]") is None
    assert a.class_reason("animal") == "K-animal" and a.class_reason("Truck") is None  # retired is ignored
    pre = resolve(
        kb, RunContext(name="run", stage="train_pretrain", config={"model.num_radars": 1}, datasets=["autolabels_v19"])
    )
    assert {e.id for e in pre.entries} == {"K-animal"}


def test_store_upsert_import_and_validation(tmp_path) -> None:
    st = KnowledgeStore(tmp_path / "k")
    st.upsert(entry(id="K-a", effects={"ignore_classes": ["Animal"]}))
    added, errors = st.import_text(
        "entries:\n  - {id: K-a, statement: overwrite attempt}\n  - {id: K-b, statement: from the codebase, "
        "source: {code_refs: ['losses/x.py:12']}}\n",
        via="harness:loss_composition",
    )
    assert [e.id for e in added] == ["K-b"] and "not overwritten" in errors[0]
    entries, _ = st.load()
    b = next(e for e in entries if e.id == "K-b")
    assert b.status == "proposed" and b.source.via == "harness:loss_composition"
    assert resolve(entries, RunContext()).entries == [next(e for e in entries if e.id == "K-a")]  # proposed ≠ applied
    bad, errs = st.import_text("entries:\n  - {id: 'bad id!', statement: x}\n")
    assert not bad and "id" in errs[0]
    assert st.delete("K-b") and not st.delete("K-zz")


def test_api_and_run_knowledge(container) -> None:
    c = TestClient(create_app(container))
    r = c.put(
        "/api/v1/knowledge/K-animal",
        json={"kind": "constraint", "statement": "Animal ignored", "effects": {"ignore_classes": ["Animal"]}},
    )
    assert r.status_code == 200 and r.json()["source"]["via"] == "ui"
    imp = c.post(
        "/api/v1/knowledge/import",
        json={
            "yaml": "- {id: K-gt-vel, statement: GT has no velocity, "
            "when: {stage_contains: [finetune]}, "
            "effects: {ignore_metrics: ['*velocity*']}}"
        },
    ).json()
    assert imp["added"] == ["K-gt-vel"]
    rk = c.get("/api/v1/runs/local:xf-pipe-finetune/knowledge").json()
    assert {e["id"] for e in rk["run"]["entries"]} == {"K-animal"}  # the proposed entry is not applied yet
    acc = c.put(
        "/api/v1/knowledge/K-gt-vel", json={**c.get("/api/v1/knowledge").json()["entries"][1], "status": "active"}
    )
    assert acc.json()["status"] == "active"
    rk = c.get("/api/v1/runs/local:xf-pipe-finetune/knowledge").json()
    assert "K-gt-vel" in {e["id"] for e in rk["train_finetune"]["entries"]}
    assert "K-gt-vel" not in {e["id"] for e in rk["evaluate_pretrain"]["entries"]}
    assert c.put("/api/v1/knowledge/K-bad", json={"statement": ""}).status_code == 422
    assert c.delete("/api/v1/knowledge/K-gt-vel").json() == {"deleted": "K-gt-vel"}


def _seed(container) -> None:  # type: ignore[no-untyped-def]
    container.knowledge.upsert(
        entry(id="K-animal", kind="constraint", effects={"ignore_classes": ["Animal"], "caveat": "Animal is set aside"})
    )
    container.knowledge.upsert(entry(id="K-vel", kind="constraint", effects={"ignore_metrics": ["*velocity*"]}))


def test_findings_are_marked_not_deleted(container) -> None:
    _seed(container)
    c = TestClient(create_app(container))
    res = c.post(
        "/api/v1/analyze", json={"run": "local:xf-pipe-finetune", "baseline": "local:xf-online-baseline"}
    ).json()
    fs = [f for r in res for f in r["findings"]]
    aside = [f for f in fs if f.get("set_aside")]
    assert aside and all(f["set_aside"]["entry"] in {"K-animal", "K-vel"} for f in aside)
    assert any("Animal" in (f["title"] + f["statement"]) for f in aside)
    assert len(fs) > len(aside)  # the rest is untouched


def test_check_my_run_and_precision_recall_apply_knowledge(container) -> None:
    _seed(container)
    c = TestClient(create_app(container))
    chk = c.get("/api/v1/check", params={"run": "local:xf-pipe-finetune"}).json()
    assert all("Animal" not in card["title"] for card in chk["cards"])
    assert chk["knowledge"] and chk["knowledge"][0]["entry"] == "K-animal"
    pr = c.get("/api/v1/runs/local:xf-pipe-finetune/precision-recall").json()
    assert pr["classes"][-1] == "Animal" and pr["set_aside_classes"] == {"Animal": "K-animal"}
    ov = c.get("/api/v1/runs/local:xf-pipe-finetune").json()
    assert {e["id"] for e in ov["knowledge"]["entries"]} == {"K-animal", "K-vel"}
