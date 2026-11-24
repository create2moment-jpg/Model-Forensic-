"""Run groups: a candidate or baseline whose results are spread over several run ids, as one subject."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from model_forensic.application.run_groups import group_ref, members_of
from model_forensic.domain.run import RunRef
from model_forensic.shared.errors import ValidationError

TRAIN, PIPE, BASE = "local:lr-sweep-1e3", "local:xf-pipe-finetune", "local:baseline-cl4f-s0"
G = f"group:{TRAIN}+{PIPE}"


def test_reference_format() -> None:
    assert group_ref([TRAIN, PIPE]) == G and group_ref([TRAIN]) == TRAIN and group_ref([TRAIN, TRAIN]) == TRAIN
    assert members_of(f"{TRAIN}+{PIPE}") == [RunRef.parse(TRAIN), RunRef.parse(PIPE)]
    with pytest.raises(ValidationError):
        members_of(TRAIN)  # one run is not a group
    with pytest.raises(ValidationError):
        members_of(f"{TRAIN}+group:x")


def test_group_is_one_subject_built_from_its_members(container) -> None:  # type: ignore[no-untyped-def]
    from model_forensic.api.app import create_app

    api = TestClient(create_app(container))
    li = api.get(f"/api/v1/runs/{G}/light").json()
    run = li["run"]
    assert run["kind"] == "pipeline" and len(run["children"]) == 2 and " + " in run["display_name"]
    assert run["status"] == "failed"  # the worst member status
    assert container.service.index.get(RunRef.parse(G)) is None  # never written to the run index
    cat = api.get(f"/api/v1/runs/{G}/metric-catalog").json()
    assert len(cat["stages"]) == 2 and any(e["label"].startswith(cat["stages"][0] + " / ") for e in cat["entries"])
    # a member that is a pipeline contributes its stages' metrics and files too
    pipe_keys = {str(s.key) for s in container.service.series(PIPE)}
    assert pipe_keys & {e["metric"] for e in cat["entries"] if e["stage"] == cat["stages"][1]}
    assert any("/" in (a.stage or "") for a in container.service.artifacts(G))
    assert container.service.configs(G)
    cmp = api.get("/api/v1/compare", params={"baseline": BASE, "candidate": G})
    assert cmp.status_code == 200 and cmp.json()["deltas"]


def test_groups_on_both_sides_and_in_studies(container) -> None:  # type: ignore[no-untyped-def]
    from model_forensic.api.app import create_app

    api = TestClient(create_app(container))
    gb = f"group:{BASE}+local:baseline-cl4f-s1"
    cmp = api.get("/api/v1/compare", params={"baseline": gb, "candidate": G})
    assert cmp.status_code == 200
    st = api.post("/api/v1/studies", json={"title": "multi", "goal": "g", "runs": [G, TRAIN], "baseline": gb})
    assert st.status_code in (200, 201), st.text
    body = st.json()

    def ref(x):  # type: ignore[no-untyped-def]
        return x if isinstance(x, str) else f"{x['backend']}:{x['run_id']}"

    assert ref(body["baseline"]) == gb and G in [ref(r) for r in body["runs"]]


def test_group_files_download_from_the_member(container) -> None:  # type: ignore[no-untyped-def]
    arts = [a for a in container.service.artifacts(G) if (a.size_bytes or 0) < 100_000][:2]
    assert arts
    files, _ = container.service.artifact_store.fetch(container.service.source(RunRef.parse(G)), arts)
    assert len(files) == len(arts) and all(f.local_path.is_file() for f in files)


def test_bad_group_is_a_clear_error(container) -> None:  # type: ignore[no-untyped-def]
    from model_forensic.api.app import create_app

    api = TestClient(create_app(container))
    assert api.get(f"/api/v1/runs/group:{TRAIN}/light").status_code == 400


def test_a_pipeline_without_some_stages_stays_that_pipeline(container) -> None:  # type: ignore[no-untyped-def]
    """``group:<pipeline>!<stage>``: the pipeline (its name, its stage names) without the stages left out — expanded
    from the pipeline's own stage list, so it works when the stage runs are not in the index."""
    import pytest

    from model_forensic.application.run_groups import members_of, parse_members
    from model_forensic.shared.errors import ValidationError

    svc = container.service
    gid = "group:local:xf-pipe-finetune!evaluate_pretrain"
    run = svc.get_run(gid)
    assert run.display_name == "sparse_detection_temporal finetune (without evaluate_pretrain)"
    assert [n for n, _ in svc.stages(gid)] == ["train_finetune", "evaluate_finetune"]
    assert svc.series(gid) and svc.compare("local:xf-online-baseline", gid).deltas
    # two stages out, and mixed with another run
    two = svc.get_run("group:local:xf-pipe-finetune!evaluate_pretrain!evaluate_finetune")
    assert [str(c) for c in two.children] == ["local:xf-pipe-finetune-train"]
    mixed = svc.get_run("group:local:xf-pipe-finetune!evaluate_pretrain+local:baseline-cl4f-s0")
    assert len(mixed.children) == 3
    assert parse_members("local:a!x!y+local:b") == [
        (members_of("local:a+local:b")[0], ("x", "y")),
        (members_of("local:a+local:b")[1], ()),
    ]
    with pytest.raises(ValidationError):
        svc.get_run("group:local:xf-pipe-finetune!no_such_stage")
    with pytest.raises(ValidationError):
        svc.get_run("group:local:xf-pipe-finetune!train_finetune!evaluate_pretrain!evaluate_finetune")
    with pytest.raises(ValidationError):
        members_of("local:xf-pipe-finetune")  # one plain run is not a group
