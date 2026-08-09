# 002 · Close the scaffolding gaps — Tasks

Derived from [`plan.md`](plan.md). Nothing below is started. Work them in order — each group should
be green (`uv run pytest` + `uv run ruff check .`) before the next one starts.

## 1 · Source kind in the local mirror

- [ ] `models.py`: add `kind: str | None = None` to `PlannedAction`.
- [ ] `engine.plan()`: set `kind=match.kind` on the SKIP, REFRESH and not-refreshable branches.
- [ ] `engine.execute()`: on ADD, set `action.kind = created.kind or action.type` from the
      `RemoteSource` returned by `add_source()`.
- [ ] `cli.py`: pass `kind=action.kind` to `upsert_source` instead of `None`.
- [ ] `db.upsert_source()`: `kind = COALESCE(excluded.kind, kind)` in the conflict clause; leave
      `title` and `status` overwriting.
- [ ] Tests: `plan()` carries the kind (pure, no shim); `execute()` fills it on add; a second upsert
      with `kind=None` does not null the stored value.
- [ ] Confirm `PRAGMA user_version` is still `1` — no migration.

## 2 · `-v/--verbose`

- [ ] `nlm.py`: `on_call: Callable[[list[str]], None] | None = None` on `NlmClient.__init__`, invoked
      in `_run()` right after `self.calls.append(argv)`.
- [ ] `cli.py`: `-v/--verbose` on `sync`, `status` and `notebooks`; `_client(settings, verbose=…)`
      passes a callback printing `$ notebooklm …` dimmed to `err_console`.
- [ ] Verify no `print`/`Console` usage entered `nlm.py`.
- [ ] Test: with `-v` the argv appears on stderr; without it stdout is unchanged.

## 3 · `notebooks` health check

- [ ] `cli.notebooks()`: call `client.list_notebooks()`, build `{id: title}`, add a **Remote** column
      rendering `ok` / red `missing`.
- [ ] Wrap the call: on `SyncError`, print `could not reach notebooklm: <message>` dimmed, render
      `?` for every row, exit 0.
- [ ] `tests/fixtures/notebook_list.json` — scrubbed capture of `notebooklm list --json`, pinned by a
      test the way `test_live_shape.py` pins the source shape.
- [ ] Tests: configured ID present → `ok`; absent → `missing`, exit 0; failing `list` scenario →
      warning, exit 0.

## 4 · `--only-stale`

- [ ] `engine.apply_stale_filter(plan_, client)` — rewrite REFRESH → SKIP (`reason="not stale"`) for
      sources upstream reports as fresh. Read-only; no mutating call.
- [ ] Fail open: `is_stale()` returning `None` or raising `NlmError` leaves the REFRESH in place.
- [ ] `cli.sync()`: `--only-stale` flag, applied between `plan()` and the dry-run branch so both
      modes share one filtered plan.
- [ ] Confirm the decision reads the JSON `stale` field only — never `source stale`'s exit code.
- [ ] Tests: fresh → skipped and no `source refresh` in the argv log; stale → refreshed; probe error
      → still refreshed; `--dry-run --only-stale` issues only `source list` + `source stale`.
- [ ] Confirm the flag is an accepted no-op under `skip` and `create`.

## 5 · CLI-level tests

- [ ] `tests/test_cli.py` with `typer.testing.CliRunner`, plus a helper that sets `NOTEBOOKS`,
      `NOTEBOOK_RESEARCH_ID`, `NOTEBOOK_RESEARCH_SOURCES`, `SYNC_DB_PATH` and `monkeypatch.chdir`
      into `tmp_path`.
- [ ] `sync --dry-run` → exit 0 and `commands() == ["source list"]`.
- [ ] Exit codes end to end: `0` ok · `1` failed add · `2` config/manifest · `3` auth, with the
      `notebooklm login` hint asserted.
- [ ] `source wait` exit 2 → `pending`, run exit 0.
- [ ] `status`, `history`, `notebooks` render and exit 0.
- [ ] `init` in an empty dir creates `.env` and `sources/example.yaml`, and is idempotent.
- [ ] Confirm the suite passes with **no `.env` present** and never writes `./notebooklm-sync.db`.

## 6 · CI

- [ ] `.github/workflows/ci.yml` — push + pull_request, `ubuntu-latest`, matrix Python 3.11 / 3.12,
      `astral-sh/setup-uv` with caching, then `uv sync`, `uv run ruff check .`, `uv run pytest`.
- [ ] No secrets, no credentials, no `NOTEBOOKLM_AUTH_JSON`.
- [ ] Green on a pull request.

## 7 · Docs and closing out

- [ ] `README.md`: document `-v/--verbose`, `--only-stale` and the `notebooks` remote column.
- [ ] `AGENTS.md`: replace "There is no CI." in *Commands*; add the new flags to the command list.
- [ ] `constitution/tech-stack.md`: **CI** entry now describes the workflow; refresh the test count.
- [ ] `../001-project-scaffolding/spec.md`: point its "Out of scope" gaps bullet at this folder.
- [ ] Tick the acceptance criteria in [`spec.md`](spec.md) and set its status to implemented.
- [ ] Move 002 to **Done** in [`../../constitution/roadmap.md`](../../constitution/roadmap.md).
- [ ] `engram save` the outcome (`--type discovery` for anything the upstream CLI taught us,
      `--type decision` for design calls), always `--project notebooklm-sync`.

## Maintenance (recurring checklist)

Repeat these whenever this feature is touched again:

- [ ] `uv run pytest` and `uv run ruff check .` after any change under `src/`.
- [ ] Run the `nlm-cli-contract` agent (`model: sonnet`) after an upstream `notebooklm` version bump
      — `list --json` and `source stale --json` are now load-bearing, not just wrapped.
- [ ] If a wire shape changed, re-capture `tests/fixtures/source_list.json` and
      `tests/fixtures/notebook_list.json` from a live run and **scrub them** before committing.
- [ ] Update this plan's status and the roadmap when work lands.
