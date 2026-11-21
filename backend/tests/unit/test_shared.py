from pathlib import Path

import pytest

from model_forensic.shared.errors import BackendUnavailableError, ValidationError
from model_forensic.shared.logging import redact, register_secret
from model_forensic.shared.paths import safe_join
from model_forensic.shared.retry import RetryPolicy, retry


@pytest.mark.parametrize("bad", ["../etc/passwd", "/etc/passwd", "a/../../b", "C:/win", "a\x00b", "", "..\\..\\x"])
def test_safe_join_rejects_traversal(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ValidationError):
        safe_join(tmp_path, bad)


def test_safe_join_accepts_nested(tmp_path: Path) -> None:
    assert safe_join(tmp_path, "outputs/x/y.pt") == (tmp_path / "outputs/x/y.pt").resolve()


def test_redaction() -> None:
    register_secret("supersecretvalue123")
    out = redact("key=supersecretvalue123 Authorization: Bearer abcdefghijkl sk-ABCDEFGHIJKLMNOP api_key=zzzz1234")
    assert (
        "supersecretvalue123" not in out
        and "abcdefghijkl" not in out
        and "sk-ABCDEFGHIJKLMNOP" not in out
        and "zzzz1234" not in out
    )


def test_retry_bounded() -> None:
    calls = []

    def fn() -> int:
        calls.append(1)
        raise BackendUnavailableError("down")

    with pytest.raises(BackendUnavailableError):
        retry(fn, RetryPolicy(max_attempts=3), sleep=lambda s: None)
    assert len(calls) == 3


def test_retry_does_not_retry_permanent_errors() -> None:
    calls = []

    def fn() -> int:
        calls.append(1)
        raise ValueError("bad")

    with pytest.raises(ValueError):
        retry(fn, RetryPolicy(max_attempts=5), sleep=lambda s: None)
    assert len(calls) == 1
