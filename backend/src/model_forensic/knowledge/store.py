"""Knowledge files: every ``*.yaml`` under the knowledge folder (git-ignored — site knowledge stays out of git).

Edits from the UI and imports from the harness go to ``knowledge.local.yaml``; other files are read-only sources
(e.g. a team-shared file). Imported entries arrive as ``proposed`` and apply only after someone accepts them.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError as PydanticValidationError

from model_forensic.knowledge.model import KnowledgeEntry
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
    def __init__(self, folder: Path | None = None) -> None:
        self.folder = folder or knowledge_dir()
        self._lock = threading.Lock()

    @property
    def main(self) -> Path:
        return self.folder / MAIN_FILE

    def load(self) -> tuple[list[KnowledgeEntry], list[str]]:
        """All entries (the main file wins on duplicate ids) and per-file problems."""
        entries: dict[str, KnowledgeEntry] = {}
        notes: list[str] = []
        if not self.folder.is_dir():
            return [], []
        files = sorted(p for p in self.folder.glob("*.yaml") if not p.name.endswith(".example.yaml"))
        files.sort(key=lambda p: p.name == MAIN_FILE)  # main last → it wins
        for f in files:
            got, errs = parse(f.read_text(encoding="utf-8", errors="replace"))
            notes += [f"{f.name}: {e}" for e in errs]
            for e in got:
                entries[e.id] = e
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
        with self._lock:
            cur = self._main_entries()
            keep = [e for e in cur if e.id != entry_id]
            if len(keep) == len(cur):
                return False
            self._write(keep)
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
