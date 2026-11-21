"""Study workflow: observe → hypothesize → change architecture → verify → verdict."""

from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from model_forensic.api.app import create_app

B = "/api/v1/studies"


def test_full_study_workflow(container) -> None:
    c = TestClient(create_app(container))
    st = c.post(
        B,
        json={
            "title": "Why does radar not help?",
            "goal": "Decide how to fix the radar branch",
            "runs": ["cand-radar-8f"],
            "baseline": "baseline-cl4f-s0",
        },
    ).json()
    sid = st["id"]
    assert [r["run_id"] for r in st["runs"]] == ["baseline-cl4f-s0", "cand-radar-8f"]
    assert st["baseline"]["run_id"] == "baseline-cl4f-s0"

    # evidence board: per-run metrics vs baseline and config changes
    ev = c.get(f"{B}/{sid}/evidence").json()
    cand = next(r for r in ev["runs"] if r["run"]["name"] == "cand-radar-8f")
    assert any(d["metric"] == "val/recall[range=60-inf]" and d["verdict"] == "regressed" for d in cand["deltas"])
    assert any(ch["path"] == "model.radar_encoder.lr_mult" for ch in cand["config_changes"])

    # run the architecture analyses and turn a finding into a hypothesis
    res = c.post(
        "/api/v1/analyze", json={"run": "cand-radar-8f", "analyses": ["module_health", "modality_ablation"]}
    ).json()
    starved = next(f for r in res for f in r["findings"] if f["title"].startswith("radar learns"))
    c.put(f"{B}/{sid}/pins/{starved['id']}")
    st = c.post(
        f"{B}/{sid}/hypotheses",
        json={
            "statement": "The radar branch is starved by its 0.02 learning-rate multiplier",
            "proposed_change": "Set model.radar_encoder.lr_mult = 1.0 and add per-scale norm",
            "source": f"finding:{starved['id']}",
            "evidence": [
                {"kind": "finding", "ref": starved["id"], "label": starved["title"]},
                {
                    "kind": "metric",
                    "ref": "train/update_ratio[module=radar]",
                    "label": "radar update ratio 50× lower",
                    "run": {"backend": "local", "run_id": "cand-radar-8f"},
                },
            ],
            "reference_run": "cand-radar-8f",
        },
    ).json()
    hid = st["hypotheses"][0]["id"]
    assert st["pinned_findings"] == [starved["id"]] and len(st["hypotheses"][0]["evidence"]) == 2

    # verification needs a test run
    assert c.get(f"{B}/{sid}/hypotheses/{hid}/verify").status_code == 400
    st = c.patch(f"{B}/{sid}/hypotheses/{hid}", json={"test_run": "sam-radar-ms-fix"}).json()
    assert any(r["run_id"] == "sam-radar-ms-fix" for r in st["runs"])  # test run joins the study
    v = c.get(f"{B}/{sid}/hypotheses/{hid}/verify").json()
    far = next(d for d in v["comparison"]["deltas"] if d["metric"] == "val/recall[range=60-inf]")
    assert far["verdict"] == "improved"
    assert "improved" in v["suggestion"]

    st = c.patch(
        f"{B}/{sid}/hypotheses/{hid}",
        json={"status": "supported", "verdict": "Far-range recall +27% after fixing lr_mult"},
    ).json()
    assert st["hypotheses"][0]["status"] == "supported"
    st = c.post(f"{B}/{sid}/notes", json={"text": "Next: check whether 8 frames still hurt tracking."}).json()

    md = c.get(f"{B}/{sid}/export").text
    assert "[SUPPORTED] The radar branch is starved" in md and "**Tested by:** `local:sam-radar-ms-fix`" in md
    assert "Far-range recall +27%" in md and "## Timeline" in md and "check whether 8 frames" in md
    kinds = [e["kind"] for e in st["timeline"]]
    assert kinds[0] == "created" and "hypothesis" in kinds and "pinned" in kinds

    listing = c.get(B).json()
    assert listing[0]["id"] == sid and listing[0]["hypotheses"]["supported"] == 1


def test_attach_ai_investigation_to_study(container) -> None:
    c = TestClient(create_app(container))
    sid = c.post(B, json={"title": "s", "runs": ["cand-radar-8f"]}).json()["id"]
    inv = c.post("/api/v1/investigate", json={"run": "cand-radar-8f", "prompt_id": "study_health_check"}).json()
    st = c.post(f"{B}/{sid}/investigations", json={"investigation_id": inv["id"]}).json()
    assert st["investigations"] == [inv["id"]] and st["timeline"][-1]["kind"] == "ai"


def test_concurrent_edits_are_not_lost(container) -> None:
    c = TestClient(create_app(container))
    sid = c.post(B, json={"title": "s"}).json()["id"]
    with ThreadPoolExecutor(8) as ex:
        list(ex.map(lambda i: c.post(f"{B}/{sid}/notes", json={"text": f"note {i}"}), range(20)))
    assert len(c.get(f"{B}/{sid}").json()["notes"]) == 20


def test_validation_and_errors(container) -> None:
    c = TestClient(create_app(container))
    assert c.post(B, json={"title": " "}).status_code == 400
    assert c.post(B, json={"title": "x", "runs": ["does-not-exist"]}).status_code == 404
    sid = c.post(B, json={"title": "x"}).json()["id"]
    assert c.patch(f"{B}/{sid}/hypotheses/nope", json={"status": "rejected"}).status_code == 404
    assert c.delete(f"{B}/{sid}").json() == {"deleted": sid}
    assert c.get(f"{B}/{sid}").status_code == 404
