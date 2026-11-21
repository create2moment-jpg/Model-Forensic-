from model_forensic.infrastructure.persistence.sqlite_store import (
    DocumentStore,
    SqliteFindingStore,
    SqliteWorkspaceStore,
)


def test_findings_roundtrip_and_lookup_by_run(container) -> None:
    svc = container.service
    res = svc.analyze("lr-sweep-1e3", analyses=["training_dynamics"])
    store = SqliteFindingStore(container.docs)
    saved = store.for_runs([svc.ref("lr-sweep-1e3")])
    assert {f.id for f in saved} == {f.id for f in res[0].findings}
    assert store.get(saved[0].id) == next(f for f in res[0].findings if f.id == saved[0].id)


def test_workspace_persists(tmp_path) -> None:
    from model_forensic.domain.run import RunRef
    from model_forensic.domain.workspace import ForensicWorkspace

    db = tmp_path / "db.sqlite"
    SqliteWorkspaceStore(DocumentStore(db)).save(
        ForensicWorkspace(id="w1", title="t", runs=(RunRef(backend="l", run_id="a"),))
    )
    again = SqliteWorkspaceStore(DocumentStore(db))  # new process, same file
    assert again.get("w1").title == "t"
