"""Who is making the request. Local mode: "local". Server mode: set per request by the auth middleware."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class User:
    id: str  # stable id (email / UPN / oid)
    name: str
    admin: bool = False


LOCAL = User("local", "local", admin=True)
_current: ContextVar[User] = ContextVar("mf_user", default=LOCAL)


def current() -> User:
    return _current.get()


def set_current(user: User) -> object:
    return _current.set(user)


def reset(token: object) -> None:
    _current.reset(token)  # type: ignore[arg-type]
