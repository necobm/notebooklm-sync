# 002 · Close the scaffolding gaps — Tasks

Derived from [`plan.md`](plan.md). Groups 1–7 shipped on 2026-08-09; each was green
(`uv run pytest` + `uv run ruff check .`) before the next one started. The one open box is the
first CI run, which needs a push to a remote.

## 1 · Source kind in the local mirror

- [x] `models.py`: add `kind: str | None = None` to `PlannedAction`.
- [x] `engine.plan()`: set `kind=match.kind` on the SKIP, REFRESH and not-refreshable branches.
- [x] `engine.execute()`: on ADD, set `action.kind = created.kind or action.type` from the
      `RemoteSource` returned by `add_source()`.
- [x] `cli.py`: pass `kind=action.kind` to `upsert_source` instead of `None`.
- [x] `db.upsert_source()`: `kind = COALESCE(excluded.kind, kind)` in the conflict clause; leave
      `title` and `status` overwriting.
- [x] Tests: `plan()` carries the kind (pure, no shim); `execute()` fills it on add; a second upsert
      with `kind=None` does not null the stored value.
- [x] Confirm `PRAGMA user_version` is still `1` — no migration.

## 2 · `-v/--verbose`

- [x] `nlm.py`: `on_call: Callable[[list[str]], None] | None = None` on `NlmClient.__init__`, invoked
      in `_run()` right after `self.calls.append(argv)`.
- [x] `cli.py`: `-v/--verbose` on `sync`, `status` and `notebooks`; `_client(settings, verbose=…)`
      passes a callback printing `$ notebooklm …` dimmed to `err_console`.
- [x] Verify no `print`/`Console` usage entered `nlm.py`.
- [x] Test: with `-v` the argv appears on stderr; without it stdout is unchanged.

## 3 · `notebooks` health check

- [x] `cli.notebooks()`: call `client.list_notebooks()`, build `{id: title}`, add a **Remote** column
      rendering `ok` / red `missing`.
- [x] Wrap the call: on `SyncError`, print `could not reach notebooklm: <message>` dimmed, render
      `?` for every row, exit 0.
- [x] `tests/fixtures/notebook_list.json` — scrubbed capture of `notebooklm list --json`, pinned by a
      test the way `test_live_shape.py` pins the source shape.
- [x] Tests: configured ID present → `ok`; absent → `missing`, exit 0; failing `list` scenario →
      warning, exit 0.

## 4 · `--only-stale`

- [x] `engine.apply_stale_filter(plan_, client)` — rewrite REFRESH → SKIP (`reason="not stale"`) for
      sources upstream reports as fresh. Read-only; no mutating call.
- [x] Fail open: `is_stale()` returning `None` or raising `NlmError` leaves the REFRESH in place.
- [x] `cli.sync()`: `--only-stale` flag, applied between `plan()` and the dry-run branch so both
      modes share one filtered plan.
- [x] Confirm the decision reads the JSON `stale` field only — never `source stale`'s exit code.
- [x] Tests: fresh → skipped and no `source refresh` in the argv log; stale → refreshed; probe error
      → still refreshed; `--dry-run --only-stale` issues only `source list` + `source stale`.
- [x] Confirm the flag is an accepted no-op under `skip` and `create`.

## 5 · CLI-level tests

- [x] `tests/test_cli.py` with `typer.testing.CliRunner`, plus a helper that sets `NOTEBOOKS`,
      `NOTEBOOK_RESEARCH_ID`, `NOTEBOOK_RESEARCH_SOURCES`, `SYNC_DB_PATH` and `monkeypatch.chdir`
      into `tmp_path`.
- [x] `sync --dry-run` → exit 0 and `commands() == ["source list"]`.
- [x] Exit codes end to end: `0` ok · `1` failed add · `2` config/manifest · `3` auth, with the
      `notebooklm login` hint asserted.
- [x] `source wait` exit 2 → `pending`, run exit 0.
- [x] `status`, `history`, `notebooks` render and exit 0.
- [x] `init` in an empty dir creates `.env` and `sources/example.yaml`, and is idempotent.
- [x] Confirm the suite passes with **no `.env` present** and never writes `./notebooklm-sync.db`.

## 6 · CI

- [x] `.github/workflows/ci.yml` — push + pull_request, `ubuntu-latest`, matrix Python 3.11 / 3.12,
      `astral-sh/setup-uv` with caching, then `uv sync`, `uv run ruff check .`, `uv run pytest`.
- [x] No secrets, no credentials, no `NOTEBOOKLM_AUTH_JSON`.
- [ ] Green on a pull request. **Pending** — the workflow has never run; there is no remote push
      yet. Verified locally instead: the YAML parses, and `ruff check` + `pytest` pass under both
      3.11 (in a scratch venv) and 3.12.

## 7 · Docs and closing out

- [x] `README.md`: document `-v/--verbose`, `--only-stale` and the `notebooks` remote column.
- [x] `AGENTS.md`: replace "There is no CI." in *Commands*; add the new flags to the command list.
- [x] `constitution/tech-stack.md`: **CI** entry now describes the workflow; refresh the test count.
- [x] `../001-project-scaffolding/spec.md`: point its "Out of scope" gaps bullet at this folder.
- [x] Tick the acceptance criteria in [`spec.md`](spec.md) and set its status to implemented.
- [x] Move 002 to **Done** in [`../../constitution/roadmap.md`](../../constitution/roadmap.md).
- [x] `engram save` the outcome (`--type discovery` for anything the upstream CLI taught us,
      `--type decision` for design calls), always `--project notebooklm-sync`.

## Maintenance (recurring checklist)

Repeat these whenever this feature is touched again:

- [ ] `uv run pytest` and `uv run ruff check .` after any change under `src/`.
- [ ] Run the `nlm-cli-contract` agent (`model: sonnet`) after an upstream `notebooklm` version bump
      — `list --json` and `source stale --json` are now load-bearing, not just wrapped.
- [ ] If a wire shape changed, re-capture `tests/fixtures/source_list.json` and
      `tests/fixtures/notebook_list.json` from a live run and **scrub them** before committing.
- [ ] Update this plan's status and the roadmap when work lands.
