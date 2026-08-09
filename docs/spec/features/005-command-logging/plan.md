# 005 · Command and action logging — Plan

**Status:** implemented ✅ · **Last updated:** 2026-08-09

How the log files in [`spec.md`](spec.md) get written. A living document: update it when the work
lands.

---

## Approach

This feature adds **a fourth kind of I/O**, and every previous one in this codebase got the same
treatment: one module owns the capability, and nothing else in the package is allowed to perform it.
`nlm.py` owns `subprocess`, `discovery.py` owns HTTP, `progress.py` owns rendering. A new `logs.py`
owns **attaching a handler and opening a log file** — and that is *all* it owns.

The rest of the package does not write logs; it **emits records**, through
`logging.getLogger(__name__)`. That is not a new pattern, it is the existing *"the library notifies;
only `cli.py` renders"* rule stated in stdlib-logging vocabulary: a record with no handler attached
goes nowhere and costs almost nothing, exactly like an `on_*` callback left at `None`. The two
differences from the 004 callbacks are both in this feature's favour:

- **No plumbing.** A logger is reachable from inside a function without threading a parameter
  through every caller — which matters here because the interesting data (an exit code, a duration,
  a `WaitStatus`) lives in the *middle* of `nlm._run()`, where no callback fires today.
- **Levels are free.** `SYNC_LOG_LEVEL` selects the fetch-by-fetch detail without a second code
  path, and the setting already exists in `config.py` and has been dead since 001.

The one place this could erode the design is `engine.plan()`, which is pure and must stay pure —
`--dry-run` correctness rests on it. So `plan()` emits nothing, and `cli.py` logs the plan summary
after it returns. `execute()` already mutates, so it emits directly.

Choosing stdlib over the alternatives is covered in *Decisions*.

Built in dependency order, each green before the next starts:

1. **The SDD documents** — this folder plus the roadmap entry, before any `src/` change.
2. **`logs.py`** — the seam, testable on its own against a `tmp_path` with no CLI involved.
3. **`config.py`** — three new settings, and `log_level` finally consumed.
4. **The emitters** — `nlm`, `discovery`, `engine`, `db`, `manifest`; records only, no behaviour.
5. **`cli.py`** — where the session opens and closes, per command.
6. **Tests, then docs** — including the autouse switch that keeps the suite silent.

`logs.py` comes first because every question worth settling — the filename, the append behaviour,
handler teardown, retention — is settled far more cheaply against `tmp_path` than through a
`CliRunner`.

---

## Implementation

### 1 · `logs.py` — the only module that opens a log file

```python
DEFAULT_LOG_DIR = "./var/log"
ROOT = "notebooklm_sync"      # the package logger every getLogger(__name__) rolls up to
FORMAT = "%(asctime)s %(levelname)-5s %(token)s %(module)-9s %(message)s"

logging.getLogger(ROOT).addHandler(logging.NullHandler())   # at import

def log_path(log_dir: Path, command: str, *, today: date | None = None) -> Path
def prune(log_dir: Path, *, days: int) -> int
@contextmanager
def session(command: str, *, settings: Settings) -> Iterator[str]
```

- **`log_path`** — `log_dir / f"{today:%Y-%m-%d}-{command}.log"`. `today` is a parameter so a test
  can pin a date without patching the clock.
- **`session`** — the whole lifecycle of one CLI invocation: prune, `mkdir -p`, attach exactly one
  `FileHandler` to the `notebooklm_sync` logger, yield the run token, then **flush, close and
  detach in a `finally`**. Teardown is not optional bookkeeping: `CliRunner` runs many invocations
  in one process, so a handler that outlives its command would double every subsequent record and
  hold a file descriptor open on Windows and in tmp dirs.
- **The run token** — `uuid4().hex[:8]`, injected onto every record by a `logging.Filter` rather
  than by every call site. A shared daily file is only readable if each line says which invocation
  it came from.
- **Disabled** — `settings.log_enabled is False` yields the token and attaches nothing, so `cli.py`
  stays branch-free (the same argument `NullReporter` rests on in 004). `delay=True` on the handler
  means an enabled session that emits nothing still creates no file.
- **`prune`** — unlink `*.log` older than `days` by mtime; `0` disables. Wrapped so that an
  unreadable directory or a permission error never fails the command the user actually asked for.
- The module imports `logging`, `pathlib`, `uuid`, `contextlib`, `datetime` — and **not** `rich`,
  `subprocess` or `urllib`.

### 2 · `config.py`

Three new fields, following the `db_path` pattern exactly — a module constant, a `SYNC_*` key, and
directory creation left to the consumer:

| Field | Env var | Default | Parsed by |
|---|---|---|---|
| `log_dir: Path` | `SYNC_LOG_DIR` | `./var/log` | `Path(...)`, as `db_path` is |
| `log_enabled: bool` | `SYNC_LOG` | `True` | the existing `_parse_bool` |
| `log_retention_days: int` | `SYNC_LOG_RETENTION_DAYS` | `30` | the existing `_parse_int` |
| `log_level: str` | `SYNC_LOG_LEVEL` | `INFO` | **already there since 001, unused** |

`_parse_bool`'s "anything not in `FALSEY` is true" rule means a typo turns logging *on*, which is
the failure the user can see — the same reasoning `SYNC_PROGRESS` already relies on.

### 3 · The emitters

Each module gains `log = logging.getLogger(__name__)` and nothing else. No signature changes, no
new parameters, no behaviour changes.

| Module | Level | What it records |
|---|---|---|
| `nlm._run` | INFO | `$ <argv>` before the call; `rc=<n> in <ms>ms` after, timed with `time.monotonic()` |
| `nlm._payload` | ERROR | the in-band error envelope's `code` and `message` — **never the payload** |
| `nlm.wait_source` | INFO | `wait: <WaitStatus> after <ms>ms`, so exit-2-is-a-timeout is visible |
| `discovery._get` | DEBUG | url, status, byte count |
| `discovery.resolve_rule` | DEBUG / INFO | cache hit or miss (DEBUG); the resolved count and its source (INFO) |
| `engine.execute` | INFO | `<ACTION> <url> -> <OUTCOME> (<message>)` per action |
| `engine.apply_stale_filter` | DEBUG | one line per probe |
| `engine.plan` | — | **nothing. It is pure.** |
| `db.init_db` | DEBUG | schema creation and the `user_version` stamp |
| `manifest` | WARNING | the duplicate-URL warning that today is a bare `print()` to stdout, bypassing every seam. The `print` stays; user-visible output is unchanged |

`nlm._run` already fires `on_call(argv)` at exactly the point the argv record belongs, so the two
sit together and the parallel between the `-v` echo and the log line stays obvious.

### 4 · `cli.py`

Every command already has the shape `try: settings = _load(); … except SyncError as exc: _fail(exc)`.
The session slots inside that existing `try`, so the diff per command is two lines and an indent:

```python
try:
    settings = _load()
    with _logged("sync", settings):
        ...                       # the existing body, unchanged
except SyncError as exc:
    _fail(exc)
```

`_logged` is a small local context manager over `logs.session`:

```python
@contextmanager
def _logged(command: str, settings: Settings, ctx: typer.Context | None = None) -> Iterator[None]:
    with logs.session(command, settings=settings):
        log.info("start: %s", _invocation(command, ctx))
        try:
            yield
        except typer.Exit as exc:          # the normal path — every command exits this way
            log.info("done: exit=%d", exc.exit_code)
            raise
        except SyncError as exc:
            log.error("failed: %s (exit=%d)", exc, exc.exit_code)
            raise
        except BaseException:
            log.exception("crashed")
            raise
        else:
            log.info("done: exit=0")
```

**Keeping `_load()` inside the outer `try` is deliberate.** Hoisting it out to get `settings` before
the session would leave a `ConfigError` from `_load()` itself unhandled, turning today's clean
`exit 2` into a traceback — and `test_cli.py` pins that contract. The cost is that the one failure
which cannot be logged is a bad `.env`, which is documented in *Out of scope* and is already
reported on stderr.

`typer.Exit` is caught, logged and re-raised rather than swallowed: it is not an error, it is how
every command ends, and its `exit_code` is the single most useful thing in the file.

**The start line is built from the Typer context, not `sys.argv`.** Each command takes a
`ctx: typer.Context` (Typer injects it and it is not a CLI option) which `_invocation()` renders as
`sync dry_run notebook=research`. `sys.argv` was the first attempt and it is wrong twice over: under
`CliRunner` it is *pytest's* command line, which the tests caught immediately, and even in a real
run it misses a value that came from `.env` and was never typed. Typer 0.27 vendors click as
`typer._click`, so `click.get_current_context()` is not available as a shortcut.

Two extras, both in `sync` only: `plan: run=<sync_runs.id> policy=… add=… refresh=… skip=…` after
`start_run`, so a log line joins to a SQLite row; and a `results:` line with the final counts, which
is where `pending` and `failed` first become real — `plan()` cannot know either.

`init` is the exception to `_load()` — it exists to be run *before* a valid `.env` exists, so it
calls `load_settings()` directly, which does not require any notebook to be configured.

**Nothing about `-v`, `progress.py` or the results tables changes.**

### 5 · Tests

- `tests/conftest.py` — an **autouse** fixture setting `SYNC_LOG=off` for every test. This is what
  makes *"log everything except tests"* true, and it is why the 239 existing tests pass unmodified.
- `tests/test_logs.py` — opts back in by pointing `SYNC_LOG_DIR` at `tmp_path`, the same
  "throwaway, never the repo's real one" convention as the existing `db_path` fixture:
  filename for a given date and command · two sessions append to one file with distinct tokens ·
  two commands produce two files · `SYNC_LOG=off` creates nothing · DEBUG admits records INFO
  excludes · the handler count on the package logger returns to its starting value after the
  session · prune removes an old file and spares a fresh one · prune survives an unreadable
  directory.
- `tests/test_cli.py` — a `SyncError` reaches the file with its exit code; a successful `sync`
  records argv, exit code and per-action outcomes; no ANSI escape reaches a log file.

### 6 · Docs

`AGENTS.md` (the data-flow block and the invariants, which become six), `tech-stack.md` (the seam
under *Hard limits*, the new keys in the config list), `.env.example` + `ENV_TEMPLATE`, `README.md`,
and `.gitignore`:

```gitignore
# Command logs — var/log/YYYY-MM-DD-<command>.log, local runtime state only
var/
```

---

## Decisions

- **stdlib `logging`, not `structlog` or `loguru`.** Both are better than stdlib at the thing this
  feature does not need. `structlog`'s advantage is structured fields and `bind()` context for
  querying across services — but the queryable view of a run already exists here as `sync_runs` /
  `sync_events`, and duplicating it as JSONL would create two sources of truth about the same run.
  `loguru`'s advantage is zero configuration and built-in rotation — but it replaces the stdlib
  root rather than integrating with it, and its module-level singleton is the wrong shape for a
  seam whose whole job is attaching and detaching cleanly per invocation. Against that, stdlib
  costs about forty lines and no dependency. 003 shipped an HTTP layer and 004 a progress UI with
  zero additions; this matches.
- **Date-and-command filenames, not size rotation.** `RotatingFileHandler` answers "the file got too
  big", which is a daemon's problem. This is a short-lived CLI, and the question actually asked
  after a bad run is *"what did `sync` do this morning?"* — which the filename answers directly.
  Retention by age, not by count, for the same reason.
- **`getLogger(__name__)` in the library, not a fifth `on_*` callback.** The 004 callbacks are the
  right shape for *rendering*, where the renderer must not be reachable from library code. A logger
  is inert by default, so the same protection comes for free — and the data worth logging (exit
  codes, durations) sits where no callback fires. Adding `on_result` to `NlmClient` and threading it
  through would be more code for a weaker result.
- **Text, not JSONL.** The reader is a human, after something went wrong. Machines read SQLite.
- **`plan()` stays silent.** Purity is load-bearing for `--dry-run`; a log call is I/O. `cli.py` logs
  the summary instead, which is the same thing one frame up the stack.

---

## Risks

- **A handler outliving its command.** `CliRunner` invokes the app many times in one process; a
  leaked handler would double every record and pin a file descriptor. *Mitigation:* teardown in a
  `finally`, plus a test asserting the package logger's handler count returns to its starting value.
- **A test writing into the repo.** A forgotten env var would put `var/log/` in the working tree and
  eventually into someone's commit. *Mitigation:* the autouse `SYNC_LOG=off` fixture, `var/` in
  `.gitignore`, and `git status --porcelain` in the verification checklist.
- **Logging becoming a second presentation layer.** The failure mode is someone reaching for
  `log.info` to tell the *user* something, instead of `console.print`. *Mitigation:* state it in
  `AGENTS.md` — the log is a record of what happened, never a channel to the person at the terminal.
- **A secret reaching disk.** `auth check --test`'s payload describes the Google account, and log
  files are far easier to paste into a bug report than a database is. *Mitigation:* payloads are
  never logged at any level — only argv, exit codes, durations, and an error's `code`/`message` —
  plus an explicit acceptance criterion and a grep of a real log file during live verification.
- **Prune deleting something it should not.** *Mitigation:* it only ever matches `*.log` directly
  inside the configured directory, never recurses, and never follows a directory.

---

## Verification

**Offline (required, gates the work)**

- [x] `uv run pytest` — the 239 existing tests pass unmodified, plus the new `test_logs.py` cases.
- [x] `uv run ruff check .` clean. Do **not** run `ruff format`.
- [x] `grep -rnE "basicConfig|addHandler|FileHandler" src/` → `logs.py` only.
- [x] `grep -rn "rich" src/` → still `cli.py` and `progress.py` only.
- [x] `grep -rn "subprocess" src/` → still `nlm.py` only; HTTP still `discovery.py` only.
- [x] `git status --porcelain` empty after a full test run; no `var/` in the working tree.

**Manual, in a real terminal**

```bash
uv run notebooklm-sync expand research
uv run notebooklm-sync expand research                    # same day, same command
cat var/log/$(date +%F)-expand.log                        # two runs, two tokens, one file
SYNC_LOG_LEVEL=DEBUG uv run notebooklm-sync sync research --dry-run   # GET lines appear
uv run notebooklm-sync sync nonexistent; echo "exit=$?"   # exit 2, and the error is in the file
SYNC_LOG=off uv run notebooklm-sync status research       # nothing written
```

**Live (manual, gated on auth)**

- [x] One real `sync` with `notebooklm auth check --test` passing: exit codes and durations for
      actual `source add` / `source wait` calls appear, and a `source wait` timeout is recorded as
      `TIMEOUT` rather than as a failure.
- [x] `grep -iE "cookie|sid|storage_state|__Secure" var/log/*.log` → nothing.

**CI**

- [x] `.github/workflows/ci.yml` needs no change — the suite disables logging and writes nothing.

---

Progress is tracked in [`tasks.md`](tasks.md). When this lands, move 005 to "Done" in
[`../../constitution/roadmap.md`](../../constitution/roadmap.md).
