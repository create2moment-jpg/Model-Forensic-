"""Shared pieces of the HTTP API: request models, the job registry, error diagnosis, build info.

Split out of app.py (design review D7); the routes live in api/routes/, wiring in app.py::create_app."""

from __future__ import annotations

import contextlib
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from model_forensic.ai.investigator import Persona
from model_forensic.domain.downloads import DownloadProfile
from model_forensic.domain.study import EvidenceLink, HypothesisStatus
from model_forensic.shared.logging import get_logger


@dataclass
class Job:
    id: str
    kind: str
    title: str = ""
    created_at: float = field(default_factory=time.time)
    status: str = "running"
    progress: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    error: str | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    params: dict[str, Any] = field(default_factory=dict)  # what is needed to run it again

    def doc(self) -> dict[str, Any]:
        result = self.result
        if isinstance(result, dict) and isinstance(result.get("files"), list) and len(result["files"]) > 500:
            result = {**result, "files": result["files"][:500], "files_truncated": True}
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "created_at": self.created_at,
            "status": self.status,
            "progress": self.progress,
            "result": result,
            "error": self.error,
            "params": self.params,
        }


class JobRegistry(dict[str, Job]):
    """Jobs kept in the document store: saved when created, flushed while they run, reloaded after a restart (jobs
    that were running are marked *interrupted* and can be run again — cached files are not fetched twice)."""

    KEEP = 200

    def __init__(self, docs: Any) -> None:
        super().__init__()
        self._docs = docs
        self._saved: dict[str, str] = {}
        self._lock = threading.Lock()
        try:
            stored = sorted(docs.list_raw("job"), key=lambda d: -float(d.get("created_at") or 0))
        except Exception:  # an unreadable store must not stop the server
            stored = []
        for d in stored[: self.KEEP]:
            j = Job(
                id=d["id"],
                kind=d.get("kind", ""),
                title=d.get("title", ""),
                created_at=float(d.get("created_at") or 0),
                status=d.get("status", "done"),
                progress=d.get("progress") or {},
                result=d.get("result"),
                error=d.get("error"),
                params=d.get("params") or {},
            )
            if j.status == "running":
                j.status = "interrupted"
                j.error = "the server restarted while this job was running — run it again (cached files are kept)"
            super().__setitem__(j.id, j)
            self._save(j)
        for d in stored[self.KEEP :]:
            with contextlib.suppress(Exception):
                docs.delete("job", d["id"])
        threading.Thread(target=self._flusher, daemon=True, name="job-flusher").start()

    def __setitem__(self, key: str, job: Job) -> None:
        super().__setitem__(key, job)
        self._save(job)

    def _save(self, job: Job) -> None:
        body = job.doc()
        sig = json.dumps(body, sort_keys=True, default=str)
        with self._lock:
            if self._saved.get(job.id) == sig:
                return
            self._saved[job.id] = sig
        try:
            self._docs.put("job", job.id, json.loads(sig))
        except Exception as exc:  # persistence is best effort; the job itself goes on
            log.warning("job not persisted", job=job.id, error=type(exc).__name__)

    def flush(self) -> None:
        for j in list(self.values()):
            self._save(j)

    def _flusher(self) -> None:
        while True:
            time.sleep(2.0)
            self.flush()


class AnalyzeRequest(BaseModel):
    run: str
    baseline: str | None = None
    analyses: list[str] | None = None
    params: dict[str, dict[str, Any]] | None = None


class InvestigateRequest(BaseModel):
    run: str
    baseline: str | None = None
    question: str | None = None
    prompt_id: str | None = None
    variables: dict[str, Any] | None = None
    persona: Persona = Persona.ANALYST
    allow_downloads: bool = False
    others: list[str] | None = None  # extra runs to compare in one question (max 8)


class ReportRequest(BaseModel):
    run: str
    baseline: str | None = None
    title: str | None = None
    problem: str | None = None
    investigation_id: str | None = None
    format: str = "markdown"


class DownloadRequest(BaseModel):
    profile: DownloadProfile = DownloadProfile.FAST_DEBUG
    paths: list[str] | None = None  # "path" or "<stage run>::<path>"
    stages: list[str] | None = None  # pipeline stages to include (default: all)


class LLMSettingsRequest(BaseModel):
    enabled: bool
    base_url: str
    model: str
    api_key: str | None = None  # write-only; None = keep current, "" = clear
    api_key_env: str = "MF_LLM_API_KEY"
    temperature: float = 0.1
    max_tokens: int = 2048
    timeout_s: float = 120.0
    context_limit_tokens: int = 64_000
    reasoning_effort: str | None = None
    azure_api_version: str | None = None
    max_tool_steps: int = 4


class StudyCreate(BaseModel):
    title: str
    goal: str = ""
    runs: list[str] = []
    baseline: str | None = None


class StudyPatch(BaseModel):
    title: str | None = None
    goal: str | None = None
    baseline: str | None = None  # "" clears
    status: str | None = None  # open | closed


class RunBody(BaseModel):
    run: str


class TextBody(BaseModel):
    text: str


class AttachInvestigation(BaseModel):
    investigation_id: str


class HypothesisCreate(BaseModel):
    statement: str
    rationale: str = ""
    proposed_change: str = ""
    evidence: list[EvidenceLink] = []
    source: str = "manual"
    reference_run: str | None = None


class HypothesisPatch(BaseModel):
    statement: str | None = None
    status: HypothesisStatus | None = None
    rationale: str | None = None
    proposed_change: str | None = None
    verdict: str | None = None
    test_run: str | None = None  # "" unlinks
    reference_run: str | None = None


log = get_logger(__name__)


def diagnosis_for(err: str | None) -> dict[str, Any] | None:
    if not err:
        return None
    from model_forensic.infrastructure.connectors.azureml.diagnose import classify

    return classify(err).to_dict()


class BuildInfo:
    """The commit the server's code comes from (read once at start) and the commit the served UI was built from
    (``web/dist/build.json``, written by the UI build). Stale = both known and different."""

    def __init__(self, web: Path | None) -> None:
        import json
        import shutil
        import subprocess

        self.source_rev: str | None = None
        repo = Path(__file__).resolve().parents[4]
        git = shutil.which("git")
        if git and (repo / ".git").exists():
            try:
                r = subprocess.run(  # noqa: S603 - fixed arguments, our own checkout
                    [git, "-C", str(repo), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5,
                    check=False,
                )  # fmt: skip
                self.source_rev = r.stdout.strip() or None
            except (OSError, subprocess.SubprocessError):
                self.source_rev = None
        self.web_rev: str | None = None
        try:
            if web is not None:
                self.web_rev = str(json.loads((web / "build.json").read_text()).get("rev") or "") or None
        except (OSError, ValueError):
            self.web_rev = None

    @property
    def stale(self) -> bool:
        if not self.source_rev or not self.web_rev or self.web_rev == "unknown":
            return False
        return self.web_rev.split("+", 1)[0] != self.source_rev


NO_UI_PAGE = """<!doctype html><meta charset="utf-8"><title>Model Forensic — UI not built</title>
<style>body{{font:15px system-ui,sans-serif;max-width:760px;margin:48px auto;padding:0 16px;line-height:1.5}}
code,pre{{background:#f2f2f5;padding:2px 5px;border-radius:4px}}pre{{padding:10px}}</style>
<h1>The server runs — the web UI is not built</h1>
<p>The API answers (<a href="/api/v1/health">/api/v1/health</a>), but no built UI (<code>index.html</code>) was found.
Build it once from the Model Forensic folder, then restart <code>mf serve</code>:</p>
<pre>cd web &amp;&amp; npm ci &amp;&amp; npx vite build
mf stop; mf serve</pre>
<p>Or point to an existing build: <code>MF_WEB_DIST=/path/to/web/dist mf serve</code>.</p>
<p>Looked in:</p><pre>{where}</pre>"""
