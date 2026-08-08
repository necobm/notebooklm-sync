"""Typed errors and the exit codes they map to.

Exit codes are part of the tool's contract (see README and the sync-engine skill):

    0  success
    1  one or more source actions failed
    2  config or manifest error
    3  auth failure
"""

from __future__ import annotations

EXIT_OK = 0
EXIT_ACTION_FAILED = 1
EXIT_CONFIG = 2
EXIT_AUTH = 3


class SyncError(Exception):
    """Base class for errors that map to a specific exit code."""

    exit_code = EXIT_ACTION_FAILED


class ConfigError(SyncError):
    """Bad or missing environment configuration."""

    exit_code = EXIT_CONFIG


class ManifestError(SyncError):
    """Malformed or unreadable source manifest."""

    exit_code = EXIT_CONFIG


class AuthError(SyncError):
    """Upstream authentication is missing or expired.

    The message should always tell the user to run ``notebooklm login``, since that
    is interactive and cannot be done on their behalf.
    """

    exit_code = EXIT_AUTH


class NlmError(SyncError):
    """The upstream ``notebooklm`` CLI reported a failure.

    Note that upstream signals errors *in band* on stdout as
    ``{"error": true, "code": ..., "message": ...}``, sometimes with exit code 0 —
    so this is raised from payload inspection, not only from the exit status.
    """

    def __init__(self, code: str | None, message: str, *, argv: list[str] | None = None) -> None:
        self.code = code
        self.argv = argv or []
        super().__init__(message)


class NlmTimeout(NlmError):
    """The upstream CLI exceeded its subprocess timeout."""
