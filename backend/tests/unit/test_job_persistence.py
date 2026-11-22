"""Jobs survive a server restart: finished ones keep their result, running ones become 'interrupted' and can rerun."""

import time

from fastapi.testclient import TestClient

from model_forensic.api.app import create_app


def _wait(c: TestClient, jid: str) -> dict:
    for _ in range(200):
        j = c.get(f"/api/v1/jobs/{jid}").json()
        if j["status"] != "running":
            return j
        time.sleep(0.05)
    return j


def test_jobs_persist_and_interrupted_jobs_rerun(container) -> None:
    app = create_app(container)
    c = TestClient(app)
    done = c.post("/api/v1/runs/local:xf-pipe-finetune/download", json={"profile": "configs"}).json()["job_id"]
    assert _wait(c, done)["status"] == "done"
    # a job that is "running" when the server stops
    from model_forensic.api.app import Job

    ghost = Job(
        id="ghost00001",
        kind="download",
        title="configs · x",
        params={"run": "local:xf-pipe-finetune", "profile": "logs", "paths": None, "stages": None},
    )
    app.state.jobs["ghost00001"] = ghost
    app.state.jobs.flush()

    c2 = TestClient(create_app(container))  # "restart"
    jobs = {j["id"]: j for j in c2.get("/api/v1/jobs").json()}
    assert jobs[done]["status"] == "done"
    assert c2.get(f"/api/v1/jobs/{done}").json()["result"]["files"]
    g = c2.get("/api/v1/jobs/ghost00001").json()
    assert g["status"] == "interrupted" and "restarted" in g["error"] and g["rerunnable"]
    again = c2.post("/api/v1/jobs/ghost00001/rerun").json()["job_id"]
    assert _wait(c2, again)["status"] == "done"
    assert c2.post("/api/v1/jobs/nope/rerun").status_code == 404
