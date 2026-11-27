"""Search query language and a deterministic natural-language rewriter.

DSL (space separated, free text for everything else)::

    status:failed  kind:training  exp:sparse4d-fusion  user:alex  compute:a100-cluster
    branch:main  commit:3f9c  tag:role=baseline  tag:feature  cfg:temporal.num_frames=8
    NDS>0.6  AMOTA<0.45  since:7d  before:2026-11-01  parent:pipeline-nightly  is:fav  is:top  is:stage

Natural-language queries ("find failed sparse detector training jobs from last week") are
rewritten into the DSL by deterministic rules first. An LLM-backed rewriter may be plugged in
behind the same interface; its output is always shown to the user as DSL for transparency.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from model_forensic.application.ports import SearchFilters

_METRIC_RE = re.compile(r"^(?P<m>[A-Za-z][\w/\-\[\]=,.]*)(?P<op>>=|<=|>|<)(?P<v>-?\d+(?:\.\d+)?(?:e-?\d+)?)$")
_DUR_RE = re.compile(r"^(\d+)([hdwm])$")
_STATUS_WORDS = {
    "failed": "failed",
    "failing": "failed",
    "crashed": "failed",
    "completed": "completed",
    "finished": "completed",
    "succeeded": "completed",
    "running": "running",
    "canceled": "canceled",
    "cancelled": "canceled",
    "queued": "queued",
}
_KIND_WORDS = {
    "training": "training",
    "train": "training",
    "evaluation": "evaluation",
    "eval": "evaluation",
    "pipeline": "pipeline",
    "pipelines": "pipeline",
    "sweep": "sweep",
}
_STOP = {
    "find",
    "show",
    "me",
    "all",
    "the",
    "jobs",
    "job",
    "runs",
    "run",
    "with",
    "from",
    "using",
    "where",
    "that",
    "and",
    "experiments",
    "of",
    "in",
    "a",
    "an",
    "list",
    "for",
}


def _duration(text: str) -> timedelta | None:
    m = _DUR_RE.match(text)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    return {"h": timedelta(hours=n), "d": timedelta(days=n), "w": timedelta(weeks=n), "m": timedelta(days=30 * n)}[unit]


def _date(text: str, now: datetime) -> datetime | None:
    d = _duration(text)
    if d is not None:
        return now - d
    try:
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_query(q: str, now: datetime | None = None) -> SearchFilters:
    now = now or datetime.now(timezone.utc)
    f = SearchFilters()
    free: list[str] = []
    try:
        tokens = shlex.split(q)
    except ValueError:
        tokens = q.split()
    for tok in tokens:
        key, sep, val = tok.partition(":")
        k = key.lower()
        if sep and val:
            if k == "status":
                f = replace(f, statuses=(*f.statuses, *val.lower().split(",")))
            elif k == "kind":
                f = replace(f, kinds=(*f.kinds, *val.lower().split(",")))
            elif k in {"exp", "experiment"} and val.lower() in {"starred", "star", "fav"}:
                f = replace(f, starred_experiments_only=True)
            elif k in {"exp", "experiment"}:
                f = replace(f, experiment=val)
            elif k == "user":
                f = replace(f, user=val)
            elif k == "compute":
                f = replace(f, compute=val)
            elif k == "branch":
                f = replace(f, branch=val)
            elif k in {"commit", "sha"}:
                f = replace(f, commit=val)
            elif k == "backend":
                f = replace(f, backend=val)
            elif k == "parent":
                f = replace(f, parent=val)
            elif k == "tag":
                tk, _, tv = val.partition("=")
                f = replace(f, tags=(*f.tags, (tk, tv or None)))
            elif k in {"cfg", "param"}:
                pk, _, pv = val.partition("=")
                f = replace(f, params=(*f.params, (pk, pv)))
            elif k == "since":
                f = replace(f, created_after=_date(val, now))
            elif k == "before":
                f = replace(f, created_before=_date(val, now))
            elif k == "is" and val in {"fav", "favorite", "starred"}:
                f = replace(f, favorites_only=True)
            elif k == "is" and val.lower() in {"top", "toplevel", "pipeline"}:
                f = replace(f, stages="hide")
            elif k == "is" and val.lower() in {"stage", "stages", "child"}:
                f = replace(f, stages="only")
            else:
                free.append(tok)
            continue
        m = _METRIC_RE.match(tok)
        if m:
            v = float(m.group("v"))
            op = m.group("op")
            lo, hi = (v, None) if op.startswith(">") else (None, v)
            f = replace(f, metric_ranges=(*f.metric_ranges, (m.group("m"), lo, hi)))
            continue
        free.append(tok)
    text = " ".join(free).strip()
    return replace(f, text=text or None)


def rewrite_natural_language(q: str, now: datetime | None = None) -> tuple[str, list[str]]:
    """Rewrite common natural-language patterns into DSL. Returns (dsl, explanations)."""
    now = now or datetime.now(timezone.utc)
    s = " " + q.lower().strip() + " "
    out: list[str] = []
    notes: list[str] = []

    for phrase, dur in (
        ("last week", "7d"),
        ("past week", "7d"),
        ("this week", "7d"),
        ("yesterday", "2d"),
        ("today", "1d"),
        ("last month", "30d"),
        ("past month", "30d"),
    ):
        if f" {phrase} " in s:
            out.append(f"since:{dur}")
            notes.append(f"'{phrase}' → since:{dur}")
            s = s.replace(f" {phrase} ", " ")
    m = re.search(r"last (\d+) days?", s)
    if m:
        out.append(f"since:{m.group(1)}d")
        s = s.replace(m.group(0), " ")
    m = re.search(r"(\d+)[- ]frame", s)
    if m:
        out.append(f"cfg:temporal.num_frames={m.group(1)}")
        notes.append(f"'{m.group(0)}' → cfg:temporal.num_frames={m.group(1)}")
        s = s.replace(m.group(0), " ").replace("temporal windows", " ").replace("temporal window", " ")
    m = re.search(r"(\w+) (?:loss )?enabled", s)
    if m and m.group(1) not in _STOP:
        out.append(f"cfg:loss.{m.group(1)}.enabled=true")
        notes.append(f"'{m.group(0)}' → cfg:loss.{m.group(1)}.enabled=true")
        s = s.replace(m.group(0), " ")
    # "NDS improved but AMOTA dropped" needs a baseline; we surface it as a comparison hint.
    m = re.search(r"(\w+) improved but (\w+) (?:dropped|decreased|regressed)", s)
    if m:
        notes.append(
            f"'{m.group(0)}' requires a baseline: use Compare with metric filters {m.group(1).upper()}↑ {m.group(2).upper()}↓"
        )
        s = s.replace(m.group(0), " ")
    words = []
    for w in s.split():
        if w in _STATUS_WORDS:
            out.append(f"status:{_STATUS_WORDS[w]}")
        elif w in _KIND_WORDS:
            out.append(f"kind:{_KIND_WORDS[w]}")
        elif w in {"favorite", "favorites", "starred"}:
            out.append("is:fav")
        elif w not in _STOP:
            words.append(w)
    dsl = " ".join([*out, *words])
    return dsl, notes


def looks_natural(q: str) -> bool:
    return ":" not in q and not _METRIC_RE.match(q.strip()) and len(q.split()) >= 3
