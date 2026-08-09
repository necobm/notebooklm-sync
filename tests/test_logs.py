"""``logs.py`` — the only module allowed to open a log file.

These are the tests that opt *back into* logging: the suite's autouse fixture turns
it off, so everything here passes an explicit ``Settings`` pointing at ``tmp_path``,
the same "throwaway, never the repo's real one" rule ``db_path`` follows.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime
from pathlib import Path

import pytest

from notebooklm_sync import logs
from notebooklm_sync.config import Settings


def settings_for(log_dir: Path, *, level: str = "INFO", enabled: bool = True, days: int = 30):
    return Settings(
        notebooks={},
        log_dir=log_dir,
        log_enabled=enabled,
        log_level=level,
        log_retention_days=days,
    )


def emit(level: str, message: str) -> None:
    """Emit as a library module would — never by touching a handler directly."""
    getattr(logging.getLogger("notebooklm_sync.nlm"), level)(message)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# -- file naming ---------------------------------------------------------


def test_the_file_is_named_by_date_and_command(tmp_path):
    path = logs.log_path(tmp_path, "sync", today=date(2026, 8, 9))
    assert path == tmp_path / "2026-08-09-sync.log"


def test_the_default_date_is_today(tmp_path):
    today = datetime.now().astimezone().date()
    assert logs.log_path(tmp_path, "expand").name == f"{today:%Y-%m-%d}-expand.log"


def test_a_session_writes_to_that_file(tmp_path):
    with logs.session("sync", settings=settings_for(tmp_path), today=date(2026, 8, 9)):
        emit("info", "hello")

    assert read(tmp_path / "2026-08-09-sync.log").endswith("hello\n")


# -- one file per command, per day ---------------------------------------


def test_two_runs_of_one_command_share_a_file_with_distinct_tokens(tmp_path):
    settings = settings_for(tmp_path)
    with logs.session("sync", settings=settings, today=date(2026, 8, 9)) as first:
        emit("info", "run one")
    with logs.session("sync", settings=settings, today=date(2026, 8, 9)) as second:
        emit("info", "run two")

    assert first != second
    body = read(tmp_path / "2026-08-09-sync.log")
    assert "run one" in body and "run two" in body
    # The token is what makes a shared daily file attributable line by line.
    assert first in body and second in body


def test_two_commands_get_two_files(tmp_path):
    settings = settings_for(tmp_path)
    with logs.session("sync", settings=settings, today=date(2026, 8, 9)):
        emit("info", "syncing")
    with logs.session("expand", settings=settings, today=date(2026, 8, 9)):
        emit("info", "expanding")

    assert "syncing" in read(tmp_path / "2026-08-09-sync.log")
    assert "expanding" in read(tmp_path / "2026-08-09-expand.log")


def test_two_dates_get_two_files(tmp_path):
    settings = settings_for(tmp_path)
    with logs.session("sync", settings=settings, today=date(2026, 8, 8)):
        emit("info", "yesterday")
    with logs.session("sync", settings=settings, today=date(2026, 8, 9)):
        emit("info", "today")

    assert (tmp_path / "2026-08-08-sync.log").exists()
    assert (tmp_path / "2026-08-09-sync.log").exists()


def test_the_directory_is_created_on_demand(tmp_path):
    nested = tmp_path / "var" / "log"
    with logs.session("sync", settings=settings_for(nested), today=date(2026, 8, 9)):
        emit("info", "hello")

    assert (nested / "2026-08-09-sync.log").exists()


# -- levels --------------------------------------------------------------


def test_info_excludes_what_debug_admits(tmp_path):
    quiet, loud = tmp_path / "quiet", tmp_path / "loud"
    with logs.session("sync", settings=settings_for(quiet), today=date(2026, 8, 9)):
        emit("debug", "GET https://example.com")
        emit("info", "kept")
    with logs.session("sync", settings=settings_for(loud, level="DEBUG"), today=date(2026, 8, 9)):
        emit("debug", "GET https://example.com")

    assert "GET https://example.com" not in read(quiet / "2026-08-09-sync.log")
    assert "GET https://example.com" in read(loud / "2026-08-09-sync.log")


def test_an_unreadable_level_falls_back_to_info(tmp_path):
    # A typo must not silence the log — the same argument `_parse_bool` rests on.
    with logs.session("sync", settings=settings_for(tmp_path, level="LOUD"), today=date(2026, 8, 9)):
        emit("info", "still recorded")

    assert "still recorded" in read(tmp_path / "2026-08-09-sync.log")


def test_the_record_carries_level_token_and_module(tmp_path):
    with logs.session("sync", settings=settings_for(tmp_path), today=date(2026, 8, 9)) as token:
        emit("error", "boom")

    line = read(tmp_path / "2026-08-09-sync.log").strip()
    assert "ERROR" in line
    assert token in line
    assert "test_logs" in line  # %(module)s, not the full dotted logger name
    assert "\x1b[" not in line  # no ANSI ever reaches a file


# -- off -----------------------------------------------------------------


def test_disabled_writes_nothing_and_creates_no_directory(tmp_path):
    target = tmp_path / "var" / "log"
    with logs.session("sync", settings=settings_for(target, enabled=False)) as token:
        emit("info", "should not be written")

    assert token  # still yielded, which is what keeps cli.py branch-free
    assert not target.exists()


def test_a_session_that_emits_nothing_leaves_no_empty_file(tmp_path):
    with logs.session("sync", settings=settings_for(tmp_path), today=date(2026, 8, 9)):
        pass

    assert not (tmp_path / "2026-08-09-sync.log").exists()


def test_an_unwritable_directory_does_not_fail_the_command(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("I am a file, not a directory", encoding="utf-8")

    with logs.session("sync", settings=settings_for(blocker / "log")) as token:
        emit("info", "the command still runs")

    assert token


# -- handler hygiene -----------------------------------------------------


def test_the_handler_is_detached_when_the_session_ends(tmp_path):
    logger = logging.getLogger(logs.ROOT)
    before = list(logger.handlers)
    level_before = logger.level

    with logs.session("sync", settings=settings_for(tmp_path), today=date(2026, 8, 9)):
        assert len(logger.handlers) == len(before) + 1

    assert logger.handlers == before
    assert logger.level == level_before


def test_a_raising_body_still_detaches_the_handler(tmp_path):
    logger = logging.getLogger(logs.ROOT)
    before = list(logger.handlers)

    def crash() -> None:
        with logs.session("sync", settings=settings_for(tmp_path), today=date(2026, 8, 9)):
            emit("info", "before the crash")
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        crash()

    assert logger.handlers == before
    assert "before the crash" in read(tmp_path / "2026-08-09-sync.log")


def test_repeated_sessions_do_not_duplicate_records(tmp_path):
    """A leaked handler would double every later line — the CliRunner failure mode."""
    settings = settings_for(tmp_path)
    for _ in range(3):
        with logs.session("sync", settings=settings, today=date(2026, 8, 9)):
            pass
    with logs.session("sync", settings=settings, today=date(2026, 8, 9)):
        emit("info", "once")

    assert read(tmp_path / "2026-08-09-sync.log").count("once") == 1


def test_records_go_nowhere_when_no_session_is_open(tmp_path):
    emit("error", "library use, no handler")
    assert list(tmp_path.iterdir()) == []


# -- retention -----------------------------------------------------------


def _aged(path: Path, *, days: int) -> Path:
    path.write_text("old\n", encoding="utf-8")
    stamp = time.time() - days * 86_400
    os.utime(path, (stamp, stamp))
    return path


def test_prune_removes_old_files_and_spares_fresh_ones(tmp_path):
    old = _aged(tmp_path / "2026-01-01-sync.log", days=45)
    fresh = _aged(tmp_path / "2026-08-08-sync.log", days=1)

    assert logs.prune(tmp_path, days=30) == 1
    assert not old.exists()
    assert fresh.exists()


def test_prune_ignores_everything_that_is_not_a_log(tmp_path):
    keep = _aged(tmp_path / "notes.txt", days=99)
    assert logs.prune(tmp_path, days=30) == 0
    assert keep.exists()


def test_prune_with_zero_days_keeps_everything(tmp_path):
    old = _aged(tmp_path / "2020-01-01-sync.log", days=999)
    assert logs.prune(tmp_path, days=0) == 0
    assert old.exists()


def test_prune_survives_a_missing_directory(tmp_path):
    assert logs.prune(tmp_path / "nope", days=30) == 0


def test_a_session_prunes_before_it_writes(tmp_path):
    old = _aged(tmp_path / "2026-01-01-sync.log", days=45)

    with logs.session("sync", settings=settings_for(tmp_path), today=date(2026, 8, 9)):
        emit("info", "hello")

    assert not old.exists()
    assert (tmp_path / "2026-08-09-sync.log").exists()
