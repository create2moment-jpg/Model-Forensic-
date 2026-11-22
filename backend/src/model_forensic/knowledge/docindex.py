"""Full-text index of the documentation in all knowledge sources (SQLite FTS5, one row per section).

Incremental: a file is re-read only when its (mtime, size) changed; files that disappeared are dropped. Markdown is
split at headings (outside code fences) so a search returns the section, not a whole file. The index is a cache — the
files stay the source of truth; deleting ``docs.sqlite`` only costs one re-index.
"""

from __future__ import annotations

import re
import sqlite3
import threading
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from model_forensic.knowledge.sources import DOC_EXT, KnowledgeSource, safe_join, walk

MAX_SECTION = 20_000
_HEAD = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_WORD = re.compile(r"[\w.-]+", re.UNICODE)


@dataclass
class Section:
    heading: str
    level: int
    anchor: str
    body: str


@dataclass
class Hit:
    source: str
    path: str
    title: str
    heading: str
    anchor: str
    snippet: str
    codebase: str
    score: float


@dataclass
class SourceReport:
    source: str
    path: str
    exists: bool
    files: int = 0
    changed: int = 0
    removed: int = 0
    seconds: float = 0.0
    error: str = ""
    notes: list[str] = field(default_factory=list)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:80]


def sections(text: str, name: str) -> list[Section]:
    """Markdown → sections at headings (code fences respected), each titled by its heading path
    ("Uncertainty weighting › Sigma") so a parent heading's words find the section; other text → one section."""
    if not name.lower().endswith((".md", ".markdown")):
        return [Section(Path(name).stem, 1, "", text[:MAX_SECTION])]
    out: list[Section] = []
    stack: list[tuple[int, str]] = []
    head, level = Path(name).stem, 0
    buf: list[str] = []
    fence = False

    def flush() -> None:
        if "".join(buf).strip():
            path = " › ".join(h for _, h in stack) or head
            out.append(Section(path, level, slug(head) if level else "", "\n".join(buf)[:MAX_SECTION]))

    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fence = not fence
        m = None if fence else _HEAD.match(line)
        if m:
            flush()
            head, level = m.group(2).strip(), len(m.group(1))
            stack = [(lv, h) for lv, h in stack if lv < level] + [(level, head)]
            buf = []
        else:
            buf.append(line)
    flush()
    return out


def title_of(text: str, name: str) -> str:
    for line in text.splitlines()[:40]:
        m = _HEAD.match(line)
        if m:
            return m.group(2).strip()
    return Path(name).stem


def fts_query(q: str, any_word: bool = False) -> str:
    """User text → a safe FTS5 query: every word must occur (prefix match on the last one); ``any_word`` → OR."""
    words = [w.replace('"', "") for w in _WORD.findall(q)][:12]
    if not words:
        return ""
    parts = [f'"{w}"' for w in words[:-1]] + [f'"{words[-1]}"*']
    return (" OR " if any_word else " ").join(parts)


class DocIndex:
    def __init__(self, path: Path | str = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._c = sqlite3.connect(self.path, check_same_thread=False)
        self._lock = threading.RLock()
        self._last_refresh = 0.0
        with self._c:
            if self.path != ":memory:":
                self._c.execute("PRAGMA journal_mode=WAL")
            self._c.execute(
                "CREATE TABLE IF NOT EXISTS files (source TEXT, path TEXT, mtime REAL, size INTEGER, title TEXT, "
                "codebase TEXT, PRIMARY KEY(source, path))"
            )
            self._c.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS sections USING fts5(source UNINDEXED, path UNINDEXED, "
                "anchor UNINDEXED, heading, body, tokenize='porter unicode61')"
            )

    # ------------------------------------------------------------------------------------------- indexing
    def refresh(self, sources: Sequence[KnowledgeSource]) -> list[SourceReport]:
        reports = []
        names = {s.name for s in sources}
        with self._lock:
            for (gone,) in self._c.execute("SELECT DISTINCT source FROM files").fetchall():
                if gone not in names:
                    self._drop_source(gone)
            for src in sources:
                reports.append(self._refresh_source(src))
            self._last_refresh = time.monotonic()
        return reports

    def refresh_if_stale(self, sources: Sequence[KnowledgeSource], max_age_s: float = 30.0) -> None:
        if time.monotonic() - self._last_refresh > max_age_s:
            self.refresh(sources)

    def _drop_source(self, name: str) -> None:
        with self._c:
            self._c.execute("DELETE FROM files WHERE source=?", (name,))
            self._c.execute("DELETE FROM sections WHERE source=?", (name,))

    def _refresh_source(self, src: KnowledgeSource) -> SourceReport:
        t0 = time.monotonic()
        root = src.resolved()
        rep = SourceReport(src.name, str(root) if root else src.path, bool(root and root.is_dir()))
        if root is None:
            rep.error = f"path {src.path!r} uses an unset environment variable"
            return rep
        if not rep.exists:
            rep.error = "folder not found"
            return rep
        if not src.reads_docs:
            return rep
        known = {
            p: (m, s) for p, m, s in self._c.execute("SELECT path, mtime, size FROM files WHERE source=?", (src.name,))
        }
        seen: set[str] = set()
        try:
            with self._c:
                for f in walk(src, DOC_EXT):
                    seen.add(f.rel)
                    rep.files += 1
                    if known.get(f.rel) == (f.mtime, f.size):
                        continue
                    try:
                        text = f.path.read_text(encoding="utf-8", errors="replace")
                    except OSError as exc:
                        rep.notes.append(f"{f.rel}: {exc}")
                        continue
                    self._c.execute("DELETE FROM sections WHERE source=? AND path=?", (src.name, f.rel))
                    self._c.executemany(
                        "INSERT INTO sections VALUES (?,?,?,?,?)",
                        [(src.name, f.rel, s.anchor, s.heading, s.body) for s in sections(text, f.rel)],
                    )
                    self._c.execute(
                        "INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?)",
                        (src.name, f.rel, f.mtime, f.size, title_of(text, f.rel), src.codebase),
                    )
                    rep.changed += 1
                for rel in set(known) - seen:
                    self._c.execute("DELETE FROM files WHERE source=? AND path=?", (src.name, rel))
                    self._c.execute("DELETE FROM sections WHERE source=? AND path=?", (src.name, rel))
                    rep.removed += 1
        except sqlite3.Error as exc:
            rep.error = f"index error: {exc}"
        if rep.files >= src.max_files:
            rep.notes.append(f"stopped at max_files={src.max_files}; narrow the folder or raise the limit")
        rep.seconds = round(time.monotonic() - t0, 3)
        return rep

    # ------------------------------------------------------------------------------------------- reading
    def search(self, q: str, source: str | None = None, limit: int = 20) -> list[Hit]:
        """Sections containing every word; when none does, sections containing any of them (best first)."""
        hits = self._search(fts_query(q), source, limit)
        if not hits and len(_WORD.findall(q)) > 1:
            hits = self._search(fts_query(q, any_word=True), source, limit)
        return hits

    def _search(self, fq: str, source: str | None, limit: int) -> list[Hit]:
        if not fq:
            return []
        sql = (
            "SELECT s.source, s.path, s.anchor, s.heading, snippet(sections, 4, '[', ']', ' … ', 18), "
            "bm25(sections, 0, 0, 0, 4.0, 1.0), f.title, f.codebase "
            "FROM sections s JOIN files f ON f.source = s.source AND f.path = s.path "
            "WHERE sections MATCH ?"
        )
        args: list[Any] = [fq]
        if source:
            sql += " AND s.source = ?"
            args.append(source)
        sql += " ORDER BY 6 LIMIT ?"
        args.append(max(1, min(limit, 200)))
        with self._lock:
            rows = self._c.execute(sql, args).fetchall()
        return [Hit(r[0], r[1], r[6], r[3], r[2], r[4], r[7] or "", round(-float(r[5]), 3)) for r in rows]

    def files(self, source: str | None = None, limit: int = 500, offset: int = 0) -> list[dict[str, Any]]:
        sql = "SELECT source, path, title, codebase, mtime, size FROM files"
        args: list[Any] = []
        if source:
            sql += " WHERE source=?"
            args.append(source)
        sql += " ORDER BY mtime DESC LIMIT ? OFFSET ?"
        args += [limit, offset]
        with self._lock:
            rows = self._c.execute(sql, args).fetchall()
        return [dict(zip(("source", "path", "title", "codebase", "mtime", "size"), r, strict=True)) for r in rows]

    def counts(self) -> dict[str, int]:
        with self._lock:
            return dict(self._c.execute("SELECT source, COUNT(*) FROM files GROUP BY source").fetchall())

    def read(self, sources: Sequence[KnowledgeSource], source: str, rel: str) -> tuple[str, str]:
        """(title, text) of an indexed file — only files the index knows, inside their source folder."""
        src = next((s for s in sources if s.name == source), None)
        root = src.resolved() if src else None
        with self._lock:
            row = self._c.execute("SELECT title FROM files WHERE source=? AND path=?", (source, rel)).fetchone()
        if root is None or row is None:
            raise FileNotFoundError(f"{source}:{rel} is not an indexed document")
        p = safe_join(root, rel)
        if p is None or not p.is_file():
            raise FileNotFoundError(f"{source}:{rel} is not an indexed document")
        return row[0], p.read_text(encoding="utf-8", errors="replace")


def hit_dict(h: Hit) -> dict[str, Any]:
    return asdict(h)


def report_dict(r: SourceReport) -> dict[str, Any]:
    return asdict(r)
