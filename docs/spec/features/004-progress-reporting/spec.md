# 004 · Progress reporting for the slow commands

**Status:** implemented ✅ — 2026-08-09 · 239 tests pass offline (was 212) · ruff clean ·
verified in a real pty against the fake shim and a local site

## What it does

The three commands that can run for a long time show what they are doing **while** they do it,
on stderr, without changing a single byte of what lands on stdout.

**1 · Which commands, and why they are slow.** `sync`, `status` and `expand`. All three share the
discovery step; only `sync` has the execute loop.

| Command | Where the time goes | Realistic worst case |
|---|---|---|
| `sync` | `auth check` + `source list`, then **1–2 serial subprocess calls per action**. Every ADD is followed by `source wait`, which blocks up to `SYNC_WAIT_TIMEOUT` (default **120 s**) | minutes |
| `sync` · `status` · `expand` | the shared `_expand()` step. The sitemap path is 1–2 fetches, but the **crawl fallback is up to `max_urls` (default 100) serial fetches with a mandatory 0.2 s pause between each**, per rule | ~20 s+ per rule |
| `sync --only-stale` | one extra `source stale` subprocess **per refresh candidate** | seconds × N |

`notebooks` (one upstream call, ~1–2 s) and `history` / `init` (local SQLite) stay silent — see
*Out of scope*.

**2 · A phase list that accumulates.** Each stage of the run resolves to a line that stays put,
carrying a glyph, a name and a result. The order follows the code: crawl rules resolve *before* the
notebook is contacted, so a bad manifest fails without spending an auth round-trip.

```
  ✓ discovery   1 rule → 37 URL(s)
  ✓ auth        credentials ok
  ✓ sources     41 in notebook
  ⠹ syncing     ━━━━━━━━━━╸━━━━━━━━━  12/41  0:00:07
                add   https://mundana.us/blog/post-12
                ⠋ waiting for ingestion  0:00:04
```

Phases appear only when they happen: no `discovery` without crawl rules, no `auth` or `syncing`
under `--dry-run`, no `staleness` without `--only-stale`.

**3 · A determinate bar only where a total is real.** The execute loop knows `len(plan_.actions)`
before it starts, so it gets a bar, an `N/total` count and elapsed time. Discovery does **not** know
its total until the site answers — a sitemap's length is unknown until it is parsed, and the crawl
budget is a ceiling rather than an estimate — so it gets a spinner and a live fetch counter instead
of an invented percentage:

```
  ⠹ discovery   rule 1/2 · 18 pages fetched  0:00:04
                https://mundana.us/*[except=blog]
```

**4 · The step inside a step is visible.** An ADD is `source add` followed by `source wait`, and the
wait is the part that can block for two minutes. It reports itself, so a slow ingest never looks
like a hang.

**5 · Failure changes the glyph and the words, not just the colour.**

```
  ✗ auth        session expired
```

**6 · It disappears when it is done.** The live region is transient: when the work ends it is wiped
and the results table prints into a clean scrollback, exactly as it does today.

**7 · Three ways off, and it defaults to off wherever it would be wrong.** `--no-progress`,
`SYNC_PROGRESS=0`, and automatically whenever stderr is not a TTY or `-v` is in play.

## Why

- **Silence is indistinguishable from a hang.** Today nothing is printed between the command
  starting and the results table, and the two slowest paths — `source wait` at up to 120 s per added
  source, and the crawl fallback at 0.2 s × up to 100 fetches per rule — are precisely the ones with
  no output at all.
- **`-v` is a debugging tool, not a progress indicator.** It exists and it does print, but it prints
  raw argv and `GET` lines. It answers "what call is running", never "how much is left".
- **The information is already there and thrown away.** `len(plan_.actions)` is known before
  `execute()` starts; `Discoverer` already announces every fetch through `on_fetch`. Nothing has to
  be measured or estimated — only reported.
- **The work is serial by design and will stay that way.** `tech-stack.md` rules out concurrency for
  discovery on purpose. If a run cannot be made shorter, it can at least be made legible.

## Acceptance criteria

**Display**

- [x] Each phase renders a glyph, a name and — once resolved — a one-line result, and stays visible
      while the later phases run.
- [x] The execute loop renders a determinate bar with `N/total` and elapsed time, driven by
      `len(plan_.actions)`, and advances as each action completes.
- [x] Discovery renders a spinner, the rule's position (`rule 1/2`), a live fetch count and the rule
      string. It never renders a percentage or a bar.
- [x] A `source wait` in progress is reported as a distinct sub-line, so a 120 s ingest is visibly
      running rather than hung.
- [x] `sync --only-stale` reports its `source stale` probes.
- [x] A long URL is truncated with `…` at the terminal width; the display never wraps or reflows.
- [x] The live region is transient — after the command's work it leaves no trace, and the results
      table is the only lasting output.

**Scope**

- [x] `sync`, `status` and `expand` report progress. `notebooks`, `history` and `init` are unchanged.
- [x] Everything renders on **stderr**, alongside where `-v` already writes.
- [x] `stdout` is byte-identical to today for every command, in every mode.

**Opt-out**

- [x] `--no-progress` on `sync`, `status` and `expand`.
- [x] `SYNC_PROGRESS` in `.env` (default on), following the usual `os.environ` > `.env` > default
      precedence.
- [x] `-v/--verbose` disables the display; the argv and `GET` lines still print exactly as today.
- [x] The display disables itself when stderr is not a TTY, and on a dumb terminal.

**Design system** (per `.claude/skills/tui-design/`)

- [x] Colours are semantic slot names mapped to **16-ANSI** names, never hex — so the terminal's own
      theme applies, light and dark both work, and `NO_COLOR` degrades to plain text.
- [x] No state is signalled by colour alone: every one carries a glyph and a word, and the display
      is fully readable with colour stripped.
- [x] The spinner is the braille `dots` set at 80 ms.
- [x] Nothing is drawn for the first 200 ms, so a warm-cache run finishes without a flash — and the
      display *does* appear 200 ms in even when the run is sitting inside a blocking subprocess
      call, which is the case that matters and the one the first implementation got wrong.
- [x] Only box-drawing and block characters, no emoji, with an ASCII glyph fallback when the console
      encoding is not UTF-8.
- [x] Refresh is capped (12.5 fps) over a single differentially-redrawn region — no `clear()`,
      no flicker.
- [x] Ctrl-C, a `SyncError` and a `typer.Exit` all leave the terminal and cursor in a sane state.
      Covered by `test_the_terminal_is_restored_however_the_block_exits`, which asserts the
      show-cursor escape is emitted on the way out of both a `KeyboardInterrupt` and a plain
      exception. A real interactive Ctrl-C has not been pressed by hand.
- [x] It renders correctly at 60, 80 and 120 columns — the glyph and the phase name hold their
      positions and only the URL truncates.
- [ ] **Not verified: inside tmux.** Nothing in the implementation is multiplexer-specific (one
      `Live` region, no mouse capture, no alternate screen), but it has not been run there.

**Tests and lint**

- [x] The suite still runs offline with no network and no Google auth, and the existing 212 tests
      pass unmodified — under `CliRunner` stderr is not a TTY, so the reporter is inert.
- [x] An explicit regression test proves no bar character and no ANSI escape reaches `result.output`
      for `sync`, `status` or `expand`.
- [x] `uv run ruff check .` is clean, and **no new runtime dependency** is added to
      `pyproject.toml` — `rich>=13` is already there.
- [x] The two seam greps still hold: `subprocess` only in `nlm.py`, HTTP only in `discovery.py`. The
      new module appears in neither.

## Out of scope

- **`notebooks`, `history` and `init`.** `notebooks` makes a single upstream call (~1–2 s, under the
  200 ms-to-2 s band where a spinner earns its place only marginally); the other two are local
  SQLite and finish instantly. Adding chrome to them would be decoration.
- **A fullscreen TUI.** No alternate screen, no keybindings, no focus model, no panels. This is a
  transient region above the ordinary output of a batch command, and the layout half of
  `tui-design` does not apply.
- **Making anything faster.** No concurrency, no connection pooling, no batching. `tech-stack.md`
  rules those out for discovery deliberately, and `source wait` is upstream's clock, not ours. This
  feature reports on the serial work; it does not change it.
- **Cancelling mid-run.** Ctrl-C must leave a sane terminal, but there is no graceful "stop after
  the current action" handling. Sync never deletes, so an interrupted run is reconciled by the next
  one.
- **Progress inside a single `source wait`.** Upstream owns that poll loop and reports nothing until
  it exits. We can show that it is running and for how long, not how far along it is.
- **Machine-readable progress.** No `--json` progress stream, no `--progress=plain` CI mode. The
  display self-disables off a TTY, which is what CI needs.
