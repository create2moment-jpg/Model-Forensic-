"""Stable hashing for provenance and cache keys."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def stable_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def params_hash(obj: Any) -> str:
    return hashlib.sha256(stable_json(obj).encode()).hexdigest()[:16]
