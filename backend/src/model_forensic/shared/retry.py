"""Bounded exponential backoff with jitter."""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from model_forensic.shared.errors import BackendUnavailableError

T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 4
    base_delay_s: float = 0.5
    max_delay_s: float = 8.0
    jitter: float = 0.25

    def delay(self, attempt: int) -> float:
        raw = min(self.max_delay_s, self.base_delay_s * (2 ** (attempt - 1)))
        return float(raw * (1 + random.uniform(-self.jitter, self.jitter)))  # noqa: S311


def retry(
    fn: Callable[[], T],
    policy: RetryPolicy = RetryPolicy(),
    *,
    is_retryable: Callable[[BaseException], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    on_retry: Callable[[int, BaseException], None] | None = None,
) -> T:
    """Call ``fn`` until it succeeds, a non-retryable error occurs, or attempts run out."""
    check = is_retryable or (lambda e: isinstance(e, (BackendUnavailableError, ConnectionError, TimeoutError)))
    attempt = 0
    while True:
        attempt += 1
        try:
            return fn()
        except BaseException as exc:
            if attempt >= policy.max_attempts or not check(exc):
                raise
            if on_retry:
                on_retry(attempt, exc)
            sleep(policy.delay(attempt))
