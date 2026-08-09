"""Live progress reporting — **the only module in this package that renders it**.

The rest of the package does not know a display exists. ``engine.py`` and
``discovery.py`` announce events through optional callbacks and print nothing, exactly
as ``NlmClient.on_call`` and ``Discoverer.on_fetch`` already do for ``-v``. ``cli.py``
is what connects one to the other, so presentation stays in one place and the library
stays testable without capturing a terminal.

Design rules, from the ``tui-design`` skill:

* **Semantic slots over 16-ANSI names, never hex** — the terminal's own theme decides
  what "green" looks like, light and dark both work, and ``NO_COLOR`` degrades to
  plain text without any special-casing here.
* **Never colour alone** — every state carries a glyph *and* a word, so the display
  survives monochrome terminals and colour-blind readers.
* **Braille ``dots`` at 80ms** for indeterminate work, a real bar only where a real
  total exists.
* **Nothing is drawn for the first 200ms**, so a warm-cache run finishes without a
  flash of chrome — but that grace is a *timer*, not a check on the next update. The
  work being reported on blocks, so there is no next update until it is over.
* **Transient** — the region is wiped when the work ends, leaving scrollback exactly
  as clean as it was before this feature.

This module imports neither ``subprocess`` nor ``urllib``; the two seams are untouched.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Self

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.progress_bar import ProgressBar
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

#: Semantic slots, mapped to 16-ANSI names. Nothing here is a hex value on purpose:
#: fixed colours clash with the user's theme and need a truecolor terminal to look as
#: intended, while ANSI names delegate the decision to the terminal.
STYLES = {
    "success": "green",
    "error": "red",
    "warning": "yellow",
    "info": "cyan",
    "muted": "dim",
    "accent": "blue",
}

#: The non-colour half of every signal. ``✓``/``✗``/``▲`` are dingbats and geometric
#: shapes rather than emoji, so they render at one cell wide everywhere.
GLYPHS = {"done": "✓", "failed": "✗", "warn": "▲", "active": "·"}

#: Used when the console cannot encode the above — that is precisely the situation in
#: which mojibake would destroy the only signal a monochrome reader has.
ASCII_GLYPHS = {"done": "+", "failed": "x", "warn": "!", "active": "."}

#: Frames per second. Well under the 15–30 a full dashboard wants: this is a handful
#: of lines, and a slower refresh is cheaper and just as smooth.
REFRESH_PER_SECOND = 12.5

#: How long the display stays hidden. Most runs are cache hits that finish inside this
#: window, and a bar that flashes on and off is worse than no bar at all.
GRACE_SECONDS = 0.2

#: Phase labels are padded to this width so the detail column lines up down the run.
LABEL_WIDTH = 11


def _elapsed(since: float) -> str:
    """``0:00:07`` — whole seconds, since sub-second precision is noise here."""
    return str(timedelta(seconds=int(time.monotonic() - since)))


@dataclass
class _Phase:
    """One stage of a run, and how it ended."""

    label: str
    detail: str = ""
    state: str = "active"  # "active" | "done" | "failed"
    started_at: float = field(default_factory=time.monotonic)


class Reporter:
    """The interface ``cli.py`` talks to — and, as written, the disabled version.

    Every method is a no-op, so the code path through ``cli.py`` is identical whether
    a display is running or not. That is the same argument the "no dry-run-only code
    path" limit rests on: a second path drifts, and then misreports.
    """

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def phase(self, label: str) -> None:
        """Open a phase. The previous one, if unresolved, is left as it stands."""

    def finish(self, detail: str = "", *, ok: bool = True) -> None:
        """Resolve the current phase, freezing it above the ones still to come."""

    def detail(self, text: str) -> None:
        """Set the sub-line under the current phase."""

    def busy(self, text: str) -> None:
        """Announce a long inner step, with its own spinner and elapsed timer."""

    def idle(self) -> None:
        """Clear whatever :meth:`busy` last announced."""

    def start_actions(self, total: int) -> None:
        """Give the current phase a determinate bar over ``total`` items."""

    def advance(self, step: int = 1) -> None:
        """Move that bar forward."""


class NullReporter(Reporter):
    """The disabled reporter, named so call sites read honestly."""


class _Screen:
    """Renders the reporter's *current* state on every frame.

    Handing ``Live`` this instead of a snapshot is what makes the display animate
    during a blocking call: Rich's own refresh thread pulls fresh state at
    ``REFRESH_PER_SECOND``, so nobody has to push updates from a thread that is
    parked inside ``subprocess.run``.
    """

    def __init__(self, reporter: LiveReporter) -> None:
        self._reporter = reporter

    def __rich_console__(self, console: Console, options: object):
        yield self._reporter._render()


class LiveReporter(Reporter):
    """A single transient region on ``console``, redrawn differentially by Rich."""

    def __init__(self, console: Console) -> None:
        self.console = console
        self._glyphs = GLYPHS if _supports_unicode(console) else ASCII_GLYPHS
        self._phases: list[_Phase] = []
        self._detail = ""
        self._busy: tuple[str, float] | None = None
        self._bar: ProgressBar | None = None
        self._done = 0
        self._total = 0
        self._spinner = Spinner("dots", style=STYLES["accent"])
        self._created_at = time.monotonic()
        self._live: Live | None = None
        # Rendering happens on Rich's refresh thread while the main thread mutates
        # the state below it, so both sides take this.
        self._lock = threading.RLock()
        self._timer: threading.Timer | None = None
        self._stopped = False

    # -- lifecycle -------------------------------------------------------

    def __exit__(self, *exc: object) -> bool:
        # Not conditional on how we got here: Rich restores the cursor on the way out,
        # which is what makes Ctrl-C, a SyncError and a typer.Exit all leave a usable
        # terminal behind.
        with self._lock:
            self._stopped = True
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            if self._live is not None:
                self._live.stop()
                self._live = None
        return False

    def _refresh(self) -> None:
        """Make sure the display is running. Nothing is pushed to it.

        The work this reports on is **blocking** — a subprocess that can sit for two
        minutes, or an HTTP fetch — so the grace window cannot be "start on the next
        update after 200ms": there is no next update until the slow thing has already
        finished, which is exactly when the display stops being useful. It is a timer
        instead, and the display then pulls its own state (see ``_Screen``).
        """
        with self._lock:
            if self._live is not None or self._stopped or self._timer is not None:
                return
            remaining = GRACE_SECONDS - (time.monotonic() - self._created_at)
            if remaining <= 0:
                self._start()
                return
            self._timer = threading.Timer(remaining, self._start)
            self._timer.daemon = True  # never hold up interpreter shutdown
            self._timer.start()

    def _start(self) -> None:
        with self._lock:
            self._timer = None
            if self._live is not None or self._stopped:
                return
            self._live = Live(
                _Screen(self),
                console=self.console,
                refresh_per_second=REFRESH_PER_SECOND,
                transient=True,
            )
            self._live.start()

    # -- the interface ---------------------------------------------------

    def phase(self, label: str) -> None:
        with self._lock:
            self._phases.append(_Phase(label=label))
            self._detail = ""
            self._busy = None
            self._bar = None
        self._refresh()

    def finish(self, detail: str = "", *, ok: bool = True) -> None:
        with self._lock:
            if self._phases:
                current = self._phases[-1]
                current.state = "done" if ok else "failed"
                current.detail = detail
            self._detail = ""
            self._busy = None
            self._bar = None
        self._refresh()

    def detail(self, text: str) -> None:
        with self._lock:
            self._detail = text
        self._refresh()

    def busy(self, text: str) -> None:
        with self._lock:
            self._busy = (text, time.monotonic())
        self._refresh()

    def idle(self) -> None:
        with self._lock:
            self._busy = None
        self._refresh()

    def start_actions(self, total: int) -> None:
        with self._lock:
            self._total = max(0, total)
            self._done = 0
            self._bar = ProgressBar(
                total=max(1, self._total),
                completed=0,
                width=self._bar_width(),
                style=STYLES["muted"],
                complete_style=STYLES["info"],
                finished_style=STYLES["success"],
            )
        self._refresh()

    def advance(self, step: int = 1) -> None:
        with self._lock:
            self._done += step
            if self._bar is not None:
                self._bar.completed = self._done
        self._refresh()

    # -- rendering -------------------------------------------------------

    def _bar_width(self) -> int:
        """Leave room for the glyph, the label and the ``12/41  0:00:07`` tail."""
        return max(12, min(28, self.console.width - 40))

    def _grid(self) -> Table:
        # `expand` plus a ratio on the last column is what makes the glyph and the
        # label immovable: without it Rich shrinks *them* to fit a long URL, and at
        # 60 columns the spinner disappears and the phase name truncates.
        grid = Table.grid(padding=(0, 1), expand=True)
        grid.add_column(width=1, no_wrap=True)            # glyph or spinner
        grid.add_column(width=LABEL_WIDTH, no_wrap=True)  # phase name
        grid.add_column(ratio=1, overflow="ellipsis", no_wrap=True)  # detail, or the bar
        return grid

    def _phase_row(self, grid: Table, phase: _Phase, *, last: bool) -> None:
        if phase.state == "done":
            mark: RenderableType = Text(self._glyphs["done"], style=STYLES["success"])
        elif phase.state == "failed":
            mark = Text(self._glyphs["failed"], style=STYLES["error"])
        elif last:
            mark = self._spinner
        else:
            mark = Text(self._glyphs["active"], style=STYLES["muted"])

        label_style = STYLES["muted"] if phase.state != "active" else ""
        grid.add_row(mark, Text(phase.label, style=label_style), self._body(phase, last=last))

    def _body(self, phase: _Phase, *, last: bool) -> RenderableType:
        """What occupies the phase's own line: the bar if there is one, otherwise the
        inner step, otherwise the phase's result."""
        if last and phase.state == "active":
            if self._bar is not None:
                return self._bar_row(phase)
            if self._busy is not None:
                text, since = self._busy
                return Text(f"{text}  {_elapsed(since)}", style=STYLES["muted"])
        return Text(phase.detail, style=STYLES["muted"])

    def _bar_row(self, phase: _Phase) -> Table:
        row = Table.grid(padding=(0, 2))
        row.add_column(width=self._bar_width(), no_wrap=True)
        row.add_column(no_wrap=True)
        counts = f"{self._done}/{self._total}  {_elapsed(phase.started_at)}"
        # The bar is only ever drawn where the total is real. Discovery gets a spinner
        # and a live count instead, because a percentage off a ceiling would be a lie.
        row.add_row(self._bar, Text(counts, style=STYLES["muted"]))
        return row

    def _render(self) -> RenderableType:
        with self._lock:
            return self._render_locked()

    def _render_locked(self) -> RenderableType:
        grid = self._grid()
        for index, phase in enumerate(self._phases):
            self._phase_row(grid, phase, last=index == len(self._phases) - 1)
            if index != len(self._phases) - 1:
                continue
            if self._detail:
                grid.add_row("", "", Text(self._detail, style=STYLES["muted"]))
            # Only when the bar has taken the phase's own line does the inner step
            # need a row of its own — otherwise `_body` already showed it inline.
            if self._busy is not None and self._bar is not None:
                grid.add_row("", "", self._busy_row())
        return Group(grid)

    def _busy_row(self) -> Table:
        """The inner spinner — this is what keeps a 120s `source wait` from reading
        as a hang."""
        text, since = self._busy or ("", time.monotonic())
        row = Table.grid(padding=(0, 1))
        row.add_row(self._spinner, Text(f"{text}  {_elapsed(since)}", style=STYLES["muted"]))
        return row


def _supports_unicode(console: Console) -> bool:
    """Can this console encode the glyph set? If not, the ASCII one is not optional."""
    encoding = (getattr(console, "encoding", "") or "").lower()
    return encoding.startswith("utf")


def make_reporter(console: Console, *, enabled: bool) -> Reporter:
    """The single place the display is switched on or off.

    ``is_terminal`` is false under ``CliRunner``, when stdout/stderr is piped, and in
    CI — which is why the existing suite needed no changes and why no escape sequence
    can reach a redirected stream.
    """
    if enabled and console.is_terminal and not console.is_dumb_terminal:
        return LiveReporter(console)
    return NullReporter()
