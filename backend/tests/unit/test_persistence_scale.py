"""Persistence at scale: hundreds of studies list from summaries (filtered, searched, paged), old stores are
backfilled once, the database runs in WAL mode and concurrent readers / writers do not fail."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

from model_forensic.domain.study import Study, StudyEvent
from model_forensic.infrastructure.persistence.sqlite_store import DocumentStore, SqliteStudyStore

N = 600


def _study(i: int) -> Study:
    return Study(
        id=f"st-{i:04d}",
        title=f"Study {i} {'radar' if i % 3 == 0 else 'camera'} ablation",
        goal="Does the change help? " + "detail " * 50,
        owner="alice" if i % 2 else "bob",
        status="closed" if i % 5 == 0 else "open",
        timeline=tuple(StudyEvent(kind="note", text="x" * 200) for _ in range(150)),  # big bodies
    )


def test_hundreds_of_studies_list_fast_from_summaries(tmp_path: Path) -> None:
    docs = DocumentStore(tmp_path / "mf.sqlite")
    store = SqliteStudyStore(docs)
    for i in range(N):
        store.save(_study(i))
    t0 = time.monotonic()
    items, total = store.summaries(limit=50)
    took = time.monotonic() - t0
    assert total == N and len(items) == 50
    assert took < 0.3, took  # never parses the (large) study bodies
    assert all(it["status"] == "open" for it in items)  # open first
    assert "timeline" not in items[0] and isinstance(items[0]["hypotheses"], dict)
    closed, n_closed = store.summaries(status="closed", limit=1000)
    assert n_closed == N // 5 and all(it["status"] == "closed" for it in closed)
    radar, n_radar = store.summaries(text="RADAR", limit=1000)
    assert n_radar == N // 3 and all("radar" in it["title"] for it in radar)
    page2, _ = store.summaries(limit=50, offset=50)
    assert {it["id"] for it in page2}.isdisjoint({it["id"] for it in items})
    every, _ = store.summaries(limit=5000)
    statuses = [it["status"] for it in every]
    assert statuses == sorted(statuses, key=lambda s: s == "closed")  # closed studies sort last


def test_old_store_without_summaries_is_backfilled_once(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite"
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE docs (kind TEXT, id TEXT, updated_at TEXT, body TEXT, PRIMARY KEY(kind, id))")
    c.execute(
        "INSERT INTO docs VALUES ('study','st-old','2026-01-01T00:00:00+00:00',?)",
        (json.dumps(_study(1).model_dump(mode="json")),),
    )
    c.execute("INSERT INTO docs VALUES ('study','st-bad','2026-01-01T00:00:00+00:00','{\"not\": \"a study\"}')")
    c.commit()
    c.close()
    docs = DocumentStore(path)  # adds the summary column
    store = SqliteStudyStore(docs)
    items, total = store.summaries()
    assert total == 2
    by = {it["id"]: it for it in items}
    assert by["st-old"]["title"].startswith("Study 1") and by["st-bad"].get("unreadable")
    assert docs.backfill_summaries("study", lambda d: {}) == 0  # only once


def test_wal_and_concurrent_access(tmp_path: Path) -> None:
    docs = DocumentStore(tmp_path / "mf.sqlite")
    mode = docs._c.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"
    store = SqliteStudyStore(docs)
    errors: list[BaseException] = []

    def writer(k: int) -> None:
        try:
            for i in range(60):
                store.save(_study(k * 1000 + i))
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    def reader() -> None:
        try:
            for _ in range(60):
                store.summaries(limit=20)
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(k,)) for k in range(3)] + [
        threading.Thread(target=reader) for _ in range(3)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert store.summaries()[1] == 180


def test_studies_api_filters_and_total(container) -> None:
    from model_forensic.api.app import create_app

    for i in range(12):
        st = container.studies.create(f"{'Radar' if i % 2 else 'Camera'} study {i}", runs=[])
        if i % 4 == 0:
            container.studies.set_status(st.id, "closed")
    c = TestClient(create_app(container))
    r = c.get("/api/v1/studies", params={"limit": 5})
    assert r.status_code == 200 and len(r.json()) == 5 and r.headers["x-total-count"] == "12"
    assert {x["status"] for x in r.json()} == {"open"}
    assert len(c.get("/api/v1/studies", params={"status": "closed"}).json()) == 3
    assert len(c.get("/api/v1/studies", params={"q": "radar"}).json()) == 6
    assert c.get("/api/v1/studies", params={"status": "nope"}).status_code == 422
