# 005 · Command and action logging

**Status:** implemented ✅ — 2026-08-09 · 271 tests pass offline (was 239) · ruff clean ·
verified by running the CLI against a stub binary; not yet run against live NotebookLM

## What it does

Every invocation of `notebooklm-sync` writes a durable, plain-text record of what it ran and what
happened, to a file under `var/log/`. Nothing about stdout, stderr, the results tables or the
progress display changes.

**1 · One file per command, per day.** The filename carries the date and the command, and every
execution of that command on that date appends to it:

```
var/log/
├── 2026-08-09-sync.log        ← every sync run today
├── 2026-08-09-expand.log
├── 2026-08-09-status.log
└── 2026-08-08-sync.log        ← yesterday's, until retention removes it
```

Not size-rotated: a day's worth of one command is the unit you actually go looking for when
something went wrong this morning. `var/` is gitignored, and files older than
`SYNC_LOG_RETENTION_DAYS` (default 30) are removed on the next run.

**2 · Every command logs, including the fast ones.** The three slow commands are the ones you will
read, but a log with holes in it is a log you cannot trust.

| Command | What lands in the file |
|---|---|
| `sync` | the full run: every `notebooklm` argv with its exit code and duration, the plan summary, every executed action with its outcome, the `sync_runs.id` to join back to SQLite, the final counts and exit code |
| `status` · `expand` | discovery: which rules resolved, from sitemap or crawl, to how many URLs; the upstream `source list` call for `status` |
| `notebooks` | the one upstream call, its exit code and duration |
| `history` · `init` | start, end, exit code — enough to prove they ran |

**3 · What a record looks like.** Timestamp, level, an 8-character run token, the emitting module,
then the message. The token is what makes a shared daily file readable: two runs interleaved in one
file stay attributable to their own invocation.

```
2026-08-09 12:00:01,412 INFO  8f3a2c1d cli       start: sync notebook=research only_stale
2026-08-09 12:00:01,502 INFO  8f3a2c1d discover  https://mundana.us/*[except=blog] -> 37 url(s) from sitemap
2026-08-09 12:00:01,503 INFO  8f3a2c1d nlm       $ notebooklm auth check --test --json
2026-08-09 12:00:02,315 INFO  8f3a2c1d nlm       rc=0 in 812ms
2026-08-09 12:00:02,316 INFO  8f3a2c1d nlm       $ notebooklm source list -n 3fb8dfb7 --json
2026-08-09 12:00:02,904 INFO  8f3a2c1d nlm       rc=0 in 588ms
2026-08-09 12:00:02,906 INFO  8f3a2c1d cli       plan: run=17 policy=override add=1 refresh=0 skip=36 orphan=4
2026-08-09 12:00:03,001 INFO  8f3a2c1d nlm       $ notebooklm source add https://mundana.us/x -n 3fb8dfb7 --json
2026-08-09 12:00:04,120 INFO  8f3a2c1d nlm       rc=0 in 1119ms
2026-08-09 12:00:04,121 INFO  8f3a2c1d nlm       $ notebooklm source wait src_9 -n 3fb8dfb7 --timeout 120 --json
2026-08-09 12:00:19,879 INFO  8f3a2c1d nlm       rc=2 in 15758ms
2026-08-09 12:00:19,880 INFO  8f3a2c1d nlm       wait src_9: timeout (exit 2)
2026-08-09 12:00:19,882 INFO  8f3a2c1d engine    add https://mundana.us/x -> pending (still processing after 120s)
2026-08-09 12:00:19,928 INFO  8f3a2c1d cli       results: added=0 refreshed=0 skipped=36 pending=1 failed=0 orphans=4
2026-08-09 12:00:19,930 INFO  8f3a2c1d cli       done: exit=0
```

The start line carries the command's **resolved parameters**, not raw argv — they include a value
that came from `.env` and never appeared on the command line, and `sys.argv` is not the invocation
at all when the app does not own the process.

**4 · The level split.** `SYNC_LOG_LEVEL` (default `INFO`) decides how much lands:

- **INFO** — the command's start and end, every `notebooklm` argv with its exit code and duration,
  every executed action with its outcome, discovery's per-rule result, and every error.
- **DEBUG** — additionally each individual HTTP fetch with its status and byte count, every
  discovery cache hit and miss, and the SQLite schema check.

**5 · Failures are recorded, with their exit code.** The `except SyncError` boundary that already
exists in every command logs before it re-raises, so the file explains why the process exited
non-zero:

```
2026-08-09 12:05:44,010 ERROR 2b7c91ff cli        failed: unknown notebook 'reserch' (exit=2)
```

**6 · Nothing secret is ever written.** Not cookies, not `storage_state.json`, not `.env` values,
and not a single `notebooklm` stdout payload — including `auth check --test`'s, which describes the
Google account. On an upstream failure only the error `code` and `message` are recorded.

**7 · Two ways off, and it is off for the whole test suite.** `SYNC_LOG=off` disables it entirely,
and `SYNC_LOG_DIR` moves it elsewhere. The test suite sets the former, so `uv run pytest` writes no
log file anywhere.

## Why

- **The evidence is gone by the time you want it.** stderr scrolls, and the progress display is
  transient by design. When a sync misbehaves the only recovery today is to run it again with `-v`
  and hope it reproduces.
- **SQLite records the decision, never the execution.** `sync_events` holds url → action → outcome,
  which is what the reconciler *decided*. It has never held the upstream argv, an exit code, a
  duration, or an HTTP request — so "the run took four minutes and I don't know where it went" is
  currently unanswerable.
- **Only `sync` leaves any trace at all.** `status`, `expand`, `notebooks`, `history` and `init`
  touch neither `sync_runs` nor `sync_events`. A failed `expand` is unreconstructable.
- **`-v` requires having predicted the failure.** It prints the right things, to the wrong place, at
  the wrong time — it only helps on the run *after* the one that broke.
- **The config key already exists and does nothing.** `Settings.log_level` and `SYNC_LOG_LEVEL` have
  been in `config.py` since 001, and nothing in `src/` imports `logging`. This closes that gap.

## Acceptance criteria

**Files**

- [x] A command writes to `var/log/<YYYY-MM-DD>-<command>.log`, using the date the run starts.
- [x] Two runs of the same command on the same date append to one file, in order, each carrying its
      own run token.
- [x] Two different commands on the same date write to two different files.
- [x] The directory is created on demand; the repo carries no `var/` and `.gitignore` excludes it.
- [x] Files older than `SYNC_LOG_RETENTION_DAYS` (default 30) are unlinked on the next run; `0`
      keeps everything. A failure to prune never fails the command.
- [x] `SYNC_LOG=off` writes nothing and creates no directory. `SYNC_LOG_DIR` relocates the output.
      Both follow the usual `os.environ` > `.env` > default precedence.

**Content**

- [x] Every `notebooklm` invocation appears with its full argv, then its exit code and wall-clock
      duration in milliseconds.
- [x] `source wait` records which of the three outcomes it hit, so the exit-2-is-a-timeout
      distinction is visible in the file rather than inferred.
- [x] Every action executed by `engine.execute()` appears with its action, url, outcome and message.
- [x] `sync` records the plan summary and the `sync_runs.id`, so a log line joins to a SQLite row.
- [x] Each command records its start (with its resolved parameters) and its end (with its exit
      code). A command that exits via `_fail()` ends on its `failed: … (exit=N)` line instead —
      `_fail` raises outside the session, which is the one place a `done:` line is absent by design.
- [x] A `SyncError` is recorded with its message and exit code at the command boundary.
- [x] At `SYNC_LOG_LEVEL=DEBUG`, individual HTTP fetches and discovery cache hits/misses appear; at
      `INFO` they do not.

**Seam**

- [x] `logs.py` is the only module in `src/` that attaches a handler or opens a log file:
      `grep -rnE "basicConfig|addHandler|FileHandler" src/` matches it and nothing else.
- [x] Every other module only calls `logging.getLogger(__name__)`; with no handler attached the
      records go nowhere, so importing the package as a library still writes nothing.
- [x] The handler is detached, flushed and closed when the command ends, so repeated in-process
      invocations (`CliRunner`) leave nothing behind.
- [x] `engine.plan()` remains pure — it emits no records. The plan summary is logged by `cli.py`
      after `plan()` returns.
- [x] `logs.py` imports neither `rich`, `subprocess` nor `urllib`; the three existing seam greps
      still hold.
- [x] No ANSI escape and no Rich markup reaches a log file.

**Secrets**

- [x] No `notebooklm` stdout payload is written at any level, including `auth check --test`'s.
- [x] No cookie, `storage_state` content or `.env` value is written at any level.

**Tests and lint**

- [x] The 239 existing tests pass **unmodified** — the suite disables logging, exactly as it was
      blind to the progress display in 004.
- [x] `git status --porcelain` is empty after a full test run; no `var/` appears in the working tree.
- [x] `uv run ruff check .` is clean, and **no new dependency** is added to `pyproject.toml`.

## Out of scope

- **A new dependency.** `structlog` and `loguru` are both good and both unnecessary here: stdlib
  `logging` already provides handlers, levels and formatters, and the structured, queryable view of
  a sync is exactly what `sync_runs` / `sync_events` already are. 003 shipped an HTTP layer and 004
  a progress UI with zero additions; this matches.
- **JSONL or any machine-readable format.** The file is for a human reading it after a bad run. If
  something ever needs to parse a run, it should read SQLite.
- **Size-based rotation.** Date-and-command naming is the rotation. A single run cannot plausibly
  produce enough output for size to matter before retention does.
- **Replacing `-v` with a stderr mirror of the log.** Tempting, and a real simplification, but it
  changes existing user-visible behaviour and the tests that pin it. Separate feature.
- **Logging inside `engine.plan()`.** It is pure, and `--dry-run` depends on that. `cli.py` logs the
  result instead.
- **Shipping logs anywhere.** No syslog, no journald, no remote sink, no `--log-file` flag.
- **Logging the config load itself.** `Settings` has to exist before there is a log directory to
  write to, so a `ConfigError` raised by `_load()` is the one failure that cannot be logged. It is
  already reported on stderr with exit code 2.
