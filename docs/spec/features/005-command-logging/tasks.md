# 005 · Command and action logging — Tasks

Derived from [`plan.md`](plan.md). Six groups, in dependency order; each must be green
(`uv run pytest` + `uv run ruff check .`) before the next one starts.

## 1 · The SDD documents

- [x] `docs/spec/features/005-command-logging/spec.md` — behaviour, the level split, acceptance
      criteria, and what this deliberately is not.
- [x] `docs/spec/features/005-command-logging/plan.md` — the fourth seam, the emitter table, the
      `cli.py` shape, and why stdlib beat `structlog` / `loguru`.
- [x] This file.
- [x] `roadmap.md`: 005 under **Next 🔜**, replacing the "nothing scheduled" note.

## 2 · `logs.py` — the only module that opens a log file

- [x] `logging.getLogger("notebooklm_sync").addHandler(logging.NullHandler())` at import, so
      library use and tests emit into nothing without a warning.
- [x] `log_path(log_dir, command, *, today=None)` → `<YYYY-MM-DD>-<command>.log`. `today` is a
      parameter so a test pins the date without patching the clock.
- [x] `session(command, *, settings)` — prune, `mkdir -p`, attach one
      `FileHandler(mode="a", encoding="utf-8", delay=True)` at `settings.log_level`, yield the run
      token.
- [x] Teardown in a `finally`: flush, close, remove the handler. Not bookkeeping — `CliRunner` runs
      many invocations per process, and a leaked handler doubles every later record.
- [x] The run token (`uuid4().hex[:8]`) injected by a `logging.Filter`, not by call sites, so a
      shared daily file stays attributable line by line.
- [x] `settings.log_enabled is False` attaches nothing and still yields a token, keeping `cli.py`
      branch-free — the same argument `NullReporter` rests on.
- [x] `prune(log_dir, *, days)` — `*.log` older than `days` by mtime, non-recursive, `0` disables,
      and every failure swallowed so it can never fail the command the user asked for.
- [x] Confirm the module imports neither `rich`, `subprocess` nor `urllib`.

## 3 · `config.py`

- [x] `DEFAULT_LOG_DIR = "./var/log"` and `DEFAULT_LOG_RETENTION_DAYS = 30`.
- [x] `log_dir: Path` from `SYNC_LOG_DIR`, mirroring how `db_path` reads `SYNC_DB_PATH`.
- [x] `log_enabled: bool` from `SYNC_LOG` via the existing `_parse_bool` (a typo turns logging *on*).
- [x] `log_retention_days: int` from `SYNC_LOG_RETENTION_DAYS` via the existing `_parse_int`.
- [x] Confirm `log_level` — present and unused since 001 — is now actually consumed.

## 4 · The emitters

- [x] `nlm.py`: `$ <argv>` at INFO in `_run()`, beside the existing `on_call`; `rc=<n> in <ms>ms`
      after, timed with `time.monotonic()`.
- [x] `nlm.py`: the in-band error envelope's `code`/`message` at ERROR in `_payload()` — and
      **never the payload itself**, which for `auth check --test` describes the Google account.
- [x] `nlm.py`: `wait_source()` records its `WaitStatus`, so exit-2-is-a-timeout is visible in the
      file rather than inferred from an absence.
- [x] `discovery.py`: DEBUG per fetch in `_get()` (url, status, bytes); DEBUG cache hit/miss and
      INFO the resolved count and its source in `resolve_rule()`.
- [x] `engine.py`: INFO per action in `execute()`; DEBUG per probe in `apply_stale_filter()`.
- [x] `engine.py`: confirm `plan()` emits **nothing** — it is pure, and `--dry-run` depends on it.
- [x] `db.py`: DEBUG on schema creation and the `user_version` stamp.
- [x] `manifest.py`: the duplicate-URL warning also emits at WARNING. Leave the bare `print` alone;
      user-visible output is unchanged.
- [x] Confirm no module signature changed and no new parameter was threaded anywhere.

## 5 · `cli.py`

- [x] `_logged(command, settings)` — a context manager over `logs.session` that records `start:`,
      then `done: exit=<n>` on `typer.Exit`, `failed: … (exit=<n>)` on `SyncError`, and
      `log.exception` on anything else, re-raising in every case.
- [x] Wrap all six command bodies: `sync`, `status`, `expand`, `notebooks`, `history`, `init`.
- [x] Keep `_load()` **inside** the existing outer `try`. Hoisting it to get `settings` earlier
      would turn a bad `.env` from today's clean `exit 2` into a traceback, and `test_cli.py` pins
      that contract. The trade — a `ConfigError` from `_load()` is the one failure that cannot be
      logged — is documented in `spec.md` under *Out of scope*.
- [x] `init` uses `load_settings()` directly, not `_load()`: it exists to be run before a valid
      `.env` exists, so it must not require a configured notebook.
- [x] `sync` additionally logs `plan: run=<sync_runs.id> policy=… add=… refresh=… skip=…` after
      `start_run`, and the final counts with the exit code.
- [x] Confirm `-v`, the reporter and the results tables are untouched, and that stdout is
      byte-identical.

## 6 · Tests, docs and closing out

- [x] `tests/conftest.py`: an **autouse** fixture setting `SYNC_LOG=off`. This is what makes "log
      everything except tests" true, and why the 239 existing tests pass unmodified.
- [x] `tests/test_logs.py`: filename for a given date/command · two sessions append to one file with
      distinct tokens · two commands, two files · `SYNC_LOG=off` writes nothing · DEBUG admits what
      INFO excludes · the package logger's handler count returns to its starting value · prune
      removes an old file and spares a fresh one · prune survives an unreadable directory.
- [x] `tests/test_cli.py`: a `SyncError` reaches the file with its exit code; a successful `sync`
      records argv, exit code and per-action outcomes; no ANSI escape reaches a log file.
- [x] `.gitignore`: `var/`, with a comment saying why.
- [x] `.env.example` and `cli.ENV_TEMPLATE`: `SYNC_LOG`, `SYNC_LOG_DIR`, `SYNC_LOG_RETENTION_DAYS`,
      commented, beside the `SYNC_LOG_LEVEL` that is already there.
- [x] `AGENTS.md`: `logs.py` in the data-flow block, and the log-file seam added to *The five
      invariants* — which becomes six.
- [x] `constitution/tech-stack.md`: the seam under *Hard limits*, the new keys in the config list,
      and `logs.py` under *Key files*.
- [x] `README.md`: where the logs live and how to turn them off.
- [x] Run the manual terminal checklist in [`plan.md`](plan.md), including
      `grep -iE "cookie|sid|storage_state|__Secure" var/log/*.log` → nothing.
- [x] Tick the acceptance criteria in [`spec.md`](spec.md) and set its status.
- [x] Move 005 to **Done** in [`../../constitution/roadmap.md`](../../constitution/roadmap.md).
- [x] `engram save` the fourth-seam decision, why stdlib beat `structlog`/`loguru` here, and
      anything the implementation cost real debugging time — always `--project notebooklm-sync`.

## Maintenance (recurring checklist)

Repeat these whenever this feature is touched again:

- [ ] `uv run pytest` and `uv run ruff check .` after any change under `src/`.
- [ ] Re-grep the four seams: `subprocess` only in `nlm.py`, HTTP only in `discovery.py`, `rich`
      only in `cli.py` / `progress.py`, and `basicConfig|addHandler|FileHandler` only in `logs.py`.
- [ ] Re-check that no payload, cookie or `.env` value has crept into a record — the easiest
      regression to introduce and the most expensive to notice, since a log file gets pasted into
      bug reports.
- [ ] Confirm the log is still a *record*, never a channel to the user at the terminal. The moment
      `log.info` is used to tell someone something, it has become a second presentation layer.
- [ ] Keep `.env.example`, `ENV_TEMPLATE` and `config.py` in step; a documented key that does not
      exist is worse than an undocumented one.
- [ ] Update this plan's status and the roadmap when work lands.
