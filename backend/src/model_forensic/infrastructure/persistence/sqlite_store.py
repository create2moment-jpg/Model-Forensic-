"""Document persistence for findings, studies, conversations and reports (SQLite, JSON docs).

Documents carry ``schema_version``; readers upgrade old documents through ``_MIGRATIONS``.
"""

from __future__ import annotations

import builtins
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
    """JSON documents by kind and id, with an optional **summary** per document (a small JSON object kept next to the
    body) so lists, filters and paging never parse full documents. WAL mode: readers do not block the writer."""

    def __init__(self, path: Path | str = ":memory:") -> None:
        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._c = sqlite3.connect(self._path, check_same_thread=False, timeout=10)
        self._lock = threading.RLock()
        with self._c:
            if self._path != ":memory:":
                self._c.execute("PRAGMA journal_mode=WAL")
                self._c.execute("PRAGMA synchronous=NORMAL")
            self._c.execute("PRAGMA busy_timeout=10000")
            self._c.execute(
                "CREATE TABLE IF NOT EXISTS docs (kind TEXT, id TEXT, updated_at TEXT, body TEXT, PRIMARY KEY(kind, id))"
            )
            cols = {r[1] for r in self._c.execute("PRAGMA table_info(docs)")}
            if "summary" not in cols:  # stores created before summaries existed: backfilled lazily per kind
                self._c.execute("ALTER TABLE docs ADD COLUMN summary TEXT")
            self._c.execute("CREATE INDEX IF NOT EXISTS ix_docs_kind_updated ON docs(kind, updated_at)")
            self._c.execute("CREATE TABLE IF NOT EXISTS doc_runs (kind TEXT, id TEXT, run TEXT)")
            self._c.execute("CREATE INDEX IF NOT EXISTS ix_doc_runs ON doc_runs(run, kind)")
            self._c.execute("CREATE TABLE IF NOT EXISTS audit (ts TEXT, actor TEXT, action TEXT, detail TEXT)")

    def put(
        self,
        kind: str,
        doc_id: str,
        body: dict[str, Any],
        runs: Sequence[RunRef] = (),
        summary: dict[str, Any] | None = None,
    ) -> None:
        with self._lock, self._c:
            self._c.execute(
                "INSERT OR REPLACE INTO docs (kind, id, updated_at, body, summary) VALUES (?,?,?,?,?)",
                (
                    kind,
                    doc_id,
                    datetime.now(timezone.utc).isoformat(),
                    json.dumps(body, default=str),
                    json.dumps(summary, default=str) if summary is not None else None,
                ),
            )
            self._c.execute("DELETE FROM doc_runs WHERE kind=? AND id=?", (kind, doc_id))
            self._c.executemany("INSERT INTO doc_runs VALUES (?,?,?)", [(kind, doc_id, str(r)) for r in runs])

    def get_raw(self, kind: str, doc_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._c.execute("SELECT body FROM docs WHERE kind=? AND id=?", (kind, doc_id)).fetchone()
        if not row:
            raise NotFoundError(f"{kind} {doc_id!r} not found")
        doc: dict[str, Any] = json.loads(row[0])
        v = int(doc.get("schema_version", 1))
        for target in sorted(k for k in _MIGRATIONS.get(kind, {}) if k > v):
            doc = _MIGRATIONS[kind][target](doc)
        return doc

    def list_raw(self, kind: str, limit: int | None = None) -> list[dict[str, Any]]:
        sql = "SELECT body FROM docs WHERE kind=? ORDER BY updated_at DESC"
        args: list[Any] = [kind]
        if limit is not None:
            sql += " LIMIT ?"
            args.append(limit)
        with self._lock:
            return [json.loads(r[0]) for r in self._c.execute(sql, args).fetchall()]

    def count(self, kind: str) -> int:
        with self._lock:
            return int(self._c.execute("SELECT COUNT(*) FROM docs WHERE kind=?", (kind,)).fetchone()[0])

    def backfill_summaries(self, kind: str, make: Callable[[dict[str, Any]], dict[str, Any]]) -> int:
        """Fill missing summaries of ``kind`` from their bodies (once, for documents stored before summaries)."""
        with self._lock:
            rows = self._c.execute("SELECT id, body FROM docs WHERE kind=? AND summary IS NULL", (kind,)).fetchall()
            if not rows:
                return 0
            with self._c:
                for doc_id, body in rows:
                    try:
                        summ = make(json.loads(body))
                    except Exception:  # an unreadable old document must not block the list
                        summ = {"id": doc_id, "title": doc_id, "unreadable": True}
                    self._c.execute(
                        "UPDATE docs SET summary=? WHERE kind=? AND id=?", (json.dumps(summ, default=str), kind, doc_id)
                    )
            return len(rows)

    def list_summaries(
        self,
        kind: str,
        *,
        where: dict[str, Any] | None = None,
        text: str | None = None,
        limit: int = 100,
        offset: int = 0,
        last: dict[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Summaries of ``kind`` (newest first; ``last`` = {field: value} sorts those rows last), filtered by equal
        summary fields and a case-insensitive text match, with the total before paging."""
        cond, args = ["kind=?", "summary IS NOT NULL"], [kind]
        for k, v in (where or {}).items():
            if not k.isidentifier():
                raise ValueError(f"bad filter field {k!r}")
            cond.append(f"json_extract(summary, '$.{k}') = ?")
            args.append(v)
        if text:
            cond.append("lower(summary) LIKE ?")
            args.append(f"%{text.lower()}%")
        order = "updated_at DESC"
        if last:
            ((lk, _lv),) = last.items()
            if not lk.isidentifier():
                raise ValueError(f"bad sort field {lk!r}")
            order = f"(json_extract(summary, '$.{lk}') = ?) , " + order
        sql_where = " AND ".join(cond)
        with self._lock:
            total = int(self._c.execute(f"SELECT COUNT(*) FROM docs WHERE {sql_where}", args).fetchone()[0])
            q = f"SELECT summary, updated_at, id FROM docs WHERE {sql_where} ORDER BY {order} LIMIT ? OFFSET ?"
            qargs = [*args, *([next(iter(last.values()))] if last else []), max(1, limit), max(0, offset)]
            rows = self._c.execute(q, qargs).fetchall()
        return [{**json.loads(r[0]), "id": r[2], "stored_at": r[1]} for r in rows], total

    def delete(self, kind: str, doc_id: str) -> None:
        with self._lock, self._c:
            self._c.execute("DELETE FROM docs WHERE kind=? AND id=?", (kind, doc_id))
            self._c.execute("DELETE FROM doc_runs WHERE kind=? AND id=?", (kind, doc_id))

    def ids_for_runs(self, kind: str, refs: Sequence[RunRef]) -> list[str]:
        if not refs:
            return []
        q = f"SELECT DISTINCT id FROM doc_runs WHERE kind=? AND run IN ({','.join('?' * len(refs))})"
        with self._lock:
            return [r[0] for r in self._c.execute(q, [kind, *map(str, refs)]).fetchall()]

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
        self._docs.put("study", study.id, study.model_dump(mode="json"), study.runs, summary=study_summary(study))

    def get(self, study_id: str) -> Study:
        return Study.model_validate(self._docs.get_raw("study", study_id))

    def list(self) -> list[Study]:
        return [Study.model_validate(d) for d in self._docs.list_raw("study")]

    def delete(self, study_id: str) -> None:
        self._docs.delete("study", study_id)

    def summaries(
        self, status: str | None = None, text: str | None = None, limit: int = 100, offset: int = 0
    ) -> tuple[builtins.list[dict[str, Any]], int]:
        self._docs.backfill_summaries("study", lambda d: study_summary(Study.model_validate(d)))
        return self._docs.list_summaries(
            "study",
            where={"status": status} if status else None,
            text=text,
            limit=limit,
            offset=offset,
            last={"status": "closed"},
        )


def study_summary(st: Study) -> dict[str, Any]:
    """What lists show — small, so hundreds of studies list in milliseconds."""
    from model_forensic.domain.study import HypothesisStatus

    return {
        "id": st.id,
        "title": st.title,
        "goal": st.goal[:300],
        "owner": st.owner,
        "status": st.status,
        "created_at": st.created_at.isoformat(),
        "updated_at": st.updated_at.isoformat(),
        "runs": len(st.runs),
        "baseline": str(st.baseline) if st.baseline else None,
        "hypotheses": {k.value: sum(1 for h in st.hypotheses if h.status == k) for k in HypothesisStatus},
        "decision": st.decision.outcome.value if st.decision else None,
        "gate_blocking": len(st.gate.blocking) if st.gate else 0,
        "comparisons": len(st.comparisons),
    }
