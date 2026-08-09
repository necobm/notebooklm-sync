# Tech stack and conventions

The technical reference for this project. No feature plan should contradict it.

## Technologies

- **Language:** Python `>=3.11`, fully type-annotated, `from __future__ import annotations`
  everywhere. The local virtualenv runs 3.12.
- **Packaging / env:** [uv](https://docs.astral.sh/uv/) with `uv.lock`, built by hatchling from a
  `src/` layout. Console entry point `notebooklm-sync = notebooklm_sync.cli:app`.
- **CLI:** [Typer](https://typer.tiangolo.com/) for commands and flags, [Rich](https://rich.readthedocs.io/)
  for tables and coloured summaries.
- **Config:** `python-dotenv` reading `.env`, with precedence `os.environ` > `.env` > defaults.
- **Manifests:** PyYAML.
- **Database:** stdlib `sqlite3` — WAL journal mode, `foreign_keys=ON`, schema version stamped in
  `PRAGMA user_version` (currently `1`).
- **Upstream dependency:** the `notebooklm` CLI (upstream package `notebooklm-py`, v0.7.3),
  invoked as a **subprocess**. Not a library dependency — it is expected to be installed on the
  machine (a uv tool).
- **Tests:** pytest, 117 tests, **fully offline** — no network, no Google auth.
- **Lint:** ruff, `line-length = 100`, default rule set.
- **Type checking:** none configured yet, despite the code being fully annotated. See the roadmap.
- **CI:** GitHub Actions (`.github/workflows/ci.yml`) — `uv sync`, `ruff check`, `pytest` on push
  and pull request, against Python 3.11 and 3.12. **No secrets**; a test that needs one is broken.
- **Deployment:** none — a local tool, installed and run from the checkout.

## Key files / modules

Everything under `src/notebooklm_sync/`:

- `cli.py` — Typer app and Rich rendering. Commands: `sync`, `status`, `notebooks`, `history`,
  `init`. All errors funnel through one handler that maps a typed error to its exit code — except
  `notebooks`, which degrades (see *Domain model*).
- `config.py` — `.env` + environment → a frozen `Settings`. Parses `NOTEBOOKS` plus
  `NOTEBOOK_<KEY>_ID` / `_SOURCES` / `_POLICY` (non-alphanumeric chars become `_`, uppercased).
- `manifest.py` — YAML load and validation. `parse_manifest()` is IO-free (testable against
  literals); `load_manifest()` does the file read. Accepts a bare string as URL-only shorthand.
- `matching.py` — URL normalization and indexing. The identity layer: `normalize_url()`,
  `normalize_entry()`, `normalize_source()`, `index_sources()`, `dedupe_entries()`.
- `nlm.py` — **the single subprocess boundary.** `NlmClient` wraps every `notebooklm` invocation,
  appends `--json`, injects the profile, and records each argv in `self.calls`.
- `engine.py` — reconciliation. `plan()` is pure (manifest + remote sources → `SyncPlan`);
  `apply_stale_filter()` is a read-only probe step used by `--only-stale`; `execute()` performs the
  actions and catches per-action failures.
- `db.py` — SQLite schema and persistence: `notebooks`, `sources`, and the append-only
  `sync_runs` / `sync_events` audit tables.
- `models.py` — dataclasses and enums shared across the package (see *Domain model* below).
- `errors.py` — `SyncError` hierarchy (`ConfigError`, `ManifestError`, `AuthError`, `NlmError`,
  `NlmTimeout`), each carrying its exit code.

Elsewhere:

- `tests/fake_notebooklm.py` — the fake `notebooklm` binary placed on `PATH`; replays canned JSON
  keyed by argv and logs every invocation.
- `tests/conftest.py` — the `fake_cli`, `clean_env` and `db_path` fixtures.
- `tests/fixtures/source_list.json` — a scrubbed live capture of `source list --json`, pinning the
  real wire shape; `tests/fixtures/notebook_list.json` does the same for `list --json` (real shape,
  placeholder ids and titles — the live ones are the user's private notebooks).
- `tests/test_cli.py` — the Typer layer through `CliRunner`; the only place the exit-code contract
  is asserted end to end.
- `.env.example` — committed template; `.env` itself is gitignored.
- `sources/example.yaml` — example manifest, validated by the test suite.
- `.claude/skills/` — `notebooklm-cli`, `sync-engine`, `test-fixtures`.
- `.claude/agents/` — `nlm-cli-contract` (upstream drift check), `sync-verifier` (offline
  end-to-end assertions). Both read-only.

## Commands

- `uv sync` — install dependencies and the project.
- `uv run pytest` — run the full suite, offline.
- `uv run ruff check` — lint.
- `uv run notebooklm-sync --help` — smoke-check the CLI wiring.

There is no build or deploy step.

## Domain model

Only the non-obvious mechanics; the rest is readable in `models.py`.

- **`SyncPolicy`** — `skip` (default, safest) · `override` (refresh in place) · `create`
  (add a deliberate duplicate). Resolution order, first match wins: per-source `policy:` in the
  manifest entry → `NOTEBOOK_<NAME>_POLICY` → `SYNC_POLICY` → `skip`.
- **Decision table** — no match → `ADD` · match + `skip` → `SKIP` · match + `override` →
  `REFRESH` · match + `override` but kind not refreshable → `SKIP` with a warning · match +
  `create` → `ADD` · in the notebook but not in the manifest → `ORPHAN`.
- **`REFRESHABLE_KINDS`** — `source refresh` only works on URL- and Drive-backed kinds. Anything
  else (pasted text, uploaded files) makes `override` degrade to a skip rather than error.
- **`--only-stale` narrows `override`, and fails open.** `engine.apply_stale_filter()` probes
  `source stale` (a read-only call, so it runs under `--dry-run` too — one code path, both modes)
  and rewrites REFRESH → SKIP for fresh sources. An unusable answer or a failed probe leaves the
  refresh in place: a wasted refresh beats a notebook quietly serving stale content.
- **`notebooks` degrades instead of exiting.** It checks every configured ID against
  `notebooklm list`, but an unreachable upstream prints a warning, renders the remote column as `?`
  and still exits 0 — it is the command you run *because* auth broke.
- **`Outcome.PENDING` is not a failure.** `source wait` exits 2 on timeout, meaning ingestion is
  merely slow; the next run reconciles it and the run still exits 0.
- **URL normalization is comparison-only.** `normalize_url()` strips whitespace, rejects non-http(s)
  schemes, lowercases scheme and host (never the path), drops `www.`, drops a default port, drops
  tracking params (`utm_*`, `fbclid`, `gclid`, `mc_eid`, `msclkid`, `ref`, `ref_src`), drops the
  fragment, strips a trailing slash, sorts the query, and collapses every YouTube shape
  (`youtu.be`, `/shorts`, `/embed`, `/v`, `/live`, `m.` and `music.` hosts) onto
  `https://youtube.com/watch?v=<id>`. **The original URL is always what gets sent to `source add`.**
- **Duplicates are reported, not fatal.** Manifest entries are deduped by normalized URL with a
  warning; on the notebook side the first match wins and the rest are listed.
- **Wire shape ≠ Python model.** The upstream JSON uses `type` (not `kind`) and lowercase
  `status`. `source_from_payload()` reads `kind or type`; `tests/fixtures/source_list.json` pins
  this against a real capture, and unknown future fields are ignored rather than rejected.

## Conventions

- Frozen dataclasses for value objects (`ManifestEntry`, `RemoteSource`, `NotebookConfig`,
  `Settings`); mutable ones only where a result is accumulated (`PlannedAction`, `SyncPlan`).
- Library code raises `SyncError` subclasses carrying `exit_code`; it never calls `sys.exit`.
  Only `cli.py` turns an error into an exit.
- **Parse the payload before the return code.** Upstream reports errors in band on stdout as
  `{"error": true, ...}`, sometimes with exit code 0.
- Tests mirror modules: `tests/test_<module>.py`. Keep `plan()` tests pure — no shim needed.
  Anything touching the adapter goes through the `fake_cli` fixture and asserts on the argv it
  received. SQLite tests use a tmp-path database, never `./notebooklm-sync.db`.
- Fixtures are captured from real runs and **scrubbed before committing** — never from
  `auth check` or `storage_state.json`.
- Code, comments and docs are written in English.
- **Memory (engram) is required.** Read before planning
  (`engram context notebooklm-sync`), write after finishing anything meaningful
  (`engram save ... --type <architecture|discovery|bugfix|decision>`). Always pass
  `--project notebooklm-sync` — cwd detection would misfile it under `notebooklm-py`.
- **Subagents are pinned to Sonnet.** Pass `model: sonnet` on every ad-hoc `Agent` call; the
  repo's own agent definitions pin it in frontmatter.

## Visual style

Not applicable — there is no UI beyond Rich tables in the terminal.

## Hard limits

- **No `subprocess` import anywhere in `src/` except `nlm.py`.** That single seam is what makes
  the suite testable offline; breaking it breaks the whole test strategy.
- **No second, dry-run-only code path.** `--dry-run` runs the identical `plan()` and stops before
  side effects. A parallel path drifts and then lies about what a real run would do.
- **Never delete a source.** Do not call `source delete`, `source delete-by-title` or
  `source clean`. Pruning, if it ever ships, needs an explicit opt-in flag *and* a confirmation
  prompt, designed deliberately.
- **No test may require network access or Google auth.** A test that needs live credentials is a
  broken test.
- **Never commit `.env`, cookies, `storage_state.json`, or raw `auth check` output.** Never store
  any of it in engram either.
- **Do not introduce `NOTEBOOKLM_AUTH_JSON`** without the user explicitly asking — it is a full
  Google session credential.
- **Never name anything `notebooklm_py`.** The distribution is `notebooklm-sync`, the module is
  `notebooklm_sync`; a bare `notebooklm` always means the upstream CLI.
