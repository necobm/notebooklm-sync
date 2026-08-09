# 004 · Progress reporting for the slow commands — Tasks

Derived from [`plan.md`](plan.md). All five groups shipped on 2026-08-09; each was green
(`uv run pytest` + `uv run ruff check .`) before the next one started. 239 tests pass offline,
up from 212, and no existing test was modified.

## 1 · `progress.py` — the only module that renders progress

- [x] `STYLES` — semantic slots (`success`, `error`, `warning`, `info`, `muted`, `accent`) mapped to
      **16-ANSI names**, never hex.
- [x] `GLYPHS` / `ASCII_GLYPHS` and the encoding check that picks between them.
- [x] `Reporter` interface: `phase`, `finish`, `detail`, `busy`, `idle`, `start_actions`,
      `advance`, `__enter__`, `__exit__`. Shipped domain-free — the planned `step(action, step)` /
      `advance(action)` would have made the renderer import `models`; `cli._execute_callbacks()`
      does that mapping instead.
- [x] `NullReporter` — every method a no-op; `__exit__` returns `False`.
- [x] `LiveReporter` — a `rich.live.Live` on the stderr console, `refresh_per_second=12.5`,
      `transient=True`, over a `Group` of resolved phase lines, the current phase, and a dim detail
      line with `overflow="ellipsis", no_wrap=True`.
- [x] Build the bar from `rich.progress_bar.ProgressBar`, not `rich.progress.Progress`: `Progress`
      fixes its columns for every task, so spinner-only and bar-carrying phases could not coexist
      in one. The bare renderable drops into a grid cell and the surrounding `Live` refreshes it.
- [x] `Table.grid(expand=True)` with `ratio=1` on the last column — without it Rich shrinks the
      glyph and label columns to fit a long URL, and at 60 columns the spinner vanishes.
- [x] `Spinner("dots")` — the braille set at 80 ms.
- [x] The 200 ms grace as a `threading.Timer`, **not** a check on the next update — the reported
      work blocks, so there is no next update until it is already over. Found by running it, not by
      the tests; guarded now by `test_the_display_appears_during_a_blocking_call`.
- [x] Hand `Live` a `_Screen` that renders current state per frame, not a snapshot, so nothing has
      to be pushed from a thread parked in `subprocess.run`. `RLock` around state, since the
      refresh thread renders while the main thread mutates.
- [x] `make_reporter(console, *, enabled)` — the single decision point:
      `enabled and console.is_terminal and not console.is_dumb_terminal`.
- [x] Confirm the module imports neither `subprocess` nor `urllib`.

## 2 · The three callbacks

- [x] `engine.execute(..., on_step=None, on_action=None)`; `on_step(action, step)` fires **before**
      each step with `step` in `{"add", "wait", "refresh"}`. Existing `on_action` behaviour
      unchanged.
- [x] `engine.apply_stale_filter(..., on_probe=None)` — one call per `source stale` probe.
- [x] `discovery.expand_entries(..., on_rule=None)` — fires per rule, with the rule and its 1-based
      index.
- [x] Confirm every new parameter defaults to `None`, that no existing caller changes, and that
      neither module gained an import.

## 3 · `config.py` and `cli.py`

- [x] `config.py`: `progress: bool` on `Settings` from `SYNC_PROGRESS` (default on), same
      `os.environ` > `.env` > default precedence as every other key.
- [x] `cli.py`: `--no-progress` on `sync`, `status` and `expand`; help text states that `-v`
      disables the display.
- [x] Resolve once: `enabled = settings.progress and not no_progress and not verbose`.
- [x] `_expand()` takes a `reporter` and attaches `on_rule` / `on_fetch` via `_DiscoveryProgress`.
      `_client()` needed no change after all — the `auth`, `sources` and `syncing` phases are opened
      by the command bodies themselves, so `NlmClient` stays untouched by this feature.
- [x] `with reporter:` opens before `_expand()` and **closes before** `_render_plan()` and the
      `expand` tables — no table inside a live region, and every `raise typer.Exit(...)` outside it.
- [x] `ENV_TEMPLATE` and `.env.example`: `SYNC_PROGRESS`, commented.

## 4 · Tests (all offline)

- [x] `tests/test_progress.py`: `LiveReporter` against
      `Console(file=StringIO(), force_terminal=True, width=80)`.
- [x] A resolved phase renders glyph **and** label **and** detail; the failed one differs in glyph
      and wording, not only in colour.
- [x] The ASCII glyph set is selected on a non-UTF-8 console.
- [x] A URL longer than the width is truncated with `…`, never wrapped.
- [x] `start_actions(n)` then `advance()` × n reaches the total.
- [x] `make_reporter()` returns `NullReporter` when `is_terminal` is `False`, and its output is
      exactly empty.
- [x] `test_cli.py`: **no cursor-hide, no erase-line and no phase text** in `result.output` for
      `sync`, `status` or `expand` — the regression that matters. Note the results *table* legitimately
      contains `━`, so the bar character is not a usable signal and is deliberately not asserted on.
- [x] `test_cli.py`: `--no-progress` and `SYNC_PROGRESS=0` accepted, exit-code contract unchanged.
- [x] `test_cli.py`: `-v` still prints `$ notebooklm`.
- [x] Confirm the suite passes with no `.env`, no network and no Google auth, and that the existing
      212 tests were not modified.

## 5 · Docs and closing out

- [x] `constitution/tech-stack.md`: `progress.py` under *Key files*, `SYNC_PROGRESS` in the config
      list, and the `## Visual style` stub (*"Not applicable"*) replaced with the design rules this
      feature commits to.
- [x] `AGENTS.md`: a line in the data-flow block, and the `tui-design` skill entry updated from
      "nothing in this repo is a TUI today".
- [x] `README.md`: `--no-progress` and `SYNC_PROGRESS`.
- [x] Run the manual terminal checklist in [`plan.md`](plan.md) — it is the only place the live path
      is exercised at all. Done under `script -qec` against the fake shim and a local `http.server`;
      tmux and an interactive Ctrl-C remain unrun and are marked as such there.
- [x] Tick the acceptance criteria in [`spec.md`](spec.md) and set its status.
- [x] Move 004 to **Done** in [`../../constitution/roadmap.md`](../../constitution/roadmap.md).
- [x] `engram save` the notify-don't-render callback rule, why `on_step` had to fire *before* the
      work, and the semantic-slot / no-colour-alone decisions — always `--project notebooklm-sync`.

## Maintenance (recurring checklist)

Repeat these whenever this feature is touched again:

- [ ] `uv run pytest` and `uv run ruff check .` after any change under `src/`.
- [ ] Re-grep the two seams: `subprocess` only in `nlm.py`, `urllib.request`/`http.client`/`socket`
      only in `discovery.py`.
- [ ] Re-grep the third: `rich` only in `cli.py` and `progress.py`. Presentation leaking back into
      the library is the failure mode this feature is one callback away from.
- [ ] Re-check that stdout is unchanged — pipe every command and diff, since no test can see the
      live path.
- [ ] If a new slow command appears, decide whether it joins the three; if a fast one becomes slow,
      it probably should.
- [ ] Update this plan's status and the roadmap when work lands.
