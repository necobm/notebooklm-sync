# 001 · Project scaffolding — Tasks

Derived from [`plan.md`](plan.md). Everything below shipped in commits `465c1f6` and `5302313`.

## Scaffolding and packaging

- [x] `pyproject.toml`: uv + hatchling, `src/` layout, `notebooklm-sync` entry point, ruff and
      pytest config.
- [x] `.gitignore` covering `.env*`, `*.db`, and the usual Python artefacts.
- [x] `.env.example` documenting `NOTEBOOKS`, per-notebook keys and sync behaviour.
- [x] `sources/example.yaml` as a runnable example manifest.

## Core modules

- [x] `models.py` — `SyncPolicy`, `Action`, `Outcome`, `WaitStatus`, `REFRESHABLE_KINDS` and the
      dataclasses.
- [x] `errors.py` — `SyncError` hierarchy with exit codes 0/1/2/3.
- [x] `config.py` — `.env` + environment → frozen `Settings`, with precedence and validation.
- [x] `manifest.py` — YAML load and validation, IO-free `parse_manifest()`, string shorthand.
- [x] `matching.py` — URL normalization rules, YouTube collapse, source indexing, entry dedupe.
- [x] `nlm.py` — `NlmClient` as the only subprocess seam: `--json`, profile injection, in-band
      error envelope, auth promotion, `wait` exit 0/1/2 mapping.
- [x] `engine.py` — `resolve_policy()`, pure `plan()`, `execute()` with per-action error capture.
- [x] `db.py` — schema v1, WAL, `notebooks` / `sources` / `sync_runs` / `sync_events`, never
      deleting rows.
- [x] `cli.py` — `sync`, `status`, `notebooks`, `history`, `init`; auth preflight with `--test`;
      dry runs persisted with `dry_run=1`.

## Tests

- [x] `tests/fake_notebooklm.py` — fake `notebooklm` binary replaying canned JSON keyed by argv.
- [x] `tests/conftest.py` — `fake_cli`, `clean_env`, `db_path` fixtures.
- [x] Unit tests per module: matching, manifest, config, engine, db, nlm.
- [x] `tests/fixtures/source_list.json` — scrubbed live capture, plus `test_live_shape.py` pinning
      `type`-not-`kind` and lowercase status.
- [x] Whole suite green offline: **85 passed**, no network, no Google auth.

## Docs and agent tooling

- [x] `README.md` — install, setup, usage, policy table, exit codes.
- [x] `AGENTS.md` — memory rule, naming, architecture rules, upstream sharp edges, auth model,
      testing, subagent policy.
- [x] Skills: `notebooklm-cli`, `sync-engine`, `test-fixtures`.
- [x] Agents: `nlm-cli-contract`, `sync-verifier` — both read-only, both `model: sonnet`.

## Verification

- [x] Offline: `uv sync`, `uv run pytest`, `ruff check`, all five subcommands, `init` in a clean
      directory, `subprocess` confined to `nlm.py`.
- [x] Offline against the shim: dry run is read-only, auth failure exits 3 with the login hint,
      `wait` timeout reports `pending`.
- [x] Live against a throwaway notebook: dry run, add, idempotent re-run, `override` with
      unchanged source IDs, orphan reporting, history.
- [x] Validated against the acceptance criteria in [`spec.md`](spec.md).
- [x] Moved to "Done" in [`../../constitution/roadmap.md`](../../constitution/roadmap.md).

## Outstanding

- [ ] Delete the throwaway smoke-test notebook: `notebooklm delete 3fb8dfb7 -y`.

## Maintenance (recurring checklist)

Repeat these whenever this feature is touched again:

- [ ] `uv run pytest` and `uv run ruff check` after any change under `src/`.
- [ ] Run the `nlm-cli-contract` agent after an upstream `notebooklm` version bump, to diff the
      installed CLI against the assumptions in `nlm.py`.
- [ ] If the wire shape changed, re-capture `tests/fixtures/source_list.json` from a live run and
      **scrub it** before committing.
- [ ] Update this plan's status and the roadmap when work lands.
