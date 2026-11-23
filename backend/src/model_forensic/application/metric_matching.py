"""Which metrics of two runs are the same thing under different names — e.g. a local Lightning run's
``train_loss_step`` and an Azure ML run's ``train/loss`` — so they can be compared.

Deterministic: names are normalised (separators, ``train_`` / ``val_`` prefixes, ``_step`` / ``_epoch`` suffixes,
case) and compared; a pair is suggested only when the normalised names agree, or are very close and unambiguous.
The user confirms each pair; confirmed pairs become aliases (``interface.metric_aliases``).
"""

from __future__ import annotations

import difflib
import re
from typing import Any

_PREFIX = [(re.compile(r"^(train|training)[_\-.]"), "train/"), (re.compile(r"^(val|valid|validation)[_\-.]"), "val/"),
           (re.compile(r"^(test|eval|evaluation)[_\-.]"), "test/")]  # fmt: skip
_SUFFIX = re.compile(r"([_/](step|epoch))$")


def normalise(name: str) -> str:
    base, _, dims = name.partition("[")
    n = base.strip().lower()
    for rx, rep in _PREFIX:
        n = rx.sub(rep, n)
    n = _SUFFIX.sub("", n)
    n = re.sub(r"[_\-.]+", "_", n).replace("/_", "/")
    return n + (f"[{dims}" if dims else "")


def suggest(ref_keys: list[str], cand_raw: dict[str, str]) -> dict[str, Any]:
    """``ref_keys``: the reference run's metric keys; ``cand_raw``: the other run's key → raw name. Pairs for the
    metrics only one of them has: ``{raw, key, to, score, why}`` (rename the candidate's raw name to ``to``)."""
    ref = set(ref_keys)
    only_ref = sorted(ref - set(cand_raw))
    only_cand = sorted(k for k in cand_raw if k not in ref)
    norm_ref: dict[str, list[str]] = {}
    for k in only_ref:
        norm_ref.setdefault(normalise(k), []).append(k)
    taken: set[str] = set()
    out = []
    for k in only_cand:
        nk = normalise(k)
        exact = [r for r in norm_ref.get(nk, []) if r not in taken]
        if len(exact) == 1:
            out.append({"raw": cand_raw[k], "key": k, "to": exact[0], "score": 1.0, "why": "same name once normalised"})
            taken.add(exact[0])
            continue
        close = difflib.get_close_matches(nk, [n for n in norm_ref if any(r not in taken for r in norm_ref[n])], n=2,
                                          cutoff=0.88)  # fmt: skip
        if len(close) == 1 and len(norm_ref[close[0]]) == 1:
            to = norm_ref[close[0]][0]
            score = round(difflib.SequenceMatcher(None, nk, close[0]).ratio(), 2)
            out.append({"raw": cand_raw[k], "key": k, "to": to, "score": score, "why": "very similar name"})
            taken.add(to)
    return {"only_reference": only_ref, "only_candidate": only_cand, "matched": len(ref & set(cand_raw)),
            "raw_of": {k: cand_raw[k] for k in only_cand},  # an alias renames the raw name, not the key
            "suggestions": sorted(out, key=lambda x: -x["score"])}  # fmt: skip
