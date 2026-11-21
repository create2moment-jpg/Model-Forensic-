"""Structured JSON logging with secret redaction.

Secrets are redacted twice: known secret values registered at runtime are replaced verbatim,
and common credential shapes (bearer tokens, ``api_key=...``) are masked by pattern. This is a
defence in depth; code should still never pass secrets to a logger.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
from datetime import datetime, timezone
from typing import Any

_REDACTED = "***REDACTED***"
_PATTERNS = [
    re.compile(r"(?i)(bearer\s+)[a-z0-9._\-]{8,}"),
    re.compile(r"(?i)((?:api[_-]?key|token|secret|password|sig)\s*[=:]\s*)[^\s&\"',]{4,}"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{12,}\b"),
]
_known_secrets: set[str] = set()
_lock = threading.Lock()


def register_secret(value: str | None) -> None:
    """Register a secret value that must never appear in log output."""
    if value and len(value) >= 4:
        with _lock:
            _known_secrets.add(value)


def redact(text: str) -> str:
    with _lock:
        secrets = sorted(_known_secrets, key=len, reverse=True)
    for s in secrets:
        text = text.replace(s, _REDACTED)
    for pat in _PATTERNS:
        text = pat.sub(lambda m: m.group(1) + _REDACTED, text) if pat.groups else pat.sub(_REDACTED, text)
    return text


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(fields)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return redact(json.dumps(payload, default=str))


def configure_logging(level: str = "INFO", json_output: bool = True) -> None:
    handler = logging.StreamHandler(sys.stderr)
    if json_output:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger("model_forensic")
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    root.propagate = False


def get_logger(name: str) -> logging.LoggerAdapter[logging.Logger]:
    return _FieldsAdapter(logging.getLogger(name), {})


class _FieldsAdapter(logging.LoggerAdapter):  # type: ignore[type-arg]
    """``log.info("msg", run_id=...)`` style structured fields."""

    def log(self, level: int, msg: object, *args: Any, **kwargs: Any) -> None:
        std = {k: kwargs.pop(k) for k in ("exc_info", "stack_info", "stacklevel") if k in kwargs}
        if self.isEnabledFor(level):
            self.logger.log(level, msg, *args, extra={"fields": kwargs}, **std)

    def debug(self, msg: object, *args: Any, **kw: Any) -> None:
        self.log(logging.DEBUG, msg, *args, **kw)

    def info(self, msg: object, *args: Any, **kw: Any) -> None:
        self.log(logging.INFO, msg, *args, **kw)

    def warning(self, msg: object, *args: Any, **kw: Any) -> None:
        self.log(logging.WARNING, msg, *args, **kw)

    def error(self, msg: object, *args: Any, **kw: Any) -> None:
        self.log(logging.ERROR, msg, *args, **kw)

    def exception(self, msg: object, *args: Any, **kw: Any) -> None:
        kw.setdefault("exc_info", True)
        self.log(logging.ERROR, msg, *args, **kw)
