"""The only place in this package that shells out to the upstream ``notebooklm`` CLI.

Nothing else under ``src/`` may import ``subprocess`` — that rule is what lets the
rest of the codebase be tested offline against a fake binary on ``PATH``.

Upstream quirks encoded here (see the notebooklm-cli skill for the full list):

* errors arrive **in band** on stdout as ``{"error": true, ...}``, sometimes with
  exit code 0, so the payload is inspected rather than the exit status alone;
* ``source wait`` uses exit 2 for *timeout*, distinct from 1 for *failure*;
* ``auth check`` is local-only unless given ``--test``.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .errors import AuthError, NlmError, NlmTimeout
from .models import RemoteSource, WaitStatus

#: Records only — ``logs.py`` decides whether anything is listening. Payloads are
#: never logged: ``auth check --test`` describes the Google account behind them.
log = logging.getLogger(__name__)

BINARY = "notebooklm"

LOGIN_HINT = "Run `notebooklm login` to authenticate (it opens a browser)."


@dataclass(frozen=True)
class WaitResult:
    status: WaitStatus
    message: str = ""

    @property
    def is_ready(self) -> bool:
        return self.status is WaitStatus.READY


def _parse_created_at(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        # Python 3.11+ parses a trailing "Z" natively.
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def source_from_payload(payload: dict) -> RemoteSource:
    """Build a RemoteSource from one ``source list --json`` row."""
    return RemoteSource(
        id=str(payload.get("id") or ""),
        url=payload.get("url"),
        title=payload.get("title"),
        kind=payload.get("kind") or payload.get("type"),
        status=payload.get("status"),
        created_at=_parse_created_at(payload.get("created_at")),
    )


class NlmClient:
    """Thin typed wrapper over the upstream CLI."""

    def __init__(
        self,
        *,
        profile: str | None = None,
        timeout: int = 300,
        binary: str = BINARY,
        on_call: Callable[[list[str]], None] | None = None,
    ) -> None:
        self.profile = profile
        self.timeout = timeout
        self.binary = binary
        #: argv of every invocation, in order — handy in tests and for -v output.
        self.calls: list[list[str]] = []
        #: Notified with each argv as it is invoked. This adapter never prints;
        #: rendering belongs to the caller (``cli.py``).
        self.on_call = on_call

    # -- plumbing --------------------------------------------------------

    def _argv(self, args: list[str]) -> list[str]:
        argv = [self.binary]
        if self.profile:
            argv += ["--profile", self.profile]
        argv += args
        if "--json" not in argv:
            argv.append("--json")
        return argv

    def _run(self, args: list[str], *, timeout: int | None = None) -> subprocess.CompletedProcess:
        argv = self._argv(args)
        self.calls.append(argv)
        if self.on_call is not None:
            self.on_call(argv)
        env = dict(os.environ)
        if self.profile:
            env["NOTEBOOKLM_PROFILE"] = self.profile
        log.info("$ %s", " ".join(argv))
        started = time.monotonic()
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout or self.timeout,
                env=env,
                check=False,
            )
        except FileNotFoundError as exc:
            log.error("not on PATH: %s", self.binary)
            raise NlmError(
                "NOT_INSTALLED",
                f"`{self.binary}` not found on PATH. Install it with "
                f"`uv tool install notebooklm-py`.",
                argv=argv,
            ) from exc
        except subprocess.TimeoutExpired as exc:
            log.error("timed out after %ss", timeout or self.timeout)
            raise NlmTimeout(
                "TIMEOUT", f"`{' '.join(argv)}` timed out after {timeout or self.timeout}s", argv=argv
            ) from exc
        log.info("rc=%d in %s", proc.returncode, _took(started))
        return proc

    def _payload(self, proc: subprocess.CompletedProcess, argv: list[str]) -> Any:
        """Decode stdout JSON and raise on an in-band or exit-code error."""
        text = (proc.stdout or "").strip()
        data: Any = None
        if text:
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = None

        # In-band errors can arrive with exit code 0 — check the payload first.
        if isinstance(data, dict) and data.get("error"):
            message = str(data.get("message") or "Unknown error")
            code = data.get("code")
            log.error("upstream error code=%s: %s", code, message)
            if _looks_like_auth(message, code):
                raise AuthError(f"{message}\n{LOGIN_HINT}")
            raise NlmError(code, message, argv=argv)

        if proc.returncode != 0:
            message = (proc.stderr or text or "").strip() or f"exit code {proc.returncode}"
            log.error("upstream failed rc=%d: %s", proc.returncode, message)
            if _looks_like_auth(message, None):
                raise AuthError(f"{message}\n{LOGIN_HINT}")
            raise NlmError(None, message, argv=argv)

        if data is None and text:
            raise NlmError(None, f"Could not parse JSON output: {text[:200]}", argv=argv)
        return data

    def _call(self, args: list[str], *, timeout: int | None = None) -> Any:
        proc = self._run(args, timeout=timeout)
        return self._payload(proc, self._argv(args))

    # -- commands --------------------------------------------------------

    def auth_check(self, *, test: bool = True) -> dict:
        """Verify authentication.

        ``test=True`` forces a network round-trip. Without it the check is local
        only and reports ``ok`` even when the session has expired — so the default
        here is the useful one.
        """
        args = ["auth", "check"]
        if test:
            args.append("--test")
        payload = self._call(args)
        data = payload if isinstance(payload, dict) else {}
        if data.get("status") not in (None, "ok"):
            raise AuthError(f"Authentication check failed: {data.get('status')}\n{LOGIN_HINT}")
        return data

    def list_notebooks(self) -> list[dict]:
        payload = self._call(["list"])
        if isinstance(payload, dict):
            payload = payload.get("notebooks", [])
        return list(payload or [])

    def list_sources(self, notebook_id: str) -> list[RemoteSource]:
        payload = self._call(["source", "list", "-n", notebook_id])
        if isinstance(payload, dict):
            payload = payload.get("sources", [])
        return [source_from_payload(row) for row in (payload or []) if isinstance(row, dict)]

    def add_source(
        self,
        notebook_id: str,
        url: str,
        *,
        title: str | None = None,
        type_: str | None = None,
    ) -> RemoteSource | None:
        """Add ``url``. Always pass the user's original URL, never a normalized one."""
        args = ["source", "add", url, "-n", notebook_id]
        if type_:
            args += ["--type", type_]
        if title:
            args += ["--title", title]
        payload = self._call(args)
        if isinstance(payload, dict):
            data = payload.get("source") if isinstance(payload.get("source"), dict) else payload
            if data.get("id"):
                return source_from_payload(data)
        return None

    def refresh_source(self, notebook_id: str, source_id: str) -> dict:
        payload = self._call(["source", "refresh", source_id, "-n", notebook_id])
        return payload if isinstance(payload, dict) else {}

    def is_stale(self, notebook_id: str, source_id: str) -> bool | None:
        """Read the JSON ``stale`` field.

        Never branch on this command's exit code: under ``--exit-on-stale`` upstream
        inverts it (0 = stale), which makes exit-code logic silently wrong.
        """
        payload = self._call(["source", "stale", source_id, "-n", notebook_id])
        if isinstance(payload, dict) and isinstance(payload.get("stale"), bool):
            return payload["stale"]
        return None

    def wait_source(self, notebook_id: str, source_id: str, *, timeout: int = 120) -> WaitResult:
        """Wait for ingestion, mapping upstream's three exit codes explicitly.

        0 = ready, 1 = failed, **2 = timeout**. A timeout is not a failure: the
        source is still processing and the next run will reconcile it.
        """
        args = ["source", "wait", source_id, "-n", notebook_id, "--timeout", str(timeout)]
        # Give the subprocess headroom over the poll timeout so our own kill doesn't
        # pre-empt upstream's own timeout handling.
        proc = self._run(args, timeout=timeout + 30)

        if proc.returncode == 0:
            log.info("wait %s: %s", source_id, WaitStatus.READY.value)
            return WaitResult(WaitStatus.READY)
        if proc.returncode == 2:
            # Recorded explicitly: exit 2 is a timeout, not a failure, and a log that
            # left it implicit would read as if the source had broken.
            log.info("wait %s: %s (exit 2)", source_id, WaitStatus.TIMEOUT.value)
            return WaitResult(WaitStatus.TIMEOUT, f"still processing after {timeout}s")

        message = (proc.stderr or proc.stdout or "").strip()
        try:
            data = json.loads((proc.stdout or "").strip() or "{}")
            if isinstance(data, dict):
                if data.get("error") and _looks_like_auth(str(data.get("message")), data.get("code")):
                    raise AuthError(f"{data.get('message')}\n{LOGIN_HINT}")
                message = str(data.get("message") or message)
        except json.JSONDecodeError:
            pass
        log.error("wait %s: %s — %s", source_id, WaitStatus.FAILED.value, message)
        return WaitResult(WaitStatus.FAILED, message or "source processing failed")


def _took(started: float) -> str:
    """Wall-clock since ``started``, as ``812ms`` — the unit a slow run is read in."""
    return f"{int((time.monotonic() - started) * 1000)}ms"


def _looks_like_auth(message: str | None, code: str | None) -> bool:
    text = f"{code or ''} {message or ''}".lower()
    return any(
        marker in text
        for marker in ("authentication expired", "not authenticated", "re-authenticate", "login")
    )
