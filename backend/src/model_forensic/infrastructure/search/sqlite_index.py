"""SQLite search index: structured columns + FTS5 trigram index for fuzzy text.

SQLite is deliberately chosen (ADR-0004): zero-ops, in-process, handles millions of rows with
proper indexes, and is trivially replaceable by Postgres behind the same port.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Iterable
from pathlib import Path

from model_forensic.application.ports import SearchFilters, SearchResult
from model_forensic.domain.run import Run, RunRef

_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS runs (
  key TEXT PRIMARY KEY, backend TEXT NOT NULL, run_id TEXT NOT NULL, name TEXT, display_name TEXT,
  experiment TEXT, kind TEXT, status TEXT, created_at TEXT, user TEXT, compute TEXT, branch TEXT,
  commit_sha TEXT, parent TEXT, favorite INTEGER DEFAULT 0, doc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_runs_created ON runs(created_at DESC);
CREATE INDEX IF NOT EXISTS ix_runs_status ON runs(status);
CREATE INDEX IF NOT EXISTS ix_runs_experiment ON runs(experiment);
CREATE INDEX IF NOT EXISTS ix_runs_user ON runs(user);
CREATE INDEX IF NOT EXISTS ix_runs_parent ON runs(parent);
CREATE TABLE IF NOT EXISTS run_tags (key TEXT, tag TEXT, value TEXT, PRIMARY KEY(key, tag));
CREATE INDEX IF NOT EXISTS ix_tags ON run_tags(tag, value);
CREATE TABLE IF NOT EXISTS run_params (key TEXT, path TEXT, value TEXT, PRIMARY KEY(key, path));
CREATE INDEX IF NOT EXISTS ix_params ON run_params(path, value);
CREATE TABLE IF NOT EXISTS run_metrics (key TEXT, metric TEXT, value REAL, PRIMARY KEY(key, metric));
CREATE INDEX IF NOT EXISTS ix_metrics ON run_metrics(metric, value);
CREATE VIRTUAL TABLE IF NOT EXISTS runs_fts USING fts5(key UNINDEXED, text, tokenize='trigram');
"""


class SqliteSearchIndex:
    def __init__(self, path: Path | str = ":memory:") -> None:
        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._mem: sqlite3.Connection | None = None
        self._lock = threading.RLock()
        self._conn().executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        if self._path == ":memory:":
            if self._mem is None:
                self._mem = sqlite3.connect(":memory:", check_same_thread=False)
            return self._mem
        c = getattr(self._local, "c", None)
        if c is None:
            c = sqlite3.connect(self._path, check_same_thread=False)
            self._local.c = c
        return c

    def upsert(self, runs: Iterable[Run], params: dict[RunRef, dict[str, str]] | None = None) -> int:
        n = 0
        with self._lock:
            c = self._conn()
            with c:
                for r in runs:
                    k = str(r.ref)
                    if not r.children and "stages" not in r.backend_extras:
                        # A listing update carries no pipeline stages: keep the ones resolved earlier.
                        prev = c.execute("SELECT doc FROM runs WHERE key=?", (k,)).fetchone()
                        if prev:
                            old = Run.model_validate_json(prev[0])
                            if old.children or "stages" in old.backend_extras:
                                extras = {x: old.backend_extras[x] for x in ("stages",) if x in old.backend_extras}
                                r = r.model_copy(
                                    update={"children": old.children, "backend_extras": {**r.backend_extras, **extras}}
                                )
                    fav = c.execute("SELECT favorite FROM runs WHERE key=?", (k,)).fetchone()
                    c.execute(
                        "INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            k,
                            r.ref.backend,
                            r.ref.run_id,
                            r.name,
                            r.display_name,
                            r.experiment,
                            r.kind.value,
                            r.status.value,
                            r.created_at.isoformat() if r.created_at else None,
                            r.user,
                            r.compute.target,
                            r.git.branch,
                            r.git.commit,
                            r.parent.run_id if r.parent else None,
                            fav[0] if fav else 0,
                            r.model_dump_json(),
                        ),
                    )
                    c.execute("DELETE FROM run_tags WHERE key=?", (k,))
                    c.executemany("INSERT INTO run_tags VALUES (?,?,?)", [(k, t, v) for t, v in r.tags.items()])
                    c.execute("DELETE FROM run_metrics WHERE key=?", (k,))
                    c.executemany(
                        "INSERT INTO run_metrics VALUES (?,?,?)", [(k, m, v) for m, v in r.summary_metrics.items()]
                    )
                    if params and r.ref in params:
                        c.execute("DELETE FROM run_params WHERE key=?", (k,))
                        c.executemany(
                            "INSERT INTO run_params VALUES (?,?,?)",
                            [(k, p, str(v).lower()) for p, v in params[r.ref].items()],
                        )
                    text = " ".join(
                        filter(
                            None,
                            [
                                r.name,
                                r.display_name,
                                r.experiment,
                                r.description,
                                r.user,
                                r.git.branch,
                                r.git.commit,
                                r.compute.target,
                                " ".join(f"{a} {b}" for a, b in r.tags.items()),
                            ],
                        )
                    )
                    c.execute("DELETE FROM runs_fts WHERE key=?", (k,))
                    c.execute("INSERT INTO runs_fts VALUES (?,?)", (k, text.lower()))
                    n += 1
        return n

    def get(self, ref: RunRef) -> Run | None:
        row = self._conn().execute("SELECT doc FROM runs WHERE key=?", (str(ref),)).fetchone()
        return Run.model_validate_json(row[0]) if row else None

    def set_favorite(self, ref: RunRef, favorite: bool) -> None:
        with self._lock, self._conn() as c:
            c.execute("UPDATE runs SET favorite=? WHERE key=?", (int(favorite), str(ref)))

    def search(self, filters: SearchFilters, limit: int = 50, offset: int = 0) -> SearchResult:
        t0 = time.perf_counter()
        where: list[str] = ["1=1"]
        args: list[object] = []
        notes: list[str] = []
        if filters.statuses:
            where.append(f"status IN ({','.join('?' * len(filters.statuses))})")
            args += list(filters.statuses)
        if filters.kinds:
            where.append(f"kind IN ({','.join('?' * len(filters.kinds))})")
            args += list(filters.kinds)
        if filters.user:
            where.append("user = ? COLLATE NOCASE")
            args.append(filters.user)
        if filters.users:
            where.append(f"user COLLATE NOCASE IN ({','.join('?' * len(filters.users))})")
            args += list(filters.users)
        for col, val in (
            ("experiment", filters.experiment),
            ("compute", filters.compute),
            ("branch", filters.branch),
            ("backend", filters.backend),
            ("parent", filters.parent),
        ):
            if val:
                where.append(f"{col} = ?")
                args.append(val)
        if filters.commit:
            where.append("commit_sha LIKE ?")
            args.append(filters.commit + "%")
        if filters.created_after:
            where.append("created_at >= ?")
            args.append(filters.created_after.isoformat())
        if filters.created_before:
            where.append("created_at < ?")
            args.append(filters.created_before.isoformat())
        if filters.favorites_only:
            where.append("favorite = 1")
        for tag, val in filters.tags:
            if val is None:
                where.append("key IN (SELECT key FROM run_tags WHERE tag = ?)")
                args.append(tag)
            else:
                where.append("key IN (SELECT key FROM run_tags WHERE tag = ? AND value = ?)")
                args += [tag, val]
        for path, val in filters.params:
            where.append("key IN (SELECT key FROM run_params WHERE (path = ? OR path LIKE ?) AND value = ?)")
            args += [path, "%." + path, val.lower()]
        for metric, lo, hi in filters.metric_ranges:
            cond: list[str] = ["metric = ?"]
            margs: list[object] = [metric]
            if lo is not None:
                cond.append("value > ?")
                margs.append(lo)
            if hi is not None:
                cond.append("value < ?")
                margs.append(hi)
            where.append(f"key IN (SELECT key FROM run_metrics WHERE {' AND '.join(cond)})")
            args += margs
        if filters.text:
            terms = [t for t in filters.text.lower().split() if t]
            short = [t for t in terms if len(t) < 3]
            long_ = [t for t in terms if len(t) >= 3]
            if long_:
                match = " AND ".join('"' + t.replace('"', '""') + '"' for t in long_)
                where.append("key IN (SELECT key FROM runs_fts WHERE runs_fts MATCH ?)")
                args.append(match)
            for t in short:
                where.append("key IN (SELECT key FROM runs_fts WHERE text LIKE ?)")
                args.append(f"%{t}%")
        sql_where = " AND ".join(where)
        c = self._conn()
        total = c.execute(f"SELECT COUNT(*) FROM runs WHERE {sql_where}", args).fetchone()[0]
        rows = c.execute(
            f"SELECT doc FROM runs WHERE {sql_where} ORDER BY favorite DESC, created_at DESC LIMIT ? OFFSET ?",
            [*args, limit, offset],
        ).fetchall()
        if total == 0 and filters.text:
            notes.append("No exact matches; try fewer words or a field filter (status:, exp:, tag:).")
        return SearchResult(
            runs=[Run.model_validate_json(r[0]) for r in rows],
            total=total,
            took_ms=round((time.perf_counter() - t0) * 1000, 2),
            applied=filters,
            notes=notes,
        )

    def favorite_keys(self) -> set[str]:
        return {r[0] for r in self._conn().execute("SELECT key FROM runs WHERE favorite = 1")}

    def suggest(self, prefix: str, field: str = "name", limit: int = 10) -> list[str]:
        cols = {"name": "name", "experiment": "experiment", "user": "user", "branch": "branch", "compute": "compute"}
        if field == "tag":
            rows = self._conn().execute(
                "SELECT DISTINCT tag FROM run_tags WHERE tag LIKE ? LIMIT ?", (prefix + "%", limit)
            )
        elif field == "metric":
            rows = self._conn().execute(
                "SELECT DISTINCT metric FROM run_metrics WHERE metric LIKE ? LIMIT ?", (prefix + "%", limit)
            )
        elif field == "param":
            rows = self._conn().execute(
                "SELECT DISTINCT path FROM run_params WHERE path LIKE ? LIMIT ?", (f"%{prefix}%", limit)
            )
        else:
            col = cols.get(field, "name")
            rows = self._conn().execute(
                f"SELECT DISTINCT {col} FROM runs WHERE {col} LIKE ? ORDER BY created_at DESC LIMIT ?",
                (f"%{prefix}%", limit),
            )
        return [r[0] for r in rows if r[0]]

    def stats(self) -> dict[str, int]:
        c = self._conn()
        return {
            "runs": c.execute("SELECT COUNT(*) FROM runs").fetchone()[0],
            "params": c.execute("SELECT COUNT(*) FROM run_params").fetchone()[0],
        }


def dumps(o: object) -> str:
    return json.dumps(o, default=str)
