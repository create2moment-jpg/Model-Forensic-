"""Error model.

Every error raised across a layer boundary derives from :class:`ForensicError` and carries a
stable ``code`` so the API and UI can map failures without parsing messages.
"""

from __future__ import annotations


class ForensicError(Exception):
    """Base class for all expected, user-facing failures."""

    code: str = "internal_error"
    retryable: bool = False

    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(ForensicError):
    code = "not_found"


class ValidationError(ForensicError):
    code = "invalid_request"


class BackendUnavailableError(ForensicError):
    """A remote backend (Azure ML, storage, LLM) failed in a way that may succeed later."""

    code = "backend_unavailable"
    retryable = True


class PermissionDeniedError(ForensicError):
    code = "permission_denied"


class ConfirmationRequiredError(ForensicError):
    """An expensive or mutating operation needs explicit user confirmation."""

    code = "confirmation_required"


class IntegrityError(ForensicError):
    code = "integrity_error"


class InsufficientDiskError(ForensicError):
    code = "insufficient_disk"


class CancelledError(ForensicError):
    code = "cancelled"


class InsufficientEvidenceError(ForensicError):
    code = "insufficient_evidence"
