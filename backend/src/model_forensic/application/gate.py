"""``mf gate <config.yaml>``: launch-time checks on a training config, before a job is submitted.

Catches the harness traps of a training study before they cost a run: no validation loop, a checkpoint monitor
that will never be logged, a ruler different from the run you want to compare with. CI-friendly exit codes.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from model_forensic.analysis.config_diff import flatten
from model_forensic.analysis.study_checks import config_values, ruler_identity


@dataclass
class GateResult:
    id: str
    title: str
    status: str  # FAIL | WARN | PASS | SKIP
    detail: str


def _val_configured(flat: Mapping[str, Any]) -> tuple[bool | None, str]:
    for k, v in flat.items():
        leaf = k.rsplit(".", 1)[-1]
        if leaf == "limit_val_batches" and v in (0, 0.0, "0"):
            return False, f"{k} = 0 disables validation"
    keys = [
        k
        for k in flat
        if any(
            t in k.rsplit(".", 1)[-1].lower()
            for t in ("check_val_every_n_epoch", "val_check_interval", "val_dataloader", "validation")
        )
    ]
    data = [k for k in flat if ("val" in k.lower().split(".")[-2:][0] if "." in k else False) and "data" in k.lower()]
    if keys or data:
        return True, f"validation configured ({(keys or data)[0]})"
    return None, "no validation settings found (val dataloader / check_val_every_n_epoch / val_check_interval)"


def gate(
    config: Mapping[str, Any], logged: Iterable[str] | None = None, reference: Mapping[str, Any] | None = None
) -> list[GateResult]:
    flat = flatten(dict(config))
    out: list[GateResult] = []
    rulers = [v for v in config_values(flat, "combined_metrics_yaml").values() if isinstance(v, str)]
    ruler = ruler_identity(rulers[0]) if rulers else None
    if reference is not None:
        rref = [
            v for v in config_values(flatten(dict(reference)), "combined_metrics_yaml").values() if isinstance(v, str)
        ]
        ref_ruler = ruler_identity(rref[0]) if rref else None
        if ruler and ref_ruler and ruler != ref_ruler:
            out.append(
                GateResult(
                    "G1",
                    "Same ruler as the reference run",
                    "FAIL",
                    f"{ruler} vs reference {ref_ruler}: the headline scores will not be comparable",
                )
            )
        else:
            out.append(
                GateResult(
                    "G1",
                    "Same ruler as the reference run",
                    "PASS" if ruler else "SKIP",
                    ruler or "no combined_metrics_yaml",
                )
            )
    else:
        out.append(GateResult("G1", "Ruler recorded", "PASS" if ruler else "WARN", ruler or "no combined_metrics_yaml"))
    val, vdetail = _val_configured(flat)
    out.append(
        GateResult("G2", "Validation loop configured", "PASS" if val else "FAIL" if val is False else "WARN", vdetail)
    )
    mons = {
        k: v
        for k, v in flat.items()
        if isinstance(v, str) and (k.endswith("checkpoint_monitor") or k.endswith(".monitor"))
    }
    if not mons:
        out.append(GateResult("G3", "Checkpoint monitor will be logged", "SKIP", "no checkpoint monitor configured"))
    else:
        names = set(logged or [])
        for k, m in mons.items():
            if m.startswith("val") and val is not True:
                out.append(
                    GateResult(
                        "G3",
                        "Checkpoint monitor will be logged",
                        "FAIL",
                        f"{k} = {m!r} watches a validation metric but validation is not configured",
                    )
                )
            elif names and not any(n == m or n.endswith("/" + m) for n in names):
                out.append(
                    GateResult(
                        "G3",
                        "Checkpoint monitor will be logged",
                        "FAIL",
                        f"{k} = {m!r} never appears in the reference run's logged metrics",
                    )
                )
            elif names:
                out.append(
                    GateResult("G3", "Checkpoint monitor will be logged", "PASS", f"{m} is logged by the reference run")
                )
            else:
                out.append(
                    GateResult(
                        "G3",
                        "Checkpoint monitor will be logged",
                        "WARN",
                        f"{m}: pass --against <run> to verify it is logged",
                    )
                )
    return out
