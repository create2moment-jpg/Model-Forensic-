"""Knowledge sources: where knowledge and documentation live, across codebases.

Files stay the source of truth (agents write them, git diffs them, people review them); Model Forensic reads them,
caches parsed results by file fingerprint and indexes documentation for search (``docindex``).

* The **inbox** is the one place a coding agent writes to (``MF_KNOWLEDGE_INBOX``, default: the knowledge folder).
  UI edits and ``mf knowledge import`` go to ``knowledge.local.yaml`` there.
* Extra **sources** come from ``knowledge_sources:`` in ``model-forensic.yaml`` and from environment variables
  ``MF_KNOWLEDGE_SOURCE_<NAME>=<path>`` (one per codebase / documentation folder). Paths may use ``${VAR}`` and ``~``.
* A source holds knowledge YAML (``entries:``), documentation (Markdown / reStructuredText / text), or both.
  Entries from a ``trust: proposed`` source (the default) apply only after someone accepts them.
"""

from __future__ import annotations

import fnmatch
import os
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

DOC_EXT = (".md", ".markdown", ".rst", ".txt")
KNOWLEDGE_EXT = (".yaml", ".yml")
SKIP_DIRS = ("node_modules", ".git", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache", "dist", "build")
ENV_PREFIX = "MF_KNOWLEDGE_SOURCE_"
INBOX_ENV = "MF_KNOWLEDGE_INBOX"


class KnowledgeSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(description="short id: letters, digits, '-', '_'")
    path: str = Field(description="folder; ${VAR} and ~ are expanded")
    kind: Literal["auto", "knowledge", "docs"] = "auto"
    trust: Literal["proposed", "as_written"] = Field(
        "proposed", description="proposed: entries apply only once accepted; as_written: curated, statuses kept"
    )
    codebase: str = Field("", description="which codebase this documents (shown with search results)")
    exclude: list[str] = Field(default_factory=list, description="path globs relative to the folder")
    max_file_kb: int = 512
    max_files: int = 20000
    inbox: bool = False
    env: str = Field("", description="environment variable it came from (set automatically)")

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,40}", v):
            raise ValueError("name: letters, digits, '-', '_' (1–41 chars)")
        return v

    def resolved(self) -> Path | None:
        """The folder, or None when a ``${VAR}`` in the path is not set."""
        p = os.path.expandvars(self.path)
        if "$" in p:
            return None
        return Path(p).expanduser()

    @property
    def reads_docs(self) -> bool:
        return self.kind in ("auto", "docs")

    @property
    def reads_knowledge(self) -> bool:
        return self.kind in ("auto", "knowledge")


@dataclass(frozen=True)
class SourceFile:
    source: str
    path: Path
    rel: str
    mtime: float
    size: int


def inbox_dir(default: Path) -> Path:
    env = os.environ.get(INBOX_ENV)
    return Path(env).expanduser() if env else default


def discover(
    configured: Sequence[KnowledgeSource] = (),
    default_inbox: Path = Path("config/knowledge"),
    env: Mapping[str, str] | None = None,
) -> list[KnowledgeSource]:
    """The inbox first, then configured sources, then ``MF_KNOWLEDGE_SOURCE_<NAME>`` ones (configured names win)."""
    env = os.environ if env is None else env
    inbox_path = env.get(INBOX_ENV) or str(default_inbox)
    out = [
        KnowledgeSource(
            name="inbox",
            path=inbox_path,
            kind="auto",
            trust="as_written",
            inbox=True,
            env=INBOX_ENV if env.get(INBOX_ENV) else "",
            codebase="",
        )
    ]
    seen = {"inbox"}
    for s in configured:
        if s.name not in seen:
            seen.add(s.name)
            out.append(s.model_copy(update={"inbox": False}))
    for k in sorted(env):
        if not k.startswith(ENV_PREFIX) or not env[k].strip():
            continue
        name = k[len(ENV_PREFIX) :].lower().replace("__", "-")
        try:
            src = KnowledgeSource(name=name, path=env[k].strip(), env=k, codebase=name)
        except ValueError:
            continue
        if src.name not in seen:
            seen.add(src.name)
            out.append(src)
    return out


def walk(src: KnowledgeSource, exts: Sequence[str]) -> Iterator[SourceFile]:
    """Files of a source with one of ``exts`` — pruned walk (skips VCS / env / build folders), size and count capped."""
    root = src.resolved()
    if root is None or not root.is_dir():
        return
    n = 0
    limit = src.max_file_kb * 1024
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for fn in sorted(filenames):
            if not fn.lower().endswith(tuple(exts)) or fn.endswith(".example.yaml"):
                continue
            p = Path(dirpath) / fn
            rel = p.relative_to(root).as_posix()
            if any(fnmatch.fnmatchcase(rel, g) for g in src.exclude):
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            if st.st_size > limit:
                continue
            yield SourceFile(src.name, p, rel, st.st_mtime, st.st_size)
            n += 1
            if n >= src.max_files:
                return


def safe_join(root: Path, rel: str) -> Path | None:
    """``root/rel`` only when it stays inside ``root`` (no traversal, no symlink escape)."""
    try:
        p = (root / rel).resolve()
        r = root.resolve()
    except OSError:
        return None
    return p if p == r or r in p.parents else None
