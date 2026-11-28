"""Log insights: everything the run's logs say, pulled out by patterns — facts (world size, versions, dataset loaded),
problems (errors, warnings, OOM, NCCL, NaN — counted, first and last occurrence), events (checkpoints, evaluation
results) and curves (loss, data time, throughput) — plus the noteworthy lines no pattern explains yet.

Deterministic. Incremental: each log file is read from where the last read stopped (a running job's logs grow; only
the new bytes are read), so refreshing every half minute costs a few kB. A large remote log read for the first time is
read from its end (the automatic download limit) and says so.

Patterns: built in (``domain/log_patterns.BUILTIN``), from the training codebase (harness task ``log_patterns`` → YAML in
``<inbox>/log_patterns/``), proposed from the run's own lines (by the local LLM — grounded: the regex must compile and
match the line it cites, verbatim — or, without an LLM, written from the line's ``name=value`` numbers), or written by
the user. Anything not built in is proposed until accepted.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any

import yaml

from model_forensic.application.run_logs import clean, log_catalog
from model_forensic.domain.log_patterns import BUILTIN, NOTEWORTHY, LogPattern, check, from_line, template
from model_forensic.shared.errors import NotFoundError, ValidationError

KIND = "log_pattern"
FIRST_READ_MAX = 50 * 1024**2  # a remote log larger than this is read from its end the first time
MAX_POINTS = 20_000
_TS = re.compile(r"^\[?(\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d)")
_NUMERIC = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?$|nan|inf", re.I)


# ── patterns ────────────────────────────────────────────────────────────────────────────────────────────────────
class PatternStore:
    """Built in + the document store (user / LLM / accepted code patterns, with status) + the inbox's code patterns."""

    def __init__(self, docs: Any, inbox: Path | None = None) -> None:
        self.docs = docs
        self.inbox = inbox

    def _code(self) -> list[LogPattern]:
        out: list[LogPattern] = []
        d = self.inbox / "log_patterns" if self.inbox else None
        if d is None or not d.is_dir():
            return out
        for f in sorted(d.glob("*.y*ml")):
            try:
                data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            except (OSError, yaml.YAMLError):
                continue
            for raw in data.get("patterns", []) if isinstance(data, dict) else []:
                try:
                    p = LogPattern.model_validate({**raw, "source": "code", "status": "proposed"})
                except Exception:  # noqa: S112 — an entry that does not fit the schema is skipped; check() reports the rest
                    continue
                out.append(p)
        return out

    def all(self) -> list[LogPattern]:
        stored = {d["id"]: LogPattern.model_validate(d) for d in self.docs.list_raw(KIND)}
        out = {p.id: p for p in BUILTIN}
        for p in self._code():
            out[p.id] = stored.pop(p.id, p)  # an accepted / rejected code pattern keeps its decision
        out.update(stored)
        return list(out.values())

    def active(self) -> list[LogPattern]:
        return [p for p in self.all() if p.status == "active" and not check(p)]

    def get(self, pid: str) -> LogPattern:
        hit = next((p for p in self.all() if p.id == pid), None)
        if hit is None:
            raise NotFoundError(f"no log pattern {pid!r}")
        return hit

    def put(self, p: LogPattern) -> LogPattern:
        errs = check(p)
        if errs:
            raise ValidationError("; ".join(errs))
        if p.source == "builtin":
            raise ValidationError("built-in patterns cannot be changed (add your own instead)")
        self.docs.put(KIND, p.id, p.model_dump(), summary={"title": p.title, "status": p.status})
        return p

    def decide(self, pid: str, status: str) -> LogPattern:
        if status not in ("active", "rejected", "proposed"):
            raise ValidationError("status must be active, rejected or proposed")
        return self.put(self.get(pid).model_copy(update={"status": status}))

    def delete(self, pid: str) -> None:
        p = self.get(pid)
        if p.source == "builtin":
            raise ValidationError("built-in patterns cannot be deleted")
        self.docs.delete(KIND, pid)


def _version(patterns: list[LogPattern]) -> str:
    return hashlib.sha256(json.dumps([p.model_dump() for p in patterns], sort_keys=True).encode()).hexdigest()[:12]


# ── extraction ──────────────────────────────────────────────────────────────────────────────────────────────────
@dataclass
class _Acc:
    lines: int = 0
    matched: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    first: dict[str, dict[str, Any]] = field(default_factory=dict)
    last: dict[str, deque[dict[str, Any]]] = field(default_factory=dict)
    facts: dict[str, dict[str, Any]] = field(default_factory=dict)
    series: dict[str, list[tuple[float, float]]] = field(default_factory=dict)
    uncovered: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class _FileState:
    offset: int = 0
    line_no: int = 0
    carry: str = ""
    acc: _Acc = field(default_factory=_Acc)
    note: str | None = None


_states: dict[str, _FileState] = {}
_lock = Lock()


def _feed(acc: _Acc, line: str, line_no: int, label: str, compiled: list[tuple[LogPattern, re.Pattern[str]]]) -> None:
    acc.lines += 1
    text = line.rstrip("\r\n")
    if not text.strip():
        return
    hit = False
    for p, rx in compiled:
        m = rx.search(text)
        if not m:
            continue
        hit = True
        acc.counts[p.id] = acc.counts.get(p.id, 0) + 1
        gd = {k: v for k, v in m.groupdict().items() if v is not None}
        ts = _TS.match(text)
        ev = {
            "file": label,
            "line": line_no,
            "text": clean(text)[:400],
            "fields": gd,
            "ts": ts.group(1) if ts else None,
        }
        acc.first.setdefault(p.id, ev)
        acc.last.setdefault(p.id, deque(maxlen=20)).append(ev)
        if p.kind == "fact":
            for k, v in gd.items():
                acc.facts[f"{p.id}:{k}"] = {
                    "field": k,
                    "value": clean(v)[:200],
                    "pattern": p.id,
                    "file": label,
                    "line": line_no,
                }
        elif p.kind == "series":
            x = (
                float(gd[p.step_field])
                if p.step_field and p.step_field in gd and _NUMERIC.match(gd[p.step_field])
                else float(line_no)
            )
            for k, v in gd.items():
                if k == p.step_field or not _NUMERIC.match(v):
                    continue
                pts = acc.series.setdefault(f"{p.id}:{k}", [])
                pts.append((x, float(v)))
                if len(pts) > MAX_POINTS:
                    del pts[::2]  # keep the shape, halve the points
    if hit:
        acc.matched += 1
    elif NOTEWORTHY.search(text) or (re.search(r"\d", text) and re.search(r"[=:]", text)):
        t = template(text)
        u = acc.uncovered.get(t)
        if u is None:
            if len(acc.uncovered) >= 300:
                return
            acc.uncovered[t] = {"template": t, "count": 1, "example": clean(text)[:400], "file": label, "line": line_no,
                                "noteworthy": bool(NOTEWORTHY.search(text))}  # fmt: skip
        else:
            u["count"] += 1


def _read_new(svc: Any, a: Any, st: _FileState) -> bytes:
    """The bytes of ``a`` after ``st.offset`` (the local copy if downloaded, else a ranged read from the backend)."""
    hit = svc.artifact_store.cached(a)
    if hit is not None:
        with hit.local_path.open("rb") as fh:
            fh.seek(st.offset)
            return bytes(fh.read())
    src = svc.source(a.run)
    size = a.size_bytes or 0
    remote = bool(getattr(src, "remote", True))
    if st.offset == 0 and remote and size > FIRST_READ_MAX and hasattr(src, "read_tail"):
        st.offset = size - FIRST_READ_MAX
        st.note = f"read the last {FIRST_READ_MAX // 1024**2} MB of {size / 1024**2:.0f} MB (download it from Artifacts for all of it)"
        st.carry = "\x00skip"  # the first line is cut: drop it
        return bytes(src.read_tail(a.run.run_id, a.path, FIRST_READ_MAX))
    return bytes(b"".join(src.open_artifact(a.run.run_id, a.path, st.offset)))


def insights(svc: Any, store: PatternStore, ref: Any, stage: str | None = None) -> dict[str, Any]:
    r = svc.ref(ref)
    run = svc.get_run(r)
    pats = store.active()
    ver = _version(pats)
    compiled = [(p, p.compiled()) for p in pats]
    cat = log_catalog(svc, r, stage)
    files_out: list[dict[str, Any]] = []
    total = _Acc()
    for f in cat["files"]:
        a = svc.find_artifact(r, f["id"])
        if a is None:
            continue
        key = f"{r}|{f['id']}|{ver}"
        with _lock:
            st = _states.setdefault(key, _FileState())
        rec = {
            "id": f["id"],
            "path": f["path"],
            "stage": f["stage"],
            "label": f["label"],
            "size_bytes": f["size_bytes"],
        }
        try:
            # a running run's logs grow while the file listing may be cached: always ask for what came after the offset
            # (nothing new costs nothing); a finished run's file is read until its known size
            grow = not run.status.terminal or a.size_bytes is None or st.offset < a.size_bytes or st.offset == 0
            data = _read_new(svc, a, st) if grow else b""
        except Exception as exc:  # one unreadable file never hides the others
            rec["note"] = f"{type(exc).__name__}: {str(exc)[:160]}"
            files_out.append(rec)
            continue
        if data:
            text = data.decode("utf-8", errors="replace")
            if st.carry == "\x00skip":
                text = text.split("\n", 1)[1] if "\n" in text else ""
                st.carry = ""
            text = st.carry + text
            lines = text.split("\n")
            st.carry = lines.pop()  # an unfinished last line waits for the rest
            label = f"{f['stage'] + ' · ' if f['stage'] else ''}{f['path']}"
            for ln in lines:
                st.line_no += 1
                _feed(st.acc, ln, st.line_no, label, compiled)
            st.offset += len(data)
        if run.status.terminal and st.carry:  # a finished run: its last line is complete
            st.line_no += 1
            _feed(st.acc, st.carry, st.line_no, f"{f['stage'] + ' · ' if f['stage'] else ''}{f['path']}", compiled)
            st.carry = ""
        rec.update(lines=st.line_no, read_bytes=st.offset, note=st.note)
        files_out.append(rec)
        _merge(total, st.acc)
    return _report(r, run, stage, pats, total, files_out)


def _merge(into: _Acc, a: _Acc) -> None:
    into.lines += a.lines
    into.matched += a.matched
    for k, n in a.counts.items():
        into.counts[k] = into.counts.get(k, 0) + n
    for k, ev in a.first.items():
        into.first.setdefault(k, ev)
    for k, dq in a.last.items():
        into.last.setdefault(k, deque(maxlen=20)).extend(dq)
    into.facts.update(a.facts)
    for k, pts in a.series.items():
        into.series.setdefault(k, []).extend(pts)
    for t, u in a.uncovered.items():
        if t in into.uncovered:
            into.uncovered[t] = {**into.uncovered[t], "count": into.uncovered[t]["count"] + u["count"]}
        else:
            into.uncovered[t] = dict(u)


def _report(
    r: Any, run: Any, stage: str | None, pats: list[LogPattern], acc: _Acc, files: list[dict[str, Any]]
) -> dict[str, Any]:
    by_id = {p.id: p for p in pats}
    sev = {"problem": 0, "attention": 1, "info": 2}
    problems: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    for pid, n in acc.counts.items():
        p = by_id.get(pid)
        if p is None:
            continue
        item = {"pattern": pid, "title": p.title, "category": p.category, "severity": p.severity, "count": n,
                "first": acc.first.get(pid), "last": acc.last[pid][-1] if acc.last.get(pid) else None,
                "explain": p.explain, "source": p.source}  # fmt: skip
        if p.severity != "info" or p.category in ("error", "warning"):
            problems.append(item)
        elif p.kind == "event":
            events.append({**item, "recent": list(acc.last.get(pid, []))[-5:]})
    problems.sort(key=lambda x: (sev.get(str(x["severity"]), 3), -int(x["count"])))
    facts = []
    for f in acc.facts.values():
        p = by_id.get(f["pattern"])
        facts.append({**f, "title": p.title if p else f["pattern"], "category": p.category if p else "progress"})
    series = []
    for k, pts in acc.series.items():
        pid, fld = k.split(":", 1)
        p = by_id.get(pid)
        if len(pts) < 2:
            continue
        series.append({"key": k, "label": fld, "pattern": pid, "title": p.title if p else pid,
                       "x_label": (p.step_field if p and p.step_field else "line"), "x": [x for x, _ in pts], "y": [y for _, y in pts]})  # fmt: skip
    unc = sorted(acc.uncovered.values(), key=lambda u: (not u["noteworthy"], -u["count"]))[:40]
    return {
        "run": str(r), "status": run.status.value, "stage": stage, "state": "ok" if files else "no_logs",
        "files": files, "patterns": {"active": len(pats), "builtin": sum(p.source == "builtin" for p in pats),
                                      "code": sum(p.source == "code" for p in pats), "llm": sum(p.source == "llm" for p in pats),
                                      "user": sum(p.source == "user" for p in pats)},
        "coverage": {"lines": acc.lines, "matched": acc.matched, "uncovered": len(acc.uncovered)},
        "facts": sorted(facts, key=lambda f: (f["category"], f["title"], f["field"])),
        "problems": problems, "events": events, "series": series, "uncovered": unc,
    }  # fmt: skip


# ── proposals ───────────────────────────────────────────────────────────────────────────────────────────────────
_SYSTEM = (
    "You write regular expressions that extract information from a training job's log lines. Answer ONLY with JSON: "
    '{"patterns": [{"id", "title", "regex", "category", "kind", "severity", "step_field", "flags", "example", "explain"}]}. '
    "category: environment|data|progress|checkpoint|evaluation|warning|error. kind: event (each line is something that "
    "happened) | fact (named groups are facts about the run) | series (numeric named groups over step_field). "
    "severity: info|attention|problem. Python re syntax, named groups (?P<name>...). `example` MUST be one of the given "
    "lines copied exactly; the regex must match it. Never invent lines. One pattern per kind of line; skip lines that say "
    "nothing."
)


def _deterministic(cands: list[dict[str, Any]]) -> list[LogPattern]:
    out = []
    for i, u in enumerate(cands):
        ex = u["example"]
        if not u["noteworthy"]:
            p = from_line(ex, f"auto.{i}")
            if p:
                out.append(p)
            continue
        rx = (
            re.escape(template(ex)).replace("<n>", r"[-+]?[\d.eE+-]+").replace("<path>", r"\S+").replace("<id>", r"\w+")
        )
        rx = rx.replace(r"\ ", r"\s+")
        low = ex.lower()
        severity = "problem" if re.search(r"error|exception|traceback|fail|killed|nan|oom", low) else "attention"
        p = LogPattern(id=f"auto.{i}", title=template(ex)[:80], regex=rx, kind="event",
                       category="error" if severity == "problem" else "warning", severity=severity,
                       source="llm", status="proposed", example=ex)  # fmt: skip
        if not check(p):
            out.append(p)
    return out


def propose(
    svc: Any, store: PatternStore, llm: Any, ref: Any, stage: str | None = None, limit: int = 25
) -> dict[str, Any]:
    """Patterns for the lines no pattern explains yet: from the local LLM (grounded), else written deterministically.
    Stored as *proposed* — nothing changes until they are accepted."""
    from model_forensic.application.ports import ChatMessage, is_offline

    ins = insights(svc, store, ref, stage)
    cands = ins["uncovered"][:limit]
    if not cands:
        return {"proposed": [], "rejected": [], "model": None, "note": "every noteworthy line is already explained"}
    examples = {u["example"] for u in cands}
    rejected: list[dict[str, str]] = []
    got: list[LogPattern] = []
    model = None
    if llm is not None and not is_offline(llm):
        model = getattr(llm, "model", None)
        prompt = "Log lines (each seen `count` times; template = the line with numbers replaced):\n" + "\n".join(
            json.dumps({"line": u["example"], "count": u["count"], "template": u["template"]}) for u in cands
        )
        try:
            resp = llm.complete(
                [ChatMessage(role="system", content=_SYSTEM), ChatMessage(role="user", content=prompt)], json_mode=True
            )
            raw = json.loads(resp.content or "{}")
        except Exception as exc:  # the LLM failed or answered something else: the deterministic proposal stands in
            raw = {}
            rejected.append({"regex": "", "reason": f"LLM answer unusable: {type(exc).__name__}"})
        for item in raw.get("patterns", []) if isinstance(raw, dict) else []:
            try:
                p = LogPattern.model_validate(
                    {
                        **item,
                        "id": f"llm.{re.sub(r'[^a-z0-9]+', '-', str(item.get('id') or item.get('title') or 'p').lower())[:40]}",
                        "source": "llm",
                        "status": "proposed",
                    }
                )
            except Exception as exc:
                rejected.append(
                    {"regex": str(item.get("regex", ""))[:200], "reason": f"not a pattern: {type(exc).__name__}"}
                )
                continue
            if p.example not in examples:  # grounding: it must cite a line the run actually printed
                rejected.append({"regex": p.regex[:200], "reason": "its example is not one of the run's lines"})
                continue
            errs = check(p)
            if errs:
                rejected.append({"regex": p.regex[:200], "reason": "; ".join(errs)})
                continue
            got.append(p)
    if not got:
        got = _deterministic(cands)
    known = {p.regex for p in store.all()}
    saved = []
    for p in got:
        if p.regex in known:
            continue
        pid, n = p.id, 2
        existing = {q.id for q in store.all()}
        while pid in existing:
            pid, n = f"{p.id}-{n}", n + 1
        saved.append(store.put(p.model_copy(update={"id": pid})).model_dump())
        known.add(p.regex)
    return {"proposed": saved, "rejected": rejected, "model": model or "deterministic (no LLM connected)"}
