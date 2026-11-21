"""Document persistence for findings, studies, conversations and reports (SQLite, JSON docs).

Documents carry ``schema_version``; readers upgrade old documents through ``_MIGRATIONS``.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from model_forensic.domain.findings import Finding
from model_forensic.domain.run import RunRef
from model_forensic.domain.study import Study
from model_forensic.shared.errors import NotFoundError

_MIGRATIONS: dict[str, dict[int, Callable[[dict[str, Any]], dict[str, Any]]]] = {"finding": {}, "study": {}}


class DocumentStore:
    def __init__(self, path: Path | str = ":memory:") -> None:
        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._c = sqlite3.connect(self._path, check_same_thread=False)
        self._lock = threading.RLock()
        with self._c:
            self._c.execute(
                "CREATE TABLE IF NOT EXISTS docs (kind TEXT, id TEXT, updated_at TEXT, body TEXT, PRIMARY KEY(kind, id))"
            )
            self._c.execute("CREATE TABLE IF NOT EXISTS doc_runs (kind TEXT, id TEXT, run TEXT)")
            self._c.execute("CREATE INDEX IF NOT EXISTS ix_doc_runs ON doc_runs(run, kind)")
            self._c.execute("CREATE TABLE IF NOT EXISTS audit (ts TEXT, actor TEXT, action TEXT, detail TEXT)")

    def put(self, kind: str, doc_id: str, body: dict[str, Any], runs: Sequence[RunRef] = ()) -> None:
        with self._lock, self._c:
            self._c.execute(
                "INSERT OR REPLACE INTO docs VALUES (?,?,?,?)",
                (kind, doc_id, datetime.now(timezone.utc).isoformat(), json.dumps(body, default=str)),
            )
            self._c.execute("DELETE FROM doc_runs WHERE kind=? AND id=?", (kind, doc_id))
            self._c.executemany("INSERT INTO doc_runs VALUES (?,?,?)", [(kind, doc_id, str(r)) for r in runs])

    def get_raw(self, kind: str, doc_id: str) -> dict[str, Any]:
        row = self._c.execute("SELECT body FROM docs WHERE kind=? AND id=?", (kind, doc_id)).fetchone()
        if not row:
            raise NotFoundError(f"{kind} {doc_id!r} not found")
        doc: dict[str, Any] = json.loads(row[0])
        v = int(doc.get("schema_version", 1))
        for target in sorted(k for k in _MIGRATIONS.get(kind, {}) if k > v):
            doc = _MIGRATIONS[kind][target](doc)
        return doc

    def list_raw(self, kind: str) -> list[dict[str, Any]]:
        return [
            json.loads(r[0])
            for r in self._c.execute("SELECT body FROM docs WHERE kind=? ORDER BY updated_at DESC", (kind,))
        ]

    def delete(self, kind: str, doc_id: str) -> None:
        with self._lock, self._c:
            self._c.execute("DELETE FROM docs WHERE kind=? AND id=?", (kind, doc_id))
            self._c.execute("DELETE FROM doc_runs WHERE kind=? AND id=?", (kind, doc_id))

    def ids_for_runs(self, kind: str, refs: Sequence[RunRef]) -> list[str]:
        if not refs:
            return []
        q = f"SELECT DISTINCT id FROM doc_runs WHERE kind=? AND run IN ({','.join('?' * len(refs))})"
        return [r[0] for r in self._c.execute(q, [kind, *map(str, refs)])]

    def audit(self, actor: str, action: str, detail: dict[str, Any]) -> None:
        with self._lock, self._c:
            self._c.execute(
                "INSERT INTO audit VALUES (?,?,?,?)",
                (datetime.now(timezone.utc).isoformat(), actor, action, json.dumps(detail, default=str)),
            )

    def audit_log(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._c.execute("SELECT ts, actor, action, detail FROM audit ORDER BY ts DESC LIMIT ?", (limit,))
        return [{"ts": a, "actor": b, "action": c, "detail": json.loads(d)} for a, b, c, d in rows]


class SqliteFindingStore:
    def __init__(self, docs: DocumentStore) -> None:
        self._docs = docs

    def save(self, findings: Iterable[Finding]) -> None:
        for f in findings:
            self._docs.put("finding", f.id, f.model_dump(mode="json"), f.provenance.runs)

    def get(self, finding_id: str) -> Finding:
        return Finding.model_validate(self._docs.get_raw("finding", finding_id))

    def for_runs(self, refs: Sequence[RunRef]) -> list[Finding]:
        return [self.get(i) for i in self._docs.ids_for_runs("finding", refs)]


class SqliteStudyStore:
    def __init__(self, docs: DocumentStore) -> None:
        self._docs = docs

    def save(self, study: Study) -> None:
        self._docs.put("study", study.id, study.model_dump(mode="json"), study.runs)

    def get(self, study_id: str) -> Study:
        return Study.model_validate(self._docs.get_raw("study", study_id))

    def list(self) -> list[Study]:
        return [Study.model_validate(d) for d in self._docs.list_raw("study")]

    def delete(self, study_id: str) -> None:
        self._docs.delete("study", study_id)
