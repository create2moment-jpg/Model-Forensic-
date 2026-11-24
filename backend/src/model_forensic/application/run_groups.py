"""Run groups: one subject made of several runs (``group:<run>+<run>+…``).

A candidate (or a baseline) sometimes has its results in more than one run id — training in one job, evaluation in
another, or a training resumed in a new job. A run group is a *virtual pipeline whose stages are its member runs*:
the existing pipeline machinery merges them (metrics as ``member / metric``, a metric logged by several members kept
per member with the last member's value as the headline; configs and files of every member), so compare, studies,
the run page and the analyses treat it like any other run.

* Reference: ``group:ws:run-a+ws:run-b`` (members in order; ``+`` separated). Members may be pipelines themselves.
* A pipeline **without some stages**: ``group:ws:pipe!publish!evaluate_onnx`` (``!`` + a stage name excludes that
  stage). It stays that pipeline — expanded from the pipeline's own stage list, so it works when the stage runs are
  not in the index — and the excluded stages can be ticked back. It may be the only member.
* Groups are never written to the run index and never fetched from a backend: they are built from their members.
* Downloads go to the member that owns the file.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from typing import Any

from model_forensic.application.ports import ListRunsQuery, Page
from model_forensic.domain.artifacts import Artifact
from model_forensic.domain.config import ConfigurationSnapshot
from model_forensic.domain.metrics import MetricKey, MetricSeries
from model_forensic.domain.run import GitInfo, Run, RunKind, RunRef, RunStatus
from model_forensic.shared.errors import NotFoundError, ValidationError

BACKEND = "group"
SEP = "+"
WITHOUT = "!"
MAX_MEMBERS = 12


def parse_members(run_id: str) -> list[tuple[RunRef, tuple[str, ...]]]:
    """``[(run, stages left out)]`` in order; ``ws:pipe!a!b`` is the pipeline ``ws:pipe`` without stages a and b."""
    parts = [p.strip() for p in run_id.split(SEP) if p.strip()]
    out: list[tuple[RunRef, tuple[str, ...]]] = []
    for p in parts:
        base, *without = p.split(WITHOUT)
        out.append((RunRef.parse(base), tuple(w.strip() for w in without if w.strip())))
    if not out or (len(out) < 2 and not out[0][1]):
        raise ValidationError("a run group needs at least two runs (group:<run>+<run>) or a pipeline without some "
                              "stages (group:<pipeline>!<stage>)")  # fmt: skip
    if len(out) > MAX_MEMBERS:
        raise ValidationError(f"a run group has at most {MAX_MEMBERS} runs")
    if any(r.backend == BACKEND for r, _ in out):
        raise ValidationError("a run group cannot contain another group")
    return out


def members_of(run_id: str) -> list[RunRef]:
    """The members as written (a pipeline with stages left out is still that pipeline)."""
    return [r for r, _ in parse_members(run_id)]


def group_ref(members: Sequence[str | RunRef]) -> str:
    """The reference of a group of runs (a single run stays itself)."""
    ids = list(dict.fromkeys(str(m) for m in members))
    return ids[0] if len(ids) == 1 else f"{BACKEND}:{SEP.join(ids)}"


def _common(values: list[Any]) -> Any:
    vals = [v for v in values if v not in (None, "")]
    return vals[0] if vals and all(v == vals[0] for v in vals) else None


class GroupSource:
    """A pseudo-backend: runs are built from their members through the service (never indexed, never synced)."""

    remote = False  # nothing of its own is cached on disk; the members' caches apply
    index_runs = False
    index_configs_on_sync = False

    def __init__(self, svc: Any) -> None:
        self.name = BACKEND
        self.svc = svc

    def list_runs(self, query: ListRunsQuery) -> Page:
        return Page(items=[])

    def expand(self, run_id: str) -> list[tuple[RunRef, str | None]]:
        """``[(member run, its stage name or None)]``: a pipeline with stages left out becomes its other stages."""
        out: list[tuple[RunRef, str | None]] = []
        for ref, without in parse_members(run_id):
            if not without:
                out.append((ref, None))
                continue
            stages = self.svc.stages(ref)
            unknown = sorted(set(without) - {n for n, _ in stages})
            if unknown:
                raise ValidationError(f"{ref} has no stage {', '.join(unknown)}")
            kept = [(c, n) for n, c in stages if n not in without]
            if not kept:
                raise ValidationError(f"every stage of {ref} is left out")
            out += kept
        return out

    def get_run(self, run_id: str) -> Run:
        members = self.expand(run_id)
        refs = [r for r, _ in members]
        runs = [self.svc.get_run(r) for r in refs]
        # a stage keeps its stage name (its run may not be in the index, whose name is then only an id)
        names = [stage or r.display_name or r.name for r, (_, stage) in zip(runs, members, strict=True)]
        if len(set(names)) < len(names):  # unique stage names
            names = [f"{n} ({r.ref.run_id[:8]})" for n, r in zip(names, runs, strict=True)]
        statuses = {r.status for r in runs}
        status = next(
            (s for s in (RunStatus.FAILED, RunStatus.RUNNING, RunStatus.QUEUED, RunStatus.CANCELED, RunStatus.UNKNOWN)
             if s in statuses), RunStatus.COMPLETED,
        )  # fmt: skip
        starts = [r.started_at for r in runs if r.started_at]
        ends = [r.ended_at for r in runs if r.ended_at]
        created = [r.created_at for r in runs if r.created_at]
        summary: dict[str, float] = {}
        for r in runs:
            summary.update(r.summary_metrics)  # later members win, like merged stage metrics
        branches = [r.git.branch for r in runs]
        git = GitInfo(
            branch=_common(branches) or " | ".join(dict.fromkeys(b for b in branches if b)) or None,
            commit=_common([r.git.commit for r in runs]) or runs[-1].git.commit,
            repository=_common([r.git.repository for r in runs]),
            dirty=any(bool(r.git.dirty) for r in runs) or None,
        )
        tags = {k: v for k, v in runs[0].tags.items() if all(r.tags.get(k) == v for r in runs[1:])}
        title = " + ".join(names)
        pm = parse_members(run_id)
        if len(pm) == 1 and pm[0][1]:  # one pipeline without some stages: it stays that pipeline
            pipe = self.svc.get_run(pm[0][0])
            title = f"{pipe.display_name or pipe.name} (without {', '.join(pm[0][1])})"
            if status == RunStatus.UNKNOWN and pipe.status != RunStatus.UNKNOWN:
                status = pipe.status
        return Run(
            ref=RunRef(backend=BACKEND, run_id=run_id),
            name=title,
            display_name=title,
            experiment=_common([r.experiment for r in runs]) or runs[-1].experiment,
            kind=RunKind.PIPELINE,
            status=status,
            created_at=min(created) if created else None,
            started_at=min(starts) if starts else None,
            ended_at=max(ends) if len(ends) == len(runs) else None,
            user=_common([r.user for r in runs]) or ", ".join(dict.fromkeys(r.user for r in runs if r.user)) or None,
            description=f"Run group of {len(runs)} runs: " + ", ".join(str(r) for r in refs),
            tags=tags,
            git=git,
            compute=next(
                (r.compute for r in reversed(runs) if r.compute.target or r.compute.gpu_count), runs[-1].compute
            ),
            children=list(refs),
            summary_metrics=summary,
            backend_extras={
                "stages": ",".join(f"{r.run_id}={n}" for r, n in zip(refs, names, strict=True)),
                "group": "1",
            },
        )

    # The group has nothing of its own: the service merges the members (they are its "stages").
    def list_metric_keys(self, run_id: str) -> list[MetricKey]:
        return []

    def get_metric_series(
        self,
        run_id: str,
        keys: Sequence[MetricKey] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> list[MetricSeries]:
        return []

    def get_configs(self, run_id: str) -> list[ConfigurationSnapshot]:
        return []

    def list_artifacts(self, run_id: str) -> list[Artifact]:
        return []

    def read_log(self, run_id: str, name: str | None = None, tail_bytes: int | None = None) -> str:
        """The members' logs, one after the other (each under a header)."""
        parts = []
        for r, _ in self.expand(run_id):
            try:
                parts.append(f"===== {r} =====\n" + self.svc.source(r).read_log(r.run_id, name, tail_bytes))
            except Exception as exc:  # one member without logs does not hide the others
                parts.append(f"===== {r} =====\n({type(exc).__name__}: no log)")
        return "\n".join(parts)

    def list_children(self, run_id: str) -> list[RunRef]:
        return [r for r, _ in self.expand(run_id)]

    def open_artifact(self, run_id: str, path: str, offset: int = 0) -> Iterator[bytes]:
        """A file of a member (or of a member's stage): delegated to the backend that has that run."""
        last: Exception | None = None
        for name, src in self.svc.sources.items():
            if name == BACKEND:
                continue
            try:
                it = src.open_artifact(run_id, path, offset)
                first = next(it)
            except StopIteration:
                return iter(())
            except Exception as exc:  # not this backend
                last = exc
                continue

            def chain(first: bytes = first, it: Iterator[bytes] = it) -> Iterator[bytes]:
                yield first
                yield from it

            return chain()
        raise NotFoundError(f"no backend has {run_id}/{path}") from last
