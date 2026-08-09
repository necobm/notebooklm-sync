# 004 · Progress reporting for the slow commands — Plan

**Status:** implemented ✅ · **Last updated:** 2026-08-09

How the display in [`spec.md`](spec.md) gets built. A living document: update it when the work
lands.

---

## Approach

This feature adds **presentation**, and presentation is the thing most likely to leak into logic and
stay there. So the shape is decided before the details: **one module renders, and nothing else in
the package knows that a display exists.**

The codebase already has the pattern. `NlmClient.on_call` and `Discoverer.on_fetch` are callbacks
that announce an event and print nothing — which is exactly how `-v` works without `nlm.py` or
`discovery.py` ever importing Rich. This feature is the same idea applied twice more: the library
notifies, `cli.py` renders, and a new `progress.py` is the only place that knows what a spinner is.

That also settles the testing question. `progress.py` imports Rich and `models`, and neither
`subprocess` nor `urllib` — so both hard limits in
[`tech-stack.md`](../../constitution/tech-stack.md) are untouched and the module is unit-testable
against a `StringIO` console.

The second decision is **how it turns itself off**. Not with `if reporter:` scattered through
`cli.py`, but with a `NullReporter` whose every method is a no-op. There is one decision point,
`make_reporter()`, and one code path afterwards. A disabled run executes the same statements a live
one does — which is the same argument the "no dry-run-only code path" limit rests on.

Built in dependency order, each green before the next starts:

1. **`progress.py`** — the display, testable on its own with no CLI involved.
2. **The callbacks** — three optional parameters on existing seams, each defaulting to `None`.
3. **`config.py` and `cli.py`** — the setting, the flag, and where the `with` block starts and ends.
4. **Tests** — `test_progress.py`, plus the stdout-purity guard in `test_cli.py`.
5. **Docs** — the constitution, `AGENTS.md`, `.env.example`.

`progress.py` comes first because every visual argument — glyph sets, the 200 ms grace, truncation
at 80 columns — is settled far more cheaply against a `StringIO` than through a `CliRunner`.

---

## Implementation

### 1 · `progress.py` — the only module that renders progress

Semantic slots mapped to **16-ANSI names**, never hex. The terminal's theme decides what "green"
looks like, light and dark both work, and `NO_COLOR` is handled by Rich for free:

```python
STYLES = {
    "success": "green", "error": "red",  "warning": "yellow",
    "info":    "cyan",  "muted": "dim",  "accent":  "blue",
}
GLYPHS       = {"done": "✓", "failed": "✗", "warn": "▲", "active": "·"}
ASCII_GLYPHS = {"done": "+", "failed": "x", "warn": "!", "active": "."}
```

Two glyph sets because a non-UTF-8 console would otherwise render mojibake where the *only*
non-colour signal lives. The choice is made once, off `console.encoding`.

- `Reporter` — the interface `cli.py` talks to:

  ```python
  phase(label)                      # open a phase; spinner + label
  finish(detail, *, ok=True)        # freeze it to "✓ label  detail"
  detail(text)                      # dim sub-line under the current phase
  busy(text)                        # a long inner step, with its own spinner + timer
  idle()                            # clear it
  start_actions(total)              # switch the current phase to a determinate bar
  advance(step=1)                   # move that bar forward
  __enter__ / __exit__
  ```

  **This is deliberately domain-free.** An earlier draft had `step(action, step)` and
  `advance(action)` taking a `PlannedAction`; that would have made the renderer import `models` and
  learn the vocabulary of the sync engine. `busy`/`idle` say the same thing in display terms, and
  the mapping from engine callbacks onto them lives in `cli._execute_callbacks()` — which is the
  layer that is allowed to know about both.

- `NullReporter` — every method a no-op, `__exit__` returns `False`. This is what keeps `cli.py`
  branch-free.

- `LiveReporter` — a `rich.live.Live` on the stderr console, `refresh_per_second=12.5`,
  `transient=True`, over a `Group` of:
  1. resolved phase lines, as static `Text` (glyph + label + detail);
  2. the current phase — a `Spinner("dots")` plus label, or an embedded `rich.progress.Progress`
     once `start_actions()` has been called;
  3. a dim detail line, built with `overflow="ellipsis", no_wrap=True` so a long URL truncates at
     the terminal width instead of wrapping.

  **Built with `rich.progress_bar.ProgressBar`, not `rich.progress.Progress`.** `Progress` fixes
  its columns for every task it holds, so spinner-only phases and a bar-carrying phase could not
  coexist in one. `ProgressBar` is a bare renderable that drops into a grid cell, leaving the
  surrounding `Live` to drive the refresh and this module to own the layout.

  The layout is a `Table.grid` with `expand=True` and a `ratio=1` on the last column. Without the
  ratio Rich shrinks the *glyph and label* columns to fit a long URL: at 60 columns the spinner
  disappears and the phase name truncates to `sync…`. Pinned by
  `test_the_glyph_and_label_survive_a_narrow_terminal`.

  The **200 ms grace** is a `threading.Timer`, and that detail is load-bearing. The first
  implementation deferred `Live.start()` "until the first update that lands at least 200 ms after
  construction" — which is wrong, because the work being reported on *blocks*: nothing calls back
  from inside `subprocess.run`, so the display could not appear until the slow step had already
  finished. Measured on a 4.2 s run, the first thing drawn was `✓ auth  credentials ok` after two
  seconds of blank screen. A timer starts it 200 ms in regardless, and a warm-cache run still
  cancels it on the way out and draws nothing.

  For the same reason `Live` is handed a `_Screen` object rather than a rendered snapshot: Rich's
  own refresh thread pulls current state each frame, so nothing has to be pushed from a thread that
  is parked in a subprocess. State is guarded by an `RLock`, since the refresh thread renders while
  the main thread mutates.

- `make_reporter(console, *, enabled) -> Reporter` — the single decision point:

  ```python
  enabled and console.is_terminal and not console.is_dumb_terminal
  ```

  Under `CliRunner`, when piped, and in CI, `is_terminal` is `False` → `NullReporter`. That is the
  whole reason the existing 212 tests need no changes.

### 2 · Three optional callbacks on existing seams

All follow the `on_call` / `on_fetch` convention: **the library notifies, `cli.py` renders.** Each
defaults to `None`, so no existing caller changes and no module gains an import.

| File | Change |
|---|---|
| `engine.py` | `execute(..., on_step=None, on_action=None)`. `on_step(action, step)` fires **before** each step, with `step` in `{"add", "wait", "refresh"}` |
| `engine.py` | `apply_stale_filter(..., on_probe=None)` — one call per `source stale` probe |
| `discovery.py` | `expand_entries(..., on_rule=None)` — fires per rule, with the rule and its 1-based index |

`on_step` is the load-bearing one. `execute()`'s existing `on_action` fires *after* the work, so a
bar driven by it alone would read `0/41` for the entire duration of the first action and could never
surface "waiting for ingestion" — which is the single longest thing the tool ever does. The
alternative, sniffing `NlmClient.on_call` argv for `["source", "wait", …]`, was rejected: it puts
display logic inside the subprocess seam and breaks silently the moment upstream renames a
subcommand.

The per-fetch counter needs nothing new — `Discoverer.on_fetch` already exists and already fires
once per request.

### 3 · `config.py` and `cli.py`

`config.py` gains `progress: bool` on `Settings`, read from `SYNC_PROGRESS` (default on) through the
same `os.environ` > `.env` > default precedence as every other key.

`cli.py`:

- `--no-progress` on `sync`, `status` and `expand`, resolved once:

  ```python
  enabled = settings.progress and not no_progress and not verbose
  ```

  **`-v` wins and turns the display off.** A scrolling `$ notebooklm …` / `GET …` stream and a
  `Live` region compete for the same stderr, and `-v` is the debugging tool — routing its lines
  through the live region would make it harder to read, not easier. The flag help says so.
- `_expand()` takes a `reporter` and attaches `on_rule` / `on_fetch` through a small
  `_DiscoveryProgress` bridge. `_client()` needs no change: the `auth`, `sources` and `syncing`
  phases are opened by the command bodies, so `NlmClient` is untouched by this feature.
- `ENV_TEMPLATE` and `.env.example` gain `SYNC_PROGRESS`, commented.
- **Placement.** `with reporter:` opens before `_expand()` and **closes before** `_render_plan()` and
  the `expand` tables. Never print a table into a live region, and the `raise typer.Exit(...)` at
  the end of `sync` must land outside the block. `Live.__exit__` restores the cursor on every exit
  path — normal, `KeyboardInterrupt`, `SyncError`, `typer.Exit` — so wrapping the slow work in the
  context manager is also what satisfies the Ctrl-C criterion.

### 4 · Tests (all offline)

`tests/test_progress.py` — drive `LiveReporter` against
`Console(file=StringIO(), force_terminal=True, width=80)`:

- a resolved phase renders glyph **and** label **and** detail; the failed one differs in glyph and
  wording, not only in colour;
- the ASCII glyph set is selected on a non-UTF-8 console;
- a URL longer than the width is truncated with `…`, never wrapped;
- `start_actions(n)` then `advance()` × n reaches the total;
- `make_reporter()` returns a `NullReporter` when `is_terminal` is `False`, and its output is
  exactly empty.

`tests/test_cli.py`, in the existing substring idiom:

- **the regression that matters** — under `CliRunner` (never a TTY), no bar character and no ANSI
  escape appears in `result.output` for `sync`, `status` or `expand`;
- `--no-progress` and `SYNC_PROGRESS=0` are accepted and the exit-code contract is unchanged;
- `-v` still prints `$ notebooklm`, proving the reporter did not swallow it.

### 5 · Docs

`constitution/tech-stack.md`: `progress.py` under *Key files*, `SYNC_PROGRESS` in the config list,
and the `## Visual style` section — currently the stub *"Not applicable"* — replaced with the design
rules this feature commits to. `AGENTS.md`: a line in the data-flow block, and the `tui-design`
skill entry updated from "nothing in this repo is a TUI today".

---

## Decisions

- **One module renders; the rest of the package only notifies.** The alternative — printing from
  `engine.py` and `discovery.py` where the events happen — would put Rich imports and terminal
  assumptions inside two pure-ish modules and make them untestable without capturing stdout. The
  repo already chose the callback pattern twice (`on_call`, `on_fetch`); this follows it rather than
  inventing a third convention.
- **`NullReporter` instead of `if reporter is not None:`.** A null object keeps one code path
  through `cli.py`, so the disabled path cannot drift from the enabled one. Same reasoning as the
  no-dry-run-only-path limit, applied to rendering.
- **Semantic slots over 16-ANSI names, never hex.** Fixed hex is the single most-reported TUI
  failure (`tui-design` anti-pattern #1): it clashes with the user's theme, breaks on light
  backgrounds, and needs a truecolor terminal to look as intended. ANSI names delegate to the
  terminal, degrade to 16 colours everywhere, and let `NO_COLOR` work without special-casing.
- **Never colour alone.** Every state carries a glyph and a word. The display has to survive
  `NO_COLOR`, monochrome terminals and colour-blind readers, and "it turned red" is not information
  any of them receive.
- **A bar only where the total is real.** Discovery's total is unknowable until the site answers —
  a sitemap's length is not known before parsing it, and the crawl cap is a ceiling, not an
  estimate. A percentage derived from a ceiling would be a confident lie; a spinner with a live
  fetch count is honest and just as reassuring.
- **200 ms before anything is drawn.** Straight from `tui-design` §5. Most runs are cache hits that
  finish in well under a second, and a bar that flashes on and off is worse than no bar.
- **`transient=True`.** The phase results are either reprinted afterwards by `_render_expansions()`
  or are noise once the run is over (`41 in notebook`). Wiping the region keeps scrollback exactly
  as clean as it is today — the results table remains the only lasting output.
- **stderr, and off when not a TTY.** `-v` already writes to stderr *"so piping stdout stays
  clean"*. Progress inherits that rule, plus a hard `is_terminal` gate, so
  `notebooklm-sync sync --dry-run | cat` is byte-identical to today and CI logs gain nothing.
- **`-v` disables the display rather than composing with it.** Rich can print above a live region,
  but the result is a bar jittering under a fast argv scroll. `-v` exists to be read closely; the
  display exists to be glanced at. They serve different moments.
- **No new dependency.** `rich>=13` is already in `pyproject.toml`. Feature 003 shipped an entire
  HTTP layer with zero additions; this adds none either.

---

## Risks

- **A silent stdout regression** — an escape sequence or a stray bar leaking into piped output would
  break every downstream consumer, quietly. *Mitigation:* rendering is on a stderr console that
  self-disables off a TTY, and `test_cli.py` asserts explicitly that no bar character reaches
  `result.output` for all three commands.
- **A `typer.Exit` raised inside the live region** would leave the terminal mid-frame.
  *Mitigation:* the `with` block closes before every `raise`; and `Live.__exit__` restores state on
  any exception path regardless, so the criterion holds even if the placement rule is later broken.
- **Presentation logic drifting into `engine.py`.** The callbacks are a door, and the next feature
  may be tempted to pass a formatted string through it. *Mitigation:* the callbacks carry a
  `PlannedAction` and a bare step name — no strings to format, nothing to render — and the
  maintenance checklist re-greps for Rich imports outside `cli.py` / `progress.py`.
- **The display and `-v` fighting over stderr.** *Mitigation:* `-v` disables it outright.
- **Terminal-only behaviour that no test can reach.** `CliRunner` is never a TTY, so the live path
  is structurally invisible to the CLI suite. *Mitigation:* `test_progress.py` drives `LiveReporter`
  directly with `force_terminal=True`; the rest — flicker, resize, Ctrl-C, tmux — is on the manual
  checklist below and cannot be claimed without running it.
- **Unicode glyphs on a non-UTF-8 console.** *Mitigation:* the ASCII fallback set, chosen off
  `console.encoding`.

---

## Verification

**Offline (required, gates the work)**

All done 2026-08-09.

- [x] `uv run pytest` — **239 passed** (212 before), no network, no auth. 19 new in
      `test_progress.py`, 8 new in `test_cli.py`; **no existing test was modified**, which is the
      `is_terminal` gate proving itself.
- [x] `uv run pytest` under Python **3.11** as well as 3.12 — 239 passed on both.
- [x] `uv run ruff check .` — clean. `ruff format` deliberately not run.
- [x] `grep -rnE "^(import|from) subprocess" src/` still matches only `nlm.py`.
- [x] `grep -rnE "urllib\.request|http\.client|import socket" src/` still matches only
      `discovery.py`.
- [x] `grep -rn "rich" src/` matches only `cli.py` and `progress.py` — the new seam holds.
- [x] `progress.py` imports neither `subprocess` nor `urllib`.
- [x] `test_cli.py::assert_no_display` proves no cursor-hide, no erase-line and no phase text
      reaches the output of `sync`, `status` or `expand` under `CliRunner`.

**Manual, in a real terminal** — the part `CliRunner` structurally cannot cover. Run under
`script -qec` (a real pty) against the repo's own fake shim plus a local `http.server`, so none of
it needed auth or the internet.

- [x] `sync` with five sources: the phase list accumulates, the bar reaches `4/5  0:00:03`, the
      detail line tracks the current URL, and `waiting for ingestion` appears under it.
- [x] **Post-fix re-run:** with a shim that blocks 2 s per call, the first frame drawn is now
      `⠋ sources` *while the call is still running*, where before the fix it was
      `✓ auth  credentials ok` after two seconds of blank screen.
- [x] `expand --refresh-discovery` against a deliberately slow local site: the braille spinner
      cycles and the counter climbs — `discovery  rule 1/1 · 2 pages fetched  0:00:00` — then
      resolves to `✓ discovery  1 rule → 3 URL(s)`.
- [x] The same command against a *fast* local site draws nothing at all: the run completes inside
      the 200 ms grace. The grace window working exactly as intended.
- [x] `sync --dry-run > file` under a pty: **zero escape sequences on stdout**.
- [x] `-v`, `--no-progress` and `SYNC_PROGRESS=0` each produce **0** erase-line sequences under a
      pty, against **62** for the default run. All three switches verified independently.
- [x] `--no-progress` leaves the exit-code contract alone — expired auth still exits 3.
- [x] A rule's `[except=blog]` renders intact in the display, since `Text` is not markup.
- [x] Renders correctly at 60, 80 and 120 columns.
- [ ] **Not run: inside tmux**, and no interactive Ctrl-C was pressed by hand. The cursor-restore
      path is covered by a unit test on both `KeyboardInterrupt` and a plain exception, and nothing
      in the implementation is multiplexer-specific — but neither was exercised for real.

**Live (manual, gated on auth)** — not run. The `source wait` sub-line was exercised against the
fake shim (which does block, so the timer and spinner are real), but not against a genuine
NotebookLM ingest. That needs `notebooklm auth check --test` to pass and was not attempted here.

**CI** — unchanged. No new dependency, no new secret, no new job. The reporter is inert without a
TTY, so the existing `ruff` + `pytest` matrix covers this feature by construction.

---

Progress is tracked in [`tasks.md`](tasks.md). When this lands, move 004 to "Done" in
[`../../constitution/roadmap.md`](../../constitution/roadmap.md).
