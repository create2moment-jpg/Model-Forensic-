"""Notifications to a Teams incoming webhook (or any JSON webhook). The URL comes from an environment variable
named in ``server.notify_webhook_env`` — never from a config file. Failures are logged, never raised."""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

from model_forensic.shared.logging import get_logger

log = get_logger(__name__)


def card(title: str, lines: list[str], link: str | None = None) -> dict[str, Any]:
    """A MessageCard (accepted by Teams incoming webhooks; generic receivers get plain JSON fields too)."""
    body: dict[str, Any] = {
        "@type": "MessageCard",
        "@context": "https://schema.org/extensions",
        "summary": title,
        "title": title,
        "text": "<br>".join(lines[:20]),
        "lines": lines[:50],
    }
    if link:
        body["potentialAction"] = [
            {"@type": "OpenUri", "name": "Open in Model Forensic", "targets": [{"os": "default", "uri": link}]}
        ]
    return body


class Notifier:
    def __init__(self, env_var: str | None) -> None:
        self.env_var = env_var
        self.sent: list[dict[str, Any]] = []  # last messages (diagnostics / tests)

    @property
    def url(self) -> str | None:
        u = os.environ.get(self.env_var or "") if self.env_var else None
        return u if u and u.startswith("https://") else None

    def send(self, title: str, lines: list[str], link: str | None = None) -> bool:
        msg = card(title, lines, link)
        self.sent = [*self.sent, msg][-50:]
        url = self.url
        if url is None:
            return False
        try:
            req = urllib.request.Request(  # noqa: S310 - https enforced in .url
                url, data=json.dumps(msg).encode(), headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 - https enforced in .url
                return bool(200 <= int(r.status) < 300)
        except Exception as exc:
            log.warning("notification failed", error=type(exc).__name__)
            return False
