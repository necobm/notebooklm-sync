"""The live progress display, driven directly against a string console.

`CliRunner` is never a TTY, so the live path is structurally invisible to
`test_cli.py` — which is exactly the point of the `is_terminal` gate, and exactly why
the rendering itself needs testing here instead. These tests render frames with
`force_terminal=True` and assert on what a user would see.
"""

from __future__ import annotations

import io
import re
import time

import pytest
from rich.console import Console

from notebooklm_sync import progress
from notebooklm_sync.progress import (
    ASCII_GLYPHS,
    GLYPHS,
    LiveReporter,
    NullReporter,
    make_reporter,
)

ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")


class _Buffer:
    """A writable file that answers `.encoding` — which is where Rich, and so
    `_supports_unicode`, reads the console's encoding from. `StringIO.encoding` is
    read-only, hence the delegation."""

    def __init__(self, encoding: str = "utf-8") -> None:
        self.encoding = encoding
        self._inner = io.StringIO()

    def write(self, text: str) -> int:
        return self._inner.write(text)

    def flush(self) -> None:
        self._inner.flush()

    def getvalue(self) -> str:
        return self._inner.getvalue()


def console(width: int = 90, *, encoding: str = "utf-8", **kwargs) -> Console:
    return Console(file=_Buffer(encoding), force_terminal=True, width=width, **kwargs)


def frame(reporter: LiveReporter, width: int = 90) -> str:
    """Render the reporter's current state as plain text — no styles at all, which
    is how a `NO_COLOR` or monochrome user sees it."""
    out = console(width, no_color=True)
    out.print(reporter._render())
    return ANSI.sub("", out.file.getvalue())


# -- glyph and word, never colour alone ---------------------------------------


def test_a_resolved_phase_shows_its_glyph_label_and_result():
    reporter = LiveReporter(console())
    reporter.phase("sources")
    reporter.finish("41 in notebook")

    text = frame(reporter)
    assert GLYPHS["done"] in text
    assert "sources" in text
    assert "41 in notebook" in text


def test_failure_differs_from_success_without_any_colour():
    """The whole point of the glyph: `no_color=True` strips every style, and the two
    states must still be distinguishable."""
    ok = LiveReporter(console())
    ok.phase("auth")
    ok.finish("credentials ok")

    bad = LiveReporter(console())
    bad.phase("auth")
    bad.finish("session expired", ok=False)

    assert GLYPHS["done"] in frame(ok)
    assert GLYPHS["failed"] in frame(bad)
    assert GLYPHS["done"] not in frame(bad)
    assert frame(ok) != frame(bad)


def test_resolved_phases_stay_visible_while_later_ones_run():
    reporter = LiveReporter(console())
    reporter.phase("auth")
    reporter.finish("credentials ok")
    reporter.phase("sources")

    text = frame(reporter)
    assert "credentials ok" in text
    assert "sources" in text


# -- the ASCII fallback --------------------------------------------------------


def test_a_non_utf8_console_gets_the_ascii_glyphs():
    """Mojibake would destroy the only signal a monochrome reader has."""
    reporter = LiveReporter(console(encoding="ascii"))  # not a UTF-8 terminal
    reporter.phase("auth")
    reporter.finish("credentials ok")

    text = frame(reporter)
    assert ASCII_GLYPHS["done"] in text
    assert GLYPHS["done"] not in text


# -- the bar, and only where a total is real -----------------------------------


def test_the_action_bar_counts_towards_its_total():
    reporter = LiveReporter(console())
    reporter.phase("syncing")
    reporter.start_actions(41)
    for _ in range(12):
        reporter.advance()

    assert "12/41" in frame(reporter)

    for _ in range(29):
        reporter.advance()
    assert "41/41" in frame(reporter)


def test_discovery_reports_a_count_and_never_a_bar():
    """Discovery's total is unknowable until the site answers, so a percentage off
    the cap would be a confident lie."""
    reporter = LiveReporter(console())
    reporter.phase("discovery")
    reporter.busy("rule 1/2 · 18 pages fetched")

    text = frame(reporter)
    assert "18 pages fetched" in text
    assert "━" not in text
    assert "%" not in text


def test_a_long_url_is_truncated_rather_than_wrapped():
    reporter = LiveReporter(console(width=60))
    reporter.phase("syncing")
    reporter.start_actions(5)
    reporter.detail("add   https://example.com/blog/" + "long-slug-" * 10)

    lines = [line for line in frame(reporter, width=60).splitlines() if line.strip()]
    assert "…" in "\n".join(lines)
    assert all(len(line) <= 60 for line in lines)


def test_the_glyph_and_label_survive_a_narrow_terminal():
    """Without a ratio on the last column Rich shrinks *these* to fit a long URL,
    and at 60 columns the spinner vanishes and the phase name truncates."""
    reporter = LiveReporter(console(width=60))
    reporter.phase("syncing")
    reporter.start_actions(5)
    reporter.detail("add   https://example.com/" + "long-slug-" * 10)

    assert "syncing" in frame(reporter, width=60)


def test_a_crawl_rules_modifiers_are_not_eaten_as_markup():
    """Square brackets are Rich markup. The reporter renders through `Text`, which
    takes its content literally — this pins that property."""
    reporter = LiveReporter(console())
    reporter.phase("discovery")
    reporter.detail("https://site.com/*[except=blog]")

    assert "[except=blog]" in frame(reporter)


# -- switching it off ----------------------------------------------------------


def test_a_non_tty_console_gets_the_null_reporter():
    plain = Console(file=io.StringIO(), width=90)  # not a terminal
    assert not plain.is_terminal
    assert isinstance(make_reporter(plain, enabled=True), NullReporter)


def test_disabled_gets_the_null_reporter_even_on_a_tty():
    assert isinstance(make_reporter(console(), enabled=False), NullReporter)


def test_a_tty_with_it_enabled_gets_the_live_reporter():
    assert isinstance(make_reporter(console(), enabled=True), LiveReporter)


def test_the_null_reporter_writes_absolutely_nothing():
    out = console()
    reporter = make_reporter(out, enabled=False)
    with reporter:
        reporter.phase("syncing")
        reporter.start_actions(3)
        reporter.detail("add https://example.com/a")
        reporter.busy("waiting for ingestion")
        reporter.advance()
        reporter.idle()
        reporter.finish("done")
    assert out.file.getvalue() == ""


def test_the_grace_window_keeps_a_fast_run_from_flashing():
    """Most runs are cache hits that finish inside 200ms, and a bar that appears and
    vanishes is worse than no bar."""
    out = console()
    reporter = LiveReporter(out)
    with reporter:
        reporter.phase("sources")
        reporter.finish("41 in notebook")
    assert out.file.getvalue() == ""


def test_the_display_starts_once_the_grace_window_has_passed(monkeypatch):
    out = console()
    reporter = LiveReporter(out)
    monkeypatch.setattr(reporter, "_created_at", reporter._created_at - 1.0)
    with reporter:
        reporter.phase("sources")
        assert reporter._live is not None
    assert reporter._live is None  # stopped on the way out
    assert out.file.getvalue() != ""


def test_the_display_appears_during_a_blocking_call(monkeypatch):
    """The regression this exists for.

    The work being reported on *blocks* — `source wait` sits in `subprocess.run` for
    up to two minutes. An earlier version started the display on "the next update
    after 200ms", so nothing was drawn until the slow step had already finished,
    which is precisely when a progress display stops being worth anything. It has to
    be a timer: one `phase()` call and then silence must still produce a display.
    """
    monkeypatch.setattr(progress, "GRACE_SECONDS", 0.05)
    out = console()
    reporter = LiveReporter(out)
    with reporter:
        reporter.phase("auth")  # the only call — as if auth_check now blocked
        assert reporter._live is None  # still inside the grace window
        time.sleep(0.25)  # ... the "blocking subprocess call" ...
        assert reporter._live is not None, "the timer never started the display"
    assert "auth" in ANSI.sub("", out.file.getvalue())


def test_the_display_pulls_current_state_rather_than_a_snapshot(monkeypatch):
    """`Live` is handed `_Screen`, not a rendered snapshot, so its refresh thread
    picks up state written after it started. Mutating state *without* going through a
    method that calls `_refresh()` is what distinguishes the two."""
    monkeypatch.setattr(progress, "GRACE_SECONDS", 0.0)
    out = console()
    reporter = LiveReporter(out)
    with reporter:
        reporter.phase("sources")
        time.sleep(0.2)
        with reporter._lock:
            reporter._phases[-1].detail = "PULLED-NOT-PUSHED"  # nothing is notified
        time.sleep(0.3)
    assert "PULLED-NOT-PUSHED" in ANSI.sub("", out.file.getvalue())


@pytest.mark.parametrize("boom", [KeyboardInterrupt, RuntimeError])
def test_the_terminal_is_restored_however_the_block_exits(monkeypatch, boom):
    out = console()
    reporter = LiveReporter(out)
    monkeypatch.setattr(reporter, "_created_at", reporter._created_at - 1.0)
    with pytest.raises(boom), reporter:
        reporter.phase("syncing")
        raise boom()
    assert reporter._live is None
    assert "\x1b[?25h" in out.file.getvalue()  # cursor shown again
