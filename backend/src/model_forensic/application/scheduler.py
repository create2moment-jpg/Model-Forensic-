"""Background work for shared deployments: incremental sync, alerts for newly failed / regressed runs, nightly
study books. Runs in one daemon thread; every step is isolated so one failure never stops the loop."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from model_forensic.infrastructure.notify import Notifier
from model_forensic.shared.logging import get_logger

log = get_logger(__name__)


class Scheduler:
    def __init__(self, container: Any, notifier: Notifier | None = None) -> None:
        self.c = container
        self.cfg = container.settings.server
        self.notifier = notifier or Notifier(self.cfg.notify_webhook_env)
        self.last_sync: datetime | None = None
        self.seen_failed: set[str] = set()
        self._learned_failed = False  # the first scan only learns what already failed
        self.last_book_day: str | None = None
        self.history: list[dict[str, Any]] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # --------------------------------------------------------------- one tick (testable)
    def tick(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        out: dict[str, Any] = {"at": now.isoformat(), "synced": None, "alerts": 0, "books": []}
        try:
            since = self.last_sync - timedelta(minutes=5) if self.last_sync else None
            out["synced"] = self.c.service.sync(since=since)
            self.last_sync = now
        except Exception as exc:
            out["error"] = f"sync: {type(exc).__name__}"
        if "failed" in self.cfg.notify_on:
            out["alerts"] += self._failed_alerts()
        if self.cfg.nightly_books_utc and self._book_due(now):
            for sid in self.cfg.book_studies:
                try:
                    html_path, _ = self.c.study_book(sid)
                    out["books"].append(sid)
                    self.notifier.send(f"Study book updated: {self.c.studies.get(sid).title}", [str(html_path)])
                except Exception as exc:
                    out.setdefault("book_errors", []).append(f"{sid}: {type(exc).__name__}")
            self.last_book_day = now.date().isoformat()
        self.history = [*self.history, out][-100:]
        return out

    def _failed_alerts(self) -> int:
        res = self.c.service.search("status:failed", limit=50)
        new = [r for r in res.runs if str(r.ref) not in self.seen_failed]
        first = not self._learned_failed
        self._learned_failed = True
        self.seen_failed |= {str(r.ref) for r in res.runs}
        if first or not new:
            return 0
        lines = [
            f"{r.display_name or r.name} ({r.user or '?'}): {r.failure.message[:140] if r.failure else 'failed'}"
            for r in new[:10]
        ]
        self.notifier.send(f"{len(new)} new failed run(s)", lines)
        return len(new)

    def _book_due(self, now: datetime) -> bool:
        hh, mm = (int(x) for x in (self.cfg.nightly_books_utc or "0:0").split(":"))
        due = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        return now >= due and self.last_book_day != now.date().isoformat()

    # --------------------------------------------------------------- thread
    def start(self) -> None:
        if self.cfg.sync_minutes <= 0 or self._thread is not None:
            return

        def loop() -> None:
            while not self._stop.is_set():
                try:
                    self.tick()
                except Exception:
                    log.exception("scheduler tick failed")
                self._stop.wait(self.cfg.sync_minutes * 60)

        self._thread = threading.Thread(target=loop, name="mf-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
