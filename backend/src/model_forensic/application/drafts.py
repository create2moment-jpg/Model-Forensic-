"""Drafts: half-finished work kept on the server so it can be resumed from any browser.

A draft is whatever a form holds before it becomes a real object (a new study, a guided study, a comparison, a
knowledge entry, a question): its ``kind``, a ``title`` for lists, the ``route`` that resumes it, and the form's
``payload``. Drafts are per user, autosaved by the UI, listed from summaries (never the payloads), and pruned when
stale. Real objects (studies, jobs) keep their own lifecycle; *Continue where you left off* combines them.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from model_forensic.shared.errors import NotFoundError, ValidationError

KIND = "draft"
MAX_PAYLOAD = 256 * 1024
MAX_PER_USER = 200
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}")
_KIND = re.compile(r"[a-z][a-z0-9-]{0,39}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class DraftService:
    docs: Any  # DocumentStore
    max_age_days: int = 90

    def _check(self, draft_id: str, kind: str, route: str) -> None:
        if not _ID.fullmatch(draft_id):
            raise ValidationError("draft id: letters, digits, '_', '.', ':', '-' (1–80 chars)")
        if not _KIND.fullmatch(kind):
            raise ValidationError("draft kind: lowercase letters, digits, '-'")
        if not route.startswith("/") or route.startswith("//") or len(route) > 1000:
            raise ValidationError("draft route must be an in-app path starting with '/'")

    def save(
        self, draft_id: str, owner: str, kind: str, title: str, route: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Create or update (autosave). The owner of an existing draft cannot change."""
        self._check(draft_id, kind, route)
        if len(json.dumps(payload, default=str)) > MAX_PAYLOAD:
            raise ValidationError(f"draft payload larger than {MAX_PAYLOAD // 1024} KiB")
        try:
            cur = self.docs.get_raw(KIND, draft_id)
            if cur.get("owner") != owner:
                raise ValidationError("this draft belongs to someone else")
            created = cur.get("created_at") or _now()
        except NotFoundError:
            created = _now()
            mine = self.docs.list_summaries(KIND, where={"owner": owner}, limit=1)[1]
            if mine >= MAX_PER_USER:
                self.prune(owner, keep=MAX_PER_USER - 1)
        body = {
            "id": draft_id,
            "owner": owner,
            "kind": kind,
            "title": (title or "Untitled draft").strip()[:200],
            "route": route,
            "payload": payload,
            "created_at": created,
            "updated_at": _now(),
        }
        self.docs.put(KIND, draft_id, body, summary={k: v for k, v in body.items() if k != "payload"})
        return body

    def get(self, draft_id: str, owner: str) -> dict[str, Any]:
        d: dict[str, Any] = self.docs.get_raw(KIND, draft_id)
        if d.get("owner") != owner:
            raise NotFoundError(f"draft {draft_id!r} not found")
        return d

    def list(self, owner: str, kind: str | None = None, limit: int = 50) -> tuple[list[dict[str, Any]], int]:
        where = {"owner": owner, **({"kind": kind} if kind else {})}
        return self.docs.list_summaries(KIND, where=where, limit=limit)  # type: ignore[no-any-return]

    def delete(self, draft_id: str, owner: str) -> None:
        self.get(draft_id, owner)
        self.docs.delete(KIND, draft_id)

    def prune(self, owner: str | None = None, keep: int | None = None) -> int:
        """Drop drafts older than ``max_age_days`` (and beyond ``keep`` newest for ``owner``)."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.max_age_days)).isoformat()
        where = {"owner": owner} if owner else None
        items, _ = self.docs.list_summaries(KIND, where=where, limit=100_000)
        drop = [d for d in items if str(d.get("updated_at", "")) < cutoff]
        if keep is not None:
            drop += [d for d in items[keep:] if d not in drop]
        for d in drop:
            self.docs.delete(KIND, d["id"])
        return len(drop)
