"""Exception hierarchy for pydigitalstrom."""

from __future__ import annotations


class DssError(Exception):
    """Base of all pydigitalstrom errors."""


class DssConnectionError(DssError):
    """Network-level failure (no response received)."""


class DssTimeoutError(DssError):
    """HTTP request timed out."""


class DssAuthError(DssError):
    """Login failed or token revoked.

    Attributes:
        app_token_invalid: True if the persistent app-token itself was rejected
            (re-approval in dSS web UI needed). False for transient session-level
            failures that auto-relogin can resolve.
    """

    def __init__(self, message: str, *, app_token_invalid: bool = False) -> None:
        super().__init__(message)
        self.app_token_invalid = app_token_invalid


class DssProtocolError(DssError):
    """Unexpected JSON response shape, or ok=false without a recoverable cause."""


class DssNotFoundError(DssError):
    """Requested resource (zone, device, action) not found."""


class DssTypeMismatchError(DssError):
    """property/getString called on non-string field (or similar type mismatch).

    Use PropertyTreeWalker which checks types via getChildren first.
    """


class DssSubscriptionError(DssError):
    """event/subscribe or event/unsubscribe failed."""
