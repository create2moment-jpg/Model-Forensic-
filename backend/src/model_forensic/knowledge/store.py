"""Knowledge files: every ``*.yaml`` under the knowledge folder (git-ignored — site knowledge stays out of git).

Edits from the UI and imports from the harness go to ``knowledge.local.yaml``; other files are read-only sources
(e.g. a team-shared file). Imported entries arrive as ``proposed`` and apply only after someone accepts them.
"""

from __future__ import annotations

import os
import re
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError as PydanticValidationError

from model_forensic.knowledge.model import KnowledgeEntry
from model_forensic.knowledge.sources import KNOWLEDGE_EXT, KnowledgeSource, discover, walk
from model_forensic.shared import yamlsafe

DEFAULT_DIR = Path("config/knowledge")
MAIN_FILE = "knowledge.local.yaml"


def knowledge_dir() -> Path:
    return Path(os.environ.get("MF_KNOWLEDGE_DIR", str(DEFAULT_DIR)))


def parse(text: str) -> tuple[list[KnowledgeEntry], list[str]]:
    """Entries from YAML (``{entries: [...]}`` or a bare list); errors are returned, never raised."""
    errors: list[str] = []
    try:
        doc = yamlsafe.load(text)
    except (ValueError, yaml.YAMLError) as exc:
        return [], [f"not valid YAML: {str(exc)[:300]}"]
    items = doc.get("entries") if isinstance(doc, dict) else doc
    if not isinstance(items, list):
        return [], ["expected a list of entries (or a mapping with 'entries:')"]
    out: list[KnowledgeEntry] = []
    for i, it in enumerate(items):
        try:
            out.append(KnowledgeEntry.model_validate(it))
        except PydanticValidationError as exc:
            msgs = "; ".join(f"{'.'.join(map(str, er['loc']))}: {er['msg']}" for er in exc.errors()[:5])
            errors.append(f"entry {i + 1} ({(it or {}).get('id', '?') if isinstance(it, dict) else '?'}): {msgs}")
    ids = [e.id for e in out]
    errors += [f"duplicate id {x}" for x in sorted({x for x in ids if ids.count(x) > 1})]
    return out, errors


class KnowledgeStore:
    """All knowledge entries across sources. The inbox's ``knowledge.local.yaml`` wins (accepting / retiring an entry
    from any source writes an override there); other files are read-only. Parsed files are cached by fingerprint."""

    def __init__(self, folder: Path | None = None, sources: Sequence[KnowledgeSource] | None = None) -> None:
        self.folder = folder or knowledge_dir()
        self.sources: list[KnowledgeSource] = list(sources) if sources is not None else discover((), self.folder)
        inbox = next((s for s in self.sources if s.inbox), None)
        if inbox is not None and inbox.resolved() is not None:
            self.folder = inbox.resolved() or self.folder
        self.folder = self.folder.expanduser().absolute()  # agents in other repositories need an absolute path
        self._lock = threading.Lock()
        self._cache: dict[Path, tuple[tuple[float, int], list[KnowledgeEntry], list[str]]] = {}
        self.origins: dict[str, str] = {}

    @property
    def main(self) -> Path:
        return self.folder / MAIN_FILE

    def _parse_file(self, path: Path, mtime: float, size: int, strict: bool) -> tuple[list[KnowledgeEntry], list[str]]:
        hit = self._cache.get(path)
        if hit is not None and hit[0] == (mtime, size):
            return hit[1], hit[2]
        text = path.read_text(encoding="utf-8", errors="replace")
        if not strict and not re.search(r"^entries\s*:", text, re.M):
            got: list[KnowledgeEntry] = []
            errs: list[str] = []  # a YAML file that is not knowledge (a config, a CI file) — not ours to judge
        else:
            got, errs = parse(text)
        self._cache[path] = ((mtime, size), got, errs)
        return got, errs

    def load(self) -> tuple[list[KnowledgeEntry], list[str]]:
        """All entries (later sources override earlier ones; the inbox main file wins) and per-file problems."""
        entries: dict[str, KnowledgeEntry] = {}
        origins: dict[str, str] = {}
        notes: list[str] = []
        ordered = [s for s in self.sources if not s.inbox] + [s for s in self.sources if s.inbox]
        main_resolved = self.main.resolve() if self.main.exists() else None
        for src in ordered:
            if not src.reads_knowledge:
                continue
            files = list(walk(src, KNOWLEDGE_EXT))
            files.sort(key=lambda f: f.path.resolve() == main_resolved)  # the main file last → it wins
            for f in files:
                if f.rel in {"sources.local.yaml", "sources.yaml"}:
                    continue
                try:
                    got, errs = self._parse_file(f.path, f.mtime, f.size, strict=src.inbox)
                except OSError as exc:
                    notes.append(f"{src.name}:{f.rel}: {exc}")
                    continue
                notes += [f"{src.name}:{f.rel}: {e}" for e in errs]
                is_main = main_resolved is not None and f.path.resolve() == main_resolved
                for e in got:
                    if src.trust == "proposed" and e.status == "active" and not is_main:
                        e = e.model_copy(update={"status": "proposed"})
                    if src.inbox and not is_main and e.status == "active" and e.source.via.startswith("harness"):
                        e = e.model_copy(update={"status": "proposed"})  # agent output is reviewed first
                    entries[e.id] = e
                    origins[e.id] = f"{src.name}:{f.rel}"
        self.origins = origins
        return list(entries.values()), notes

    def _main_entries(self) -> list[KnowledgeEntry]:
        if not self.main.is_file():
            return []
        got, _ = parse(self.main.read_text(encoding="utf-8"))
        return got

    def _write(self, entries: list[KnowledgeEntry]) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        body: dict[str, Any] = {"entries": [e.model_dump(exclude_defaults=False) for e in entries]}
        tmp = self.main.with_suffix(".tmp")
        tmp.write_text(
            "# Model Forensic knowledge (git-ignored). Edit here, in the Knowledge page, or import from the harness.\n"
            + yaml.safe_dump(body, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        tmp.replace(self.main)

    def upsert(self, entry: KnowledgeEntry) -> KnowledgeEntry:
        with self._lock:
            cur = [e for e in self._main_entries() if e.id != entry.id]
            self._write([*cur, entry])
        return entry

    def delete(self, entry_id: str) -> bool:
        """Remove an entry. One that lives in a read-only source is retired by an override in the main file."""
        with self._lock:
            cur = self._main_entries()
            keep = [e for e in cur if e.id != entry_id]
            if len(keep) != len(cur):
                self._write(keep)
                if entry_id not in self.origins or self.origins[entry_id].endswith(MAIN_FILE):
                    return True
        entries, _ = self.load()
        other = next((e for e in entries if e.id == entry_id), None)
        if other is None:
            return len(keep) != len(cur)
        self.upsert(other.model_copy(update={"status": "retired"}))
        return True

    def import_text(self, text: str, via: str = "import") -> tuple[list[KnowledgeEntry], list[str]]:
        """Validate and add entries as *proposed* (existing active entries with the same id are not touched)."""
        got, errors = parse(text)
        if errors:
            return [], errors
        with self._lock:
            cur = {e.id: e for e in self._main_entries()}
            added = []
            for e in got:
                if e.id in cur and cur[e.id].status == "active":
                    errors.append(f"{e.id}: an active entry with this id exists — not overwritten")
                    continue
                e = e.model_copy(update={"status": "proposed", "source": e.source.model_copy(update={"via": via})})
                cur[e.id] = e
                added.append(e)
            self._write(list(cur.values()))
        return added, errors
