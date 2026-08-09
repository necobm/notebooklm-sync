"""The only place in this package that opens a log file or attaches a handler.

Nothing else under ``src/`` may call ``basicConfig``, ``addHandler`` or construct a
``FileHandler``. Every other module only ever does::

    log = logging.getLogger(__name__)

which is the existing "the library notifies, only ``cli.py`` renders" rule stated in
stdlib-logging terms: with no handler attached — library use, the test suite — a
record goes nowhere and costs almost nothing.

One CLI invocation is one :func:`session`. It writes to
``<log_dir>/<YYYY-MM-DD>-<command>.log``, so every run of a command on a given date
lands in one file, and every line carries a short run token so two interleaved runs
stay attributable.
"""

from __future__ import annotations

import contextlib
import logging
import time
import uuid
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import cycle avoidance only
    from .config import Settings

#: The package logger every ``getLogger(__name__)`` in ``src/`` rolls up to.
ROOT = "notebooklm_sync"

FORMAT = "%(asctime)s %(levelname)-5s %(token)s %(module)-9s %(message)s"

#: A NullHandler here is what keeps records silent when no session is open. It also
#: stops logging's "last resort" handler from printing WARNING+ straight to stderr,
#: which would put log records in the middle of the progress display.
logging.getLogger(ROOT).addHandler(logging.NullHandler())


def _level(name: str) -> int:
    """``"debug"`` -> ``logging.DEBUG``; anything unrecognised -> ``INFO``.

    A typo must not silence the log, for the same reason ``_parse_bool`` treats an
    unknown value as true: the failure you can see is the better one.
    """
    resolved = logging.getLevelName((name or "").strip().upper())
    return resolved if isinstance(resolved, int) else logging.INFO


class _TokenFilter(logging.Filter):
    """Stamps the run token onto every record, so no call site has to."""

    def __init__(self, token: str) -> None:
        super().__init__()
        self.token = token

    def filter(self, record: logging.LogRecord) -> bool:
        record.token = self.token
        return True


def log_path(log_dir: Path, command: str, *, today: date | None = None) -> Path:
    """``var/log/2026-08-09-sync.log``.

    ``today`` is a parameter so a test can pin the date without patching the clock.

    The *local* date, deliberately: the formatter's timestamps are local too, and a
    file dated in UTC holding local times would misfile every late-evening run in
    half the world's timezones.
    """
    day = today or datetime.now().astimezone().date()
    return Path(log_dir) / f"{day:%Y-%m-%d}-{command}.log"


def prune(log_dir: Path, *, days: int) -> int:
    """Delete ``*.log`` older than ``days``; return how many went. ``0`` keeps all.

    Best-effort by design: housekeeping must never be able to fail the command the
    user actually asked for. Non-recursive, and it only ever matches files.
    """
    if days <= 0:
        return 0
    cutoff = time.time() - days * 86_400
    removed = 0
    try:
        candidates = sorted(Path(log_dir).glob("*.log"))
    except OSError:
        return 0
    for path in candidates:
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


@contextlib.contextmanager
def session(command: str, *, settings: Settings, today: date | None = None) -> Iterator[str]:
    """Attach the log file for one CLI invocation; yield its run token.

    Teardown is not bookkeeping. ``CliRunner`` invokes the app many times in one
    process, so a handler that outlived its command would double every later record
    and hold a file descriptor open — hence the ``finally``.
    """
    token = uuid.uuid4().hex[:8]
    if not settings.log_enabled:
        # Yielding a token either way is what keeps cli.py branch-free.
        yield token
        return

    directory = Path(settings.log_dir)
    logger = logging.getLogger(ROOT)
    previous_level = logger.level
    handler: logging.Handler | None = None
    try:
        prune(directory, days=settings.log_retention_days)
        directory.mkdir(parents=True, exist_ok=True)
        # delay=True: a session that emits nothing leaves no empty file behind.
        handler = logging.FileHandler(
            log_path(directory, command, today=today), mode="a", encoding="utf-8", delay=True
        )
        handler.setFormatter(logging.Formatter(FORMAT))
        handler.addFilter(_TokenFilter(token))
        handler.setLevel(_level(settings.log_level))
        logger.setLevel(_level(settings.log_level))
        logger.addHandler(handler)
    except OSError:
        # An unwritable log directory is not a reason to refuse to sync.
        if handler is not None:
            logger.removeHandler(handler)
            handler.close()
        logger.setLevel(previous_level)
        yield token
        return

    try:
        yield token
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)
        with contextlib.suppress(OSError, ValueError):
            handler.flush()
        handler.close()
