from model_forensic.infrastructure.persistence.sqlite_store import (
    DocumentStore,
    SqliteFindingStore,
    SqliteStudyStore,
)


def test_findings_roundtrip_and_lookup_by_run(container) -> None:
    svc = container.service
    res = svc.analyze("lr-sweep-1e3", analyses=["training_dynamics"])
    store = SqliteFindingStore(container.docs)
    saved = store.for_runs([svc.ref("lr-sweep-1e3")])
    assert {f.id for f in saved} == {f.id for f in res[0].findings}
    assert store.get(saved[0].id) == next(f for f in res[0].findings if f.id == saved[0].id)


def test_study_persists(tmp_path) -> None:
    from model_forensic.domain.run import RunRef
    from model_forensic.domain.study import Study

    db = tmp_path / "db.sqlite"
    SqliteStudyStore(DocumentStore(db)).save(Study(id="s1", title="t", runs=(RunRef(backend="l", run_id="a"),)))
    again = SqliteStudyStore(DocumentStore(db))  # new process, same file
    assert again.get("s1").title == "t"
    again.delete("s1")
    assert again.list() == []
