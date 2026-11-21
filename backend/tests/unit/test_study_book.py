"""Study book: run ids in, book out — the demo reproduces the ruler-artifact verdict deterministically."""

import json

from fastapi.testclient import TestClient

from model_forensic.api.app import create_app
from model_forensic.reports import svgplot


def make_study(container, title: str = "GT finetune vs online <baseline>"):
    return container.studies.create(
        title, goal="Is <b>GT</b> behind?", runs=["local:xf-pipe-finetune"], baseline="local:xf-online-baseline"
    )


def test_book_reproduces_verdict_and_chapters(container) -> None:
    st = make_study(container)
    html_path, summary_path = container.study_book(st.id)
    html = html_path.read_text()
    s = json.loads(summary_path.read_text())
    assert s["verdict"].startswith("The headline gap is a ruler artifact")
    assert s["chapters"][:3] == [
        "Preface — the case file",
        "Pitfall checks (P1–P8)",
        "The shared ruler — per-class, ruler-free",
    ]
    cand = next(r for r in s["runs"] if r["role"] == "candidate")
    assert cand["checks"]["P3"]["status"] == "FAIL" and cand["checks"]["P5"]["status"] == "FAIL"
    assert cand["stages"] == ["train_finetune", "evaluate_pretrain", "evaluate_finetune"]
    for needle in ("The loss ledger", "Where the model fails", "Next steps", '<svg class="chart"', "RULER MISMATCH"):
        assert needle in html
    assert "<b>GT</b>" not in html and "&lt;b&gt;GT&lt;/b&gt;" in html  # study text is escaped
    assert "<baseline>" not in html


def test_book_api_job_and_csp(container) -> None:
    st = make_study(container, "api book")
    c = TestClient(create_app(container))
    assert c.get(f"/api/v1/studies/{st.id}/book").status_code == 404
    job = c.post(f"/api/v1/studies/{st.id}/book").json()["job_id"]
    import time

    for _ in range(100):
        j = c.get(f"/api/v1/jobs/{job}").json()
        if j["status"] in {"done", "failed"}:
            break
        time.sleep(0.1)
    assert j["status"] == "done", j
    r = c.get(f"/api/v1/studies/{st.id}/book")
    assert r.status_code == 200 and "default-src 'none'" in r.headers["content-security-policy"]


def test_svg_helpers() -> None:
    assert svgplot.line_chart([("a<b>", [0, 1], [1, 2])], "t&", "x", "y").startswith("<svg")
    assert "a&lt;b&gt;" in svgplot.line_chart([("a<b>", [0, 1], [1, 2])], "t", "x", "y")
    assert svgplot.bar_chart([], [], "t", "y") == "" and svgplot.line_chart([], "t", "x", "y") == ""
    assert "<rect" in svgplot.bar_chart(["a", "b"], [("g", [0.2, -0.1])], "t", "y", percent=True)
