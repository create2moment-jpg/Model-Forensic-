"""Work orders: every task an operator model (Copilot · Luna · local Qwen) does in the target environment.

One registry, ``workorders/index.yaml`` (ADR-0013): trigger, inputs, prompt, output contract, scope, validation,
token budget, model tier, escalation. This module loads it, scopes harness briefs (``focus``), checks an operator's
output against its contract, and writes the **escalation packet** — the only thing that goes back to an expensive
model after two failed validations.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

Tier = Literal["local", "mid", "top"]
OutputKind = Literal["knowledge", "expectations", "facets", "config", "report", "plot-specs", "calibration"]


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: OutputKind
    path: str
    schema_: str | None = Field(None, alias="schema")


class WorkOrder(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str = ""
    title: str
    tier: Tier
    status: Literal["ready", "planned"] = "ready"
    trigger: str
    task: str | None = None
    prompt: str
    inputs: list[str] = Field(default_factory=list)
    reads: list[str] = Field(default_factory=list)
    output: Output
    scope: list[str] = Field(default_factory=list)
    validate_: str = Field("", alias="validate")
    focus: dict[str, list[str]] = Field(default_factory=dict)
    checklist: list[str] = Field(default_factory=list)
    escalate: str = ""


def root() -> Path:
    from model_forensic.application.harness import harness_dir

    return harness_dir().parent


def index_path() -> Path:
    return root() / "workorders" / "index.yaml"


@lru_cache(maxsize=4)
def _load(path: str, mtime: float) -> dict[str, WorkOrder]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    d = data.get("defaults", {})
    out: dict[str, WorkOrder] = {}
    for oid, raw in (data.get("orders") or {}).items():
        body = {"escalate": d.get("escalate", ""), **raw}
        out[oid] = WorkOrder.model_validate({**body, "id": oid})
        if out[oid].task and out[oid].output.kind == "knowledge" and not out[oid].validate_:
            out[oid].validate_ = f"mf work check {oid} <file>"
        if out[oid].output.kind == "expectations" and not out[oid].validate_:
            out[oid].validate_ = f"mf work check {oid} <file>"
    return out


def load() -> dict[str, WorkOrder]:
    p = index_path()
    if not p.is_file():
        return {}
    return _load(str(p), p.stat().st_mtime)


def focus_for_task(task: str) -> dict[str, list[str]] | None:
    for w in load().values():
        if w.task == task and w.focus:
            return w.focus
    return None


def card(w: WorkOrder) -> str:
    """The work order as the operator reads it (Markdown)."""
    lines = [
        f"# {w.id} — {w.title}",
        "",
        f"- **When:** {w.trigger}",
        f"- **Tier:** {w.tier} ({'a local model can do it alone' if w.tier == 'local' else 'Copilot / Luna with the repository' if w.tier == 'mid' else 'needs a top model'})"
        + (" — **planned, not built yet**" if w.status == "planned" else ""),
        f"- **Follow:** `{w.prompt}`",
    ]
    if w.task:
        lines.append(
            f"- **Brief:** `mf work brief {w.id} --run <run>` (what the task needs first, then everything the run has)"
        )
    for i in w.inputs:
        lines.append(f"- **Run first:** `{i}`")
    if w.reads:
        lines.append("- **Open in the training repository:** " + "; ".join(w.reads))
    lines += [
        f"- **Write:** {w.output.kind} → `{w.output.path}`"
        + (f" (schema `{w.output.schema_}`)" if w.output.schema_ else ""),
        "- **Only these paths:** " + (", ".join(f"`{p}`" for p in w.scope) if w.scope else "the output path above"),
        f"- **Validate:** `{w.validate_ or 'mf work check ' + w.id + ' <file>'}`",
        f"- **Escalate when:** {w.escalate} → `mf work escalate {w.id} --file <your output>`",
        "",
        "## How to work",
        "",
        "1. **Collect first, decide second.** Pass 1: copy into a scratch list every fact you need, each with where it "
        "came from (brief line or `file:line`). Pass 2: fill the output only from that list.",
        "2. **Fill the template** in the prompt, field by field, in order — do not compose a document of your own.",
        "3. **Never invent** a name, key, class or number. Not found → leave the field out and say so in `notes`.",
        "4. **One task, one output file**, inside the paths above.",
        "",
        "## Before you validate, check",
        "",
        *[f"- [ ] {c}" for c in w.checklist],
        "- [ ] Every name / key / number in your output is in the brief or behind a `file:line` you opened",
        "- [ ] Nothing in the output contradicts the brief (values, stages, which run)",
        "- [ ] Fields you could not determine are left out, not guessed",
        "",
        "Then run the validation; repair once from its errors; still failing → escalate.",
    ]
    return "\n".join(lines) + "\n"


def check(w: WorkOrder, file: Path, dossier: Path | None = None) -> list[str]:
    """Errors of an operator's output against the work order's contract ([] = valid)."""
    if not file.is_file():
        return [f"{file}: no such file"]
    text = file.read_text(encoding="utf-8")
    kind = w.output.kind
    if kind == "knowledge":
        from model_forensic.knowledge.store import parse

        entries, errors = parse(text)
        if not entries and not errors:
            errors = ["no entries"]
        return [str(e) for e in errors] + [f"{e.id}: no code_refs (cite file:line)" for e in entries
                                           if e.kind != "setup" and not e.source.code_refs]  # fmt: skip
    if kind == "expectations":
        from model_forensic.application.expectations import Expectations

        try:
            e = Expectations.model_validate(yaml.safe_load(text) or {})
        except Exception as err:
            return [str(err)]
        out = [] if e.terms else ["no terms"]
        out += [f"term {t.name}: no code_refs" for t in e.terms if not t.code_refs]
        if not (e.runs or e.when_config):
            out.append("neither runs nor when_config: the file matches no run")
        return out
    if kind == "facets":
        from model_forensic.application.facets import validate

        return validate(file)
    if kind == "report":
        return _check_report(text, dossier)
    if kind == "calibration":
        from model_forensic.application.question_calibration import check as check_calibration

        return check_calibration(file)
    return []  # config / plot-specs: the card's validation command decides


_HEADINGS = ("diagnosis", "observations", "differential", "where to look", "next steps", "confidence")


def _check_report(text: str, dossier: Path | None) -> list[str]:
    out: list[str] = []
    low = text.lower()
    out += [f"missing section: {h}" for h in _HEADINGS if h not in low]
    cited = set(re.findall(r"\[?(E-[A-Z]+\d+)\]?", text))
    if len(cited) < 3:
        out.append(f"cites {len(cited)} evidence ids — cite the dossier's [E-…] ids for every claim (at least 3)")
    if dossier is not None and dossier.is_file():
        known = set(re.findall(r"\[(E-[A-Z]+\d+)\]", dossier.read_text(encoding="utf-8")))
        bad = sorted(cited - known)
        if bad:
            out.append(f"cites ids that are not in the dossier: {', '.join(bad[:10])}")
    return out


def escalate(
    w: WorkOrder, file: Path | None, note: str = "", dossier: Path | None = None, max_chars: int = 6000
) -> str:
    """The escalation packet (≈2k tokens): the card, the validator's errors, the output (trimmed), what was tried."""
    from model_forensic import __version__

    errors = check(w, file, dossier) if file is not None else ["(no output file)"]
    body = file.read_text(encoding="utf-8") if file is not None and file.is_file() else ""
    if len(body) > max_chars:
        body = body[: max_chars // 2] + "\n… (trimmed) …\n" + body[-max_chars // 2 :]
    rev = "unknown"
    git = shutil.which("git")
    if git:
        try:
            r = subprocess.run(  # noqa: S603 - fixed arguments, our own checkout
                [git, "-C", str(root()), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5,
                check=False,
            )  # fmt: skip
            rev = r.stdout.strip() or "unknown"
        except (OSError, subprocess.SubprocessError):
            pass
    return "\n".join([
        f"# Escalation — {w.id}: {w.title}",
        "",
        f"Model Forensic {__version__} ({rev}); {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC; tier {w.tier}.",
        "",
        "## What was asked",
        f"Follow `{w.prompt}`; write {w.output.kind} to `{w.output.path}`; validate with `{w.validate_}`.",
        "",
        "## What the validator says",
        *([f"- {e}" for e in errors] or ["- (valid — escalated for another reason, see the note)"]),
        "",
        "## What the operator tried / why it is stuck",
        note or "(no note)",
        "",
        "## The output",
        "```",
        body or "(empty)",
        "```",
        "",
        "Answer with: the fix to the output, or the change to the work order / prompt / contract (a commit), and why.",
    ]) + "\n"  # fmt: skip


def save_packet(text: str, knowledge_folder: Path, oid: str) -> Path:
    p = knowledge_folder / "escalations" / f"{oid}-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p
