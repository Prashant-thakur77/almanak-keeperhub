"""Typed errors for the KeeperHub Direct Execution API.

Only the outcomes the submitter branches on get their own class. Everything
else is a ``KeeperHubAPIError`` carrying the HTTP status and the JSON body.
"""

from __future__ import annotations

from typing import Any


class KeeperHubError(Exception):
    """Base class for every error raised by this package."""


class KeeperHubAPIError(KeeperHubError):
    def __init__(self, message: str, *, status: int, payload: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.payload = payload or {}

    @property
    def code(self) -> str | None:
        code = self.payload.get("code")
        return code if isinstance(code, str) else None

    @property
    def retryable(self) -> bool | None:
        value = self.payload.get("retryable")
        return value if isinstance(value, bool) else None


class KeeperHubAuthError(KeeperHubAPIError):
    """401, or 403 ``insufficient_scope``: the credential cannot do this."""


class KeeperHubRateLimited(KeeperHubAPIError):
    def __init__(self, message: str, *, status: int, payload: dict[str, Any] | None, retry_after_seconds: int) -> None:
        super().__init__(message, status=status, payload=payload)
        self.retry_after_seconds = retry_after_seconds


class KeeperHubIdempotencyConflict(KeeperHubAPIError):
    """409 ``idempotency_conflict``: same key, different body. Never retry with this key."""

    @property
    def original_execution_id(self) -> str | None:
        value = self.payload.get("originalExecutionId")
        return value if isinstance(value, str) else None


class KeeperHubIdempotencyInProgress(KeeperHubAPIError):
    """409 ``idempotency_in_progress``: the first request is still running. Retry the same key."""


class KeeperHubUnavailable(KeeperHubAPIError):
    """503: simulator or execution infrastructure failure, nothing was decided."""


class UndecodableCalldata(KeeperHubError):
    """The 4-byte selector is not in the offline index, so the call is refused (fail closed)."""

    def __init__(self, selector: str, to: str) -> None:
        super().__init__(
            f"Refusing to submit calldata with unknown selector {selector} to {to}: "
            "KeeperHub needs a function name and typed arguments, and this package will not guess them. "
            "Add the ABI to almanak_keeperhub/signatures.json or set ALMANAK_KEEPERHUB_SELECTOR_LOOKUP=openchain."
        )
        self.selector = selector
        self.to = to
