"""Drafts kept on the server (resume from any browser) and *Continue where you left off*."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from model_forensic.application.drafts import MAX_PAYLOAD, DraftService
from model_forensic.infrastructure.persistence.sqlite_store import DocumentStore
from model_forensic.shared import identity
from model_forensic.shared.errors import NotFoundError, ValidationError


def test_save_list_get_delete_and_ownership() -> None:
    d = DraftService(DocumentStore())
    d.save("new-study:1", "alice", "new-study", "Radar at range", "/studies?draft=new-study:1", {"title": "Radar"})
    d.save("wizard:1", "alice", "study-wizard", "", "/study-wizard?draft=wizard:1", {"step": 2})
    d.save("bob:1", "bob", "new-study", "Bob's", "/studies", {})
    items, total = d.list("alice")
    assert total == 2 and items[0]["id"] == "wizard:1" and "payload" not in items[0]
    assert items[0]["title"] == "Untitled draft"
    assert d.list("alice", kind="new-study")[1] == 1
    assert d.get("new-study:1", "alice")["payload"] == {"title": "Radar"}
    with pytest.raises(NotFoundError):
        d.get("new-study:1", "bob")  # someone else's draft does not exist for you
    with pytest.raises(ValidationError):
        d.save("new-study:1", "bob", "new-study", "steal", "/studies", {})
    created = d.get("new-study:1", "alice")["created_at"]
    d.save("new-study:1", "alice", "new-study", "Radar at long range", "/studies?draft=new-study:1", {"title": "R2"})
    again = d.get("new-study:1", "alice")
    assert again["created_at"] == created and again["payload"] == {"title": "R2"}
    d.delete("new-study:1", "alice")
    assert d.list("alice")[1] == 1


def test_validation() -> None:
    d = DraftService(DocumentStore())
    for bad in (
        dict(draft_id="../x", kind="k", route="/"),
        dict(draft_id="x", kind="Bad Kind", route="/"),
        dict(draft_id="x", kind="k", route="https://evil.example"),
        dict(draft_id="x", kind="k", route="//evil.example/path"),
    ):
        with pytest.raises(ValidationError):
            d.save(owner="a", title="t", payload={}, **bad)
    with pytest.raises(ValidationError):
        d.save("x", "a", "k", "t", "/", {"blob": "x" * (MAX_PAYLOAD + 1)})


def test_prune_by_age_and_per_user_cap() -> None:
    docs = DocumentStore()
    d = DraftService(docs, max_age_days=30)
    d.save("old", "a", "k", "old", "/", {})
    body = docs.get_raw("draft", "old")
    stale = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    docs.put(
        "draft",
        "old",
        {**body, "updated_at": stale},
        summary={**{k: v for k, v in body.items() if k != "payload"}, "updated_at": stale},
    )
    d.save("fresh", "a", "k", "fresh", "/", {})
    assert d.prune() == 1 and [x["id"] for x in d.list("a")[0]] == ["fresh"]
    for i in range(5):
        d.save(f"n{i}", "a", "k", f"n{i}", "/", {})
    assert d.prune("a", keep=3) == 3 and d.list("a")[1] == 3


def test_api_drafts_and_work(container) -> None:
    from model_forensic.api.app import create_app

    c = TestClient(create_app(container))
    r = c.put(
        "/api/v1/drafts/new-study:abc",
        json={
            "kind": "new-study",
            "title": "Radar",
            "route": "/studies?draft=new-study:abc",
            "payload": {"runs": ["r1"]},
        },
    )
    assert r.status_code == 200 and "payload" not in r.json()
    assert c.get("/api/v1/drafts/new-study:abc").json()["payload"] == {"runs": ["r1"]}
    assert c.get("/api/v1/drafts").json()["total"] == 1
    assert c.put("/api/v1/drafts/x", json={"kind": "k", "payload": "nope"}).status_code == 422
    assert c.put("/api/v1/drafts/x", json={"kind": "k", "route": "https://evil", "payload": {}}).status_code == 400
    st = container.studies.create("Open study", runs=["local:xf-online-baseline"])
    w = c.get("/api/v1/work").json()
    assert w["drafts"][0]["id"] == "new-study:abc" and w["drafts_total"] == 1
    assert any(x["id"] == st.id and x["next"] for x in w["studies"])
    assert w["jobs"] == [] and isinstance(w["knowledge_proposed"], int)
    # another user sees none of it
    tok = identity.set_current(identity.User(id="someone-else", name="Someone"))
    try:
        assert c.get("/api/v1/drafts").json()["total"] == 0
        assert c.get("/api/v1/drafts/new-study:abc").status_code == 404
    finally:
        identity._current.reset(tok)  # type: ignore[arg-type]
    assert c.delete("/api/v1/drafts/new-study:abc").status_code == 200
    assert c.get("/api/v1/drafts").json()["total"] == 0
