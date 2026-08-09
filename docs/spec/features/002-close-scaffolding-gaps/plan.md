# 002 · Close the scaffolding gaps — Plan

**Status:** specced, not started · **Last updated:** 2026-08-09

How the six gaps in [`spec.md`](spec.md) get closed. A living document: update it when the work
lands.

---

## Approach

Nothing here changes the architecture, and nothing here is allowed to bend the three invariants —
every `notebooklm` call still goes through `nlm.py`, `plan()` stays pure, and sync still never
deletes. Each gap is small enough to land and verify on its own, so they are built in dependency
order and each is green before the next starts:

1. **Source kind in the mirror** — a value we already hold, threaded one field further.
2. **`-v/--verbose`** — a callback on the adapter, rendered by the CLI.
3. **`notebooks` health check** — wire up `list_notebooks()`.
4. **`--only-stale`** — wire up `is_stale()` as an explicit read-only step between plan and execute.
5. **CLI tests** — cover all of the above, plus the pre-existing exit-code contract.
6. **CI** — run what now exists on someone else's machine.

The CLI tests come after the behaviour rather than before because they are the first tests in this
repo to drive `cli.py` at all; writing them against the finished flag surface avoids rewriting them
twice. Everything they assert is already specified as an acceptance criterion.

---

## Implementation

### 1 · Source kind in the local mirror

`cli.py` hardcodes `kind=None` in its `upsert_source` call, so `sources.kind` is always NULL.
The kind is known — it is on the `RemoteSource` that `plan()` matched against — it is simply never
carried across.

- `models.py`: add `kind: str | None = None` to `PlannedAction`.
- `engine.plan()`: set `kind=match.kind` on the SKIP and REFRESH branches (including the
  not-refreshable degradation, which already reads `match.kind` for its reason string). This keeps
  `plan()` pure — it copies a value it was handed, it does not go looking for one.
- `engine.execute()`: on ADD, after `client.add_source()` returns a `RemoteSource`, set
  `action.kind = created.kind or action.type`.
- `cli.py`: pass `kind=action.kind`.
- `db.upsert_source()`: change the conflict clause from `kind = excluded.kind` to
  `kind = COALESCE(excluded.kind, kind)`. Without this, a run that happens to know less — an add
  whose response carried no type — nulls a kind an earlier run recorded correctly. `title` and
  `status` are genuinely current-state values and keep overwriting.

No schema change: the `kind` column already exists, so `SCHEMA_VERSION` stays `1`.

### 2 · `-v/--verbose`

`NlmClient.calls` already records the argv of every invocation and nothing reads it.

- `nlm.py`: add `on_call: Callable[[list[str]], None] | None = None` to `NlmClient.__init__`, and
  call it in `_run()` immediately after `self.calls.append(argv)`. The adapter never prints — it
  hands the argv to whoever asked.
- `cli.py`: `-v/--verbose` on `sync`, `status` and `notebooks`; `_client()` grows a `verbose`
  parameter and passes a callback that writes `$ notebooklm …` to **stderr** through the existing
  `err_console`, dimmed. stdout stays clean for piping.

### 3 · `notebooks` as a health check

`nlm.list_notebooks()` is implemented, unit-tested and never called.

- `cli.notebooks()` calls it once, builds `{id: title}` from the rows, and adds a **Remote** column:
  `ok` when the configured `notebook_id` is a key, a red `missing` when it is not.
- The call is wrapped: on any `SyncError` the command prints a dim
  `could not reach notebooklm: <message>` line, renders the column as `?` for every row, and
  continues to exit 0.

The wire shape was read off the installed v0.7.3 (`notebooklm/cli/notebook_cmd.py`): `list --json`
serialises `{"id", "title", "is_owner", "created_at"}` rows under an `items_key` of `"notebooks"`.
`list_notebooks()` already accepts either a bare list or that envelope; keep both paths and pin the
shape with `tests/fixtures/notebook_list.json`.

### 4 · `--only-stale`

`nlm.is_stale()` is implemented, unit-tested and never called.

`plan()` is pure and holds no client, so the staleness probe cannot live inside it. It becomes an
explicit, separately-named step in `engine.py`:

```python
def apply_stale_filter(plan_: SyncPlan, client: NlmClient) -> None:
    """Read-only: turn REFRESH actions for fresh sources into SKIPs."""
```

It walks `Action.REFRESH` actions, calls `client.is_stale(notebook_id, source_id)`, and rewrites the
ones that come back `False` into `Action.SKIP` with `reason="not stale"`.

`cli.sync()` calls it between `plan()` and the dry-run branch when `--only-stale` is given, so the
**same** filtered plan feeds both the dry-run preview and the real execution. This is not a second
dry-run path: it is one path that both modes run through.

**Fail open.** `is_stale()` returning `None` (no usable `stale` field) or raising `NlmError` leaves
the action as a REFRESH. A probe that cannot answer must not silently stop content from refreshing.

Only `override` produces REFRESH actions, so `--only-stale` under `skip` or `create` is a no-op —
accepted, documented, not an error.

### 5 · CLI tests

New `tests/test_cli.py`, driving the app with `typer.testing.CliRunner` over the existing fixtures.
Config is set with `monkeypatch.setenv` (`NOTEBOOKS=research`, `NOTEBOOK_RESEARCH_ID`,
`NOTEBOOK_RESEARCH_SOURCES` → a manifest in `tmp_path`, `SYNC_DB_PATH` → `tmp_path`) plus
`monkeypatch.chdir(tmp_path)`, so no real `.env` and no repo database is ever touched. A small
helper builds that environment once.

Cases:

| Case | Asserts |
|---|---|
| `sync --dry-run` | exit 0; `fake_cli.commands()` is exactly `["source list"]` |
| `sync` happy path | exit 0; `source add` issued with the **original** URL; `sources` row written |
| `auth check` error envelope | exit 3; message contains the `notebooklm login` hint |
| `source wait` exit 2 | outcome `pending`; run exit 0 |
| `source add` error envelope | exit 1; run recorded as `failed` |
| no `NOTEBOOKS` / unknown name / missing manifest | exit 2 |
| `status`, `history` | exit 0, render |
| `notebooks` | remote column `ok`; unknown ID → `missing`; `list` error → warning, exit 0 |
| `init` in an empty dir | creates `.env` and `sources/example.yaml`; second run is idempotent |
| `-v` | argv lines on stderr |
| `--only-stale` | fresh → skipped, no `source refresh`; stale → refreshed |

The fake shim already routes `list` and `source stale`, so no shim change is expected. If one turns
out to be needed, it belongs in `fake_notebooklm.py` and the `test-fixtures` skill gets updated.

### 6 · CI

`.github/workflows/ci.yml` — on `push` and `pull_request`, `ubuntu-latest`, matrix Python `3.11`
(the declared floor) and `3.12` (what runs locally): `astral-sh/setup-uv` with caching, then
`uv sync`, `uv run ruff check .`, `uv run pytest`.

**No secrets, no credentials, no `NOTEBOOKLM_AUTH_JSON`.** The suite is offline by construction; if
a test ever needs a secret to pass in CI, the test is broken, not the workflow.

---

## Decisions

- **`notebooks` degrades instead of failing.** Every other command turns a `SyncError` into its exit
  code. `notebooks` deliberately does not: it is the command you run *because* something is wrong,
  and expired cookies are the most likely reason. Refusing to show your configuration at the moment
  your session breaks is the wrong failure mode, so the remote check warns, renders `?`, and exits
  0. The rejected alternative — an opt-in `--check` flag with a hard exit 3 — keeps the exit-code
  rule tidy at the cost of the check almost never being run.
- **The staleness probe also runs under `--dry-run`.** `source stale` is read-only, so probing costs
  the dry run nothing it was not already spending on `source list`, and it keeps the preview exactly
  equal to the real run. Skipping the probe in dry-run would make `--dry-run --only-stale`
  over-report refreshes — the precise kind of drift the "no dry-run-only code path" limit exists to
  prevent.
- **`--only-stale` fails open.** An unreachable or unparseable probe leaves the refresh in place.
  The failure mode of refreshing unnecessarily is a wasted call; the failure mode of skipping
  wrongly is a notebook quietly serving stale content, which is the thing the tool exists to
  prevent.
- **The stale decision reads the JSON `stale` field, never the exit code.** `source stale` *inverts*
  its exit code under `--exit-on-stale` (0 = stale, 1 = fresh). `is_stale()` already does the right
  thing; this is written down so nobody "simplifies" it into exit-code logic later.
- **`COALESCE` on `kind`, plain overwrite on `title`/`status`.** `kind` is an immutable property of
  a source that we sometimes fail to learn; the others are current state we always know. Treating
  them the same in either direction loses information.
- **The verbose callback lives on the adapter, the printing lives in the CLI.** `nlm.py` stays free
  of Rich and of anything that writes to a console, so it remains a pure boundary.
- **`apply_stale_filter()` is a named step, not a branch inside `plan()`.** Keeping the probe out of
  `plan()` is what keeps `plan()` a pure function of (manifest, remote sources) — still unit-testable
  against literals with no client at all.
- **CI runs 3.11 and 3.12.** `requires-python = ">=3.11"` is a claim nobody has ever tested; the
  matrix makes it true or makes it fail.

---

## Risks

- **`notebooklm list --json`'s envelope is inferred from upstream source, not captured live.** The
  shape was read from the installed v0.7.3 (`cli/notebook_cmd.py`), not from a real response.
  *Mitigation:* `list_notebooks()` tolerates both a bare list and the `{"notebooks": [...]}`
  envelope, and `tests/fixtures/notebook_list.json` gets captured from a real run and scrubbed
  before committing — the same guard `source_list.json` gives the source shape.
- **`--only-stale` adds one upstream call per matched source**, which is slower on a large notebook
  under `override` when most sources turn out to be stale anyway. *Mitigation:* it is opt-in;
  `override` without the flag behaves exactly as it does today.
- **`notebooks` now makes a network call on every invocation**, so a command that used to be instant
  and offline can hang until the CLI timeout. *Mitigation:* it goes through `NlmClient`'s existing
  `cli_timeout`, and a timeout is a `SyncError`, which is exactly the degrade path.
- **CLI tests can leak into the real environment** — a forgotten `chdir` reads the developer's
  `.env`, and a forgotten `SYNC_DB_PATH` writes `./notebooklm-sync.db` in the repo. *Mitigation:*
  one shared helper sets up the environment, `clean_env` strips inherited `NOTEBOOK*` / `SYNC_*`,
  and the acceptance criteria require the suite to pass with no `.env` present.
- **CI is the first thing here that runs on a machine nobody owns.** If it needs a secret to go
  green, the honest fix is fixing the test. *Mitigation:* stated as an acceptance criterion, and
  `NOTEBOOKLM_AUTH_JSON` remains forbidden by `tech-stack.md`.

---

## Verification

**Offline (required)**

- `uv run pytest` — green, including the new `tests/test_cli.py`.
- `uv run ruff check .` — clean.
- `grep -rn "^import subprocess\|^from subprocess" src/` — matches only `nlm.py`.
- Against the fake shim: `--dry-run --only-stale` issues `source list` and `source stale` and
  nothing else; `notebooks` with a failing `list` scenario still exits 0.
- `sqlite3 <db> "SELECT source_id, kind FROM sources"` after a sync against the shim — no NULL
  `kind` for sources the notebook reported.
- The `sync-verifier` agent (`model: sonnet`) for the end-to-end dry-run plus DB assertions.

**Live (manual, only if the user has run `notebooklm login`)**

- `notebooklm auth check --test` first; report honestly if it is expired rather than assuming.
- `notebooklm-sync notebooks` against a real profile — a real ID reads `ok`, a made-up
  `NOTEBOOK_*_ID` reads `missing`.
- `notebooklm-sync sync <nb> --policy override --only-stale -v` against a throwaway notebook —
  fresh sources skipped, `source refresh` issued only for stale ones, and the argv visible on
  stderr.
- Capture and scrub `notebooklm list --json` into `tests/fixtures/notebook_list.json`.

**CI** — the workflow is green on a pull request, with no secrets configured.

---

Progress is tracked in [`tasks.md`](tasks.md). When this lands, move 002 to "Done" in
[`../../constitution/roadmap.md`](../../constitution/roadmap.md).
