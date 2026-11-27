"""HTTP routes: /runs."""

from __future__ import annotations

import contextlib
import json
import mimetypes
import threading
import uuid
from pathlib import PurePosixPath
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse

from model_forensic.api.common import DownloadRequest, Job, log
from model_forensic.api.routes.context import RouteContext
from model_forensic.application.service import ForensicService
from model_forensic.container import Container
from model_forensic.domain.downloads import DownloadProfile


def register(app: FastAPI, ctx: RouteContext) -> None:
    _knowledge_for = ctx._knowledge_for
    _pending = ctx._pending
    container: Container = ctx.container
    jobs = ctx.jobs
    p = ctx.p
    svc: ForensicService = ctx.svc

    @app.get(f"{p}/runs/{{run}}/dataset")
    def run_dataset_api(run: str, refresh: bool = False) -> dict[str, Any]:
        """Requested (config) vs loaded (driver log) dataset per stage."""
        from model_forensic.application.run_info import run_dataset

        return run_dataset(svc, run, refresh)

    @app.get(f"{p}/runs/{{run}}/compute")
    def run_compute_api(run: str, refresh: bool = False) -> dict[str, Any]:
        """GPUs actually used (driver log), requested parallelism (config), compute target — per stage."""
        from model_forensic.application.run_info import run_compute

        return run_compute(svc, run, refresh)

    @app.get(f"{p}/runs/{{run}}/sweep")
    def sweep(run: str) -> dict[str, Any]:
        """A sweep's trials: hyper-parameters and primary metric, ranked in the goal's direction, the best marked."""
        from model_forensic.application.sweep import sweep_view

        return sweep_view(svc, run)

    @app.get(f"{p}/runs/{{run}}/resources")
    def resources(run: str) -> dict[str, Any]:
        """How the run used its machines (GPU / CPU / memory / disk / network per node) and what that says — from
        Azure Monitor (needs Monitoring Reader; ``access`` says so when missing) or what the run logged itself."""
        from model_forensic.application.resources import run_resources

        return run_resources(svc, run)

    @app.get(f"{p}/runs/{{run}}/stages")
    def stages(run: str) -> list[dict[str, Any]]:
        """Pipeline stages (child runs) in order; empty for non-pipeline runs."""
        out = []
        for name, ref in svc.stages(run):
            try:
                child = svc.get_run(ref)
                out.append(
                    {
                        "name": name,
                        "ref": str(ref),
                        "status": child.status.value,
                        "kind": child.kind.value,
                        "duration_s": child.duration_s,
                        "failure": child.failure.message if child.failure else None,
                    }
                )
            except Exception as exc:
                out.append(
                    {
                        "name": name,
                        "ref": str(ref),
                        "status": "unknown",
                        "kind": "other",
                        "duration_s": None,
                        "failure": f"{type(exc).__name__}",
                    }
                )
        return out

    @app.get(f"{p}/runs/{{run}}/config")
    def config(run: str, flat: bool = False) -> Any:
        return svc.flat_config(run) if flat else [c.model_dump(mode="json") for c in svc.configs(run)]

    @app.get(f"{p}/runs/{{run}}/logs", response_class=PlainTextResponse)
    def logs(run: str, name: str | None = None, tail_bytes: int = Query(200_000, le=20_000_000)) -> str:
        return svc.read_log(run, name, tail_bytes)

    @app.get(f"{p}/runs/{{run}}/logs/files")
    def log_files(run: str, stage: str | None = None) -> dict[str, Any]:
        """Every log of the run and its stages (``stage``: that stage's), the most telling first — no file is read."""
        from model_forensic.application.run_logs import log_catalog

        if stage and stage not in [n for n, _ in svc.stages(run)]:
            raise HTTPException(400, f"{run} has no stage {stage!r}")
        return log_catalog(svc, run, stage or None)

    @app.get(f"{p}/runs/{{run}}/logs/file")
    def log_file(run: str, id: str, tail_bytes: int = Query(400_000, ge=0, le=200_000_000)) -> dict[str, Any]:
        """One log's last ``tail_bytes`` (0 = the whole file) — read with a ranged request, not the whole file."""
        from model_forensic.application.run_logs import read_log_file

        return read_log_file(svc, run, id, tail_bytes or None)

    @app.get(f"{p}/runs/{{run}}/logs/search")
    def log_search(run: str, pattern: str) -> list[dict[str, Any]]:
        return svc.search_log(run, pattern)

    @app.post(f"{p}/runs/{{run}}/download")
    def download(run: str, req: DownloadRequest | None = None) -> dict[str, Any]:
        req = req or DownloadRequest()
        job = Job(
            id=uuid.uuid4().hex[:10],
            kind="download",
            title=f"{req.profile.value if not req.paths else f'{len(req.paths)} file(s)'} · {run}",
            params={"run": run, "profile": req.profile.value, "paths": req.paths, "stages": req.stages},
        )
        jobs[job.id] = job

        def progress(pr: Any) -> None:
            job.progress = {
                "done_files": pr.done_files,
                "total_files": pr.total_files,
                "done_bytes": pr.done_bytes,
                "total_bytes": pr.total_bytes,
                "cached_files": pr.cached_files,
                "throughput_bps": round(pr.throughput_bps),
            }

        def work() -> None:
            try:
                files, prog = svc.download(
                    run, req.profile, cancel=job.cancel, on_progress=progress, paths=req.paths, stages=req.stages
                )
                progress(prog)
                job.result = {
                    "root": str(svc.artifact_store.run_root(svc.ref(run))),  # every rel_path is under it
                    "files": [
                        {
                            "path": f.artifact.path,
                            "run": str(f.artifact.run),
                            "stage": f.artifact.stage,
                            "local_path": str(f.local_path),
                            "rel_path": svc.rel_path(run, f.artifact),
                            "sha256": f.sha256,
                            "from_cache": f.from_cache,
                        }
                        for f in files
                    ],
                    "cached": prog.cached_files,
                    "failed": prog.failed,
                }
                job.status = "done"
            except Exception as exc:
                job.status, job.error = ("cancelled" if job.cancel.is_set() else "failed"), str(exc)

        threading.Thread(target=work, daemon=True).start()
        container.docs.audit("api", "download", {"run": run, "profile": req.profile.value, "paths": req.paths})
        return {"job_id": job.id}

    @app.get(f"{p}/runs/{{run}}/artifacts/file")
    def artifact_file(run: str, path: str, inline: bool = False) -> Any:
        """Stream one artifact to the browser (cached copy if present, otherwise from the backend).
        ``inline`` shows raster images in the page (never SVG/HTML: same-origin script risk)."""
        ref = svc.ref(run)
        art = svc.find_artifact(ref, path)  # "path" or "<stage run>::<path>" for a pipeline stage's file
        if art is None:
            raise HTTPException(404, "artifact not found")
        name = PurePosixPath(art.path).name.replace('"', "") or "artifact"
        media = mimetypes.guess_type(name)[0] or "application/octet-stream"
        show = inline and media in {"image/png", "image/jpeg", "image/gif", "image/webp"}
        headers = {
            "Content-Disposition": f'{"inline" if show else "attachment"}; filename="{name}"',
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        }
        hit = svc.artifact_store.cached(art)
        if hit is not None:
            return FileResponse(hit.local_path, media_type=media, headers=headers)
        container.docs.audit("api", "artifact_stream", {"run": run, "path": path})
        return StreamingResponse(
            svc.source(art.run).open_artifact(art.run.run_id, art.path), media_type=media, headers=headers
        )

    @app.get(f"{p}/runs/{{run}}/artifacts/preview")
    def artifact_preview(run: str, path: str) -> dict[str, Any]:
        """A safe, bounded preview: JSON pretty-printed, text/CSV/YAML first lines, parquet schema + rows, images by
        URL. The file is fetched into the cache once."""
        art = svc.find_artifact(svc.ref(run), path)
        if art is None:
            raise HTTPException(404, "artifact not found")
        if (art.size_bytes or 0) > 50 * 1024**2:
            return {"kind": "too_large", "size_bytes": art.size_bytes}
        name = art.path.lower()
        if name.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
            from urllib.parse import quote

            return {
                "kind": "image",
                "url": f"{p}/runs/{quote(run, safe='')}/artifacts/file?path={quote(path, safe='')}&inline=true",
            }
        local = svc.artifact_store.fetch(svc.source(art.run), [art])[0][0].local_path
        if name.endswith(".parquet"):
            try:
                import pyarrow.parquet as pq

                t = pq.read_table(local)
                rows = t.slice(0, 50).to_pylist()
                return {
                    "kind": "table",
                    "columns": t.column_names,
                    "rows": json.loads(json.dumps(rows, default=str)),
                    "total_rows": t.num_rows,
                }
            except Exception as exc:
                return {"kind": "unsupported", "note": f"parquet preview needs pyarrow ({type(exc).__name__})"}
        raw = local.read_bytes()[:2_000_000]
        if b"\x00" in raw[:4096]:
            return {"kind": "binary", "size_bytes": art.size_bytes}
        text = raw.decode("utf-8", errors="replace")
        if name.endswith(".json"):
            with contextlib.suppress(ValueError):
                text = json.dumps(json.loads(text), indent=2)
        lines = text.splitlines()
        return {
            "kind": "text",
            "text": "\n".join(lines[:400])[:200_000],
            "total_lines": len(lines),
            "truncated": len(lines) > 400 or len(text) > 200_000,
        }

    @app.get(f"{p}/runs/{{run}}/precision-recall")
    def precision_recall_api(run: str) -> dict[str, Any]:
        """Working points (target vs measured recall), PR-curve AUC by distance, count check from the parquets."""
        from model_forensic.application.precision_recall import collect

        out = collect(svc, run, knowledge=_knowledge_for)
        # read from what is downloaded; ``pending`` says what is not (downloaded only on request)
        out["pending"] = [_pending(run, DownloadProfile.EVAL_JSON), _pending(run, DownloadProfile.EVAL_PARQUET)]
        return out

    @app.get(f"{p}/runs/{{run}}/precision-recall/logs")
    def precision_recall_logs(run: str) -> dict[str, Any]:
        """Precision / recall / F1 / calibration / ECE lines across every log of the run and its stages."""
        from model_forensic.application.precision_recall import log_lines

        return log_lines(svc, run)

    @app.get(f"{p}/runs/{{run}}/eval-files")
    def eval_files_api(run: str) -> list[dict[str, Any]]:
        """Evaluation outputs with what each can and cannot show (the v1 artifact guide)."""
        from model_forensic.application.precision_recall import eval_files

        return eval_files(svc, run)

    @app.get(f"{p}/runs/{{run}}/catalog")
    def run_catalog(run: str) -> dict[str, Any]:
        """The run's files grouped by the question each family answers — triage before downloading."""
        from model_forensic.application.catalog import catalog

        return catalog(svc, run)

    @app.get(f"{p}/runs/{{run}}/download/plans")
    def download_plans(run: str, stages: list[str] = Query(default=[])) -> dict[str, Any]:
        """What each download profile would fetch (files, bytes, already cached) — shown before downloading."""
        return {
            "profiles": svc.plans(run, stages or None),
            "stages": [n for n, _ in svc.stages(run)],
            "local_root": str(svc.artifact_store.run_root(svc.ref(run))),
        }

    @app.get(f"{p}/runs/{{run}}/sequences")
    def sequences(
        run: str,
        table: int | None = None,
        metric: str | None = None,
        cls: str | None = None,
        limit: int = Query(25, le=500),
    ) -> dict[str, Any]:
        """Per-sequence evaluation tables (parquets, downloaded once): per-class summary and worst sequences."""
        from model_forensic.analysis import sequences as sq

        tables = svc.eval_tables(run)
        from model_forensic.domain.messages import ParseMessage

        msgs = [t.message for t in tables if t.message is not None]
        if (
            not tables and svc.pending(run, DownloadProfile.EVAL_PARQUET)["files"] == 0
        ):  # say it — the view shows no table, never a table of zeros
            msgs.append(
                ParseMessage(
                    "missing_data",
                    "sequences.none",
                    "this run has no per-sequence evaluation parquet",
                    remediation="the evaluation stage writes them under evaluation/…/*.parquet; check the job's outputs",
                )
            )
        out: dict[str, Any] = {
            "tables": [sq.summary(t, worst_n=0) for t in tables],
            "deprioritized": svc.team.deprioritized_classes,
            "messages": [m.to_dict() for m in msgs],
            "pending": _pending(run, DownloadProfile.EVAL_PARQUET),
        }
        rec = [i for i, t in enumerate(tables) if t.recognized]
        det = [i for i in rec if tables[i].kind == "detection"]
        if rec:
            # Default: the last stage's detection table, else the last recognised table.
            default = det[-1] if det else rec[-1]
            i = table if table is not None and 0 <= table < len(tables) and tables[table].recognized else default
            out["selected"] = i
            out["worst"] = sq.worst(tables[i], metric, limit, cls)
        return out

    @app.post(f"{p}/runs/{{run}}/metrics/export")
    def export_metrics(run: str) -> dict[str, str]:
        """Write all metric histories (stages merged, raw names kept) as JSON and CSV next to the run's files."""
        j, c = svc.export_metrics(run)
        container.docs.audit("api", "export_metrics", {"run": run})
        root = svc.artifact_store.run_root(svc.ref(run))

        def rel(x: Any) -> str:
            try:
                return str(x.relative_to(root))
            except ValueError:
                return str(x)

        return {"json": str(j), "csv": str(c), "root": str(root), "json_rel": rel(j), "csv_rel": rel(c)}

    from model_forensic.application.drafts import DraftService

    drafts = DraftService(container.docs)
    try:
        drafts.prune()
    except Exception as exc:  # pruning must never stop the server
        log.warning("draft pruning failed", error=type(exc).__name__)
    ctx.drafts = drafts
    ctx.download = download
