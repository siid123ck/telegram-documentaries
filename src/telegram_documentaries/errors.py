"""Typed application errors.

The message on every error is internal, safe, and deliberately free of provider
codes, stack traces, or model prose. The routing layer decides what a user sees.
"""

from __future__ import annotations


class ConfigError(Exception):
    """Raised when the application configuration is missing or invalid.

    The message is safe to print to stderr: it names environment variables and
    remediation steps but never includes secret values.
    """


class VisionError(Exception):
    """Base class for recoverable vision-stage failures.

    Routing maps any :class:`VisionError` to the gentle "resend the photo"
    nudge; stages log the concrete error type but never expose it to users.
    """


class VisionUnavailable(VisionError):
    """The vision provider could not serve the request."""

    def __init__(self, message: str = "vision provider unavailable") -> None:
        super().__init__(message)


class ProviderTimeout(VisionError):
    """The vision provider did not answer within the allotted time."""

    def __init__(self, message: str = "vision provider timed out") -> None:
        super().__init__(message)


class RateLimited(VisionError):
    """The vision provider throttled the request."""

    def __init__(self, message: str = "vision provider rate limited") -> None:
        super().__init__(message)


class ProviderRejected(VisionError):
    """The vision provider rejected the request (HTTP 400, 401, 403, or 404).

    Non-retryable: the same request would fail again, so it fails fast.
    """

    def __init__(self, message: str = "vision provider rejected the request") -> None:
        super().__init__(message)


class InvalidResponse(VisionError):
    """The vision provider returned data that failed contract validation.

    ``detail`` is a short validation summary used only to build a repair request
    for the model. It is never shown to the user and never logged.
    """

    def __init__(
        self, message: str = "invalid vision response", *, detail: str = ""
    ) -> None:
        super().__init__(message)
        self.detail = detail
