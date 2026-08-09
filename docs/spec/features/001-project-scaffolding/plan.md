# 001 · Project scaffolding — Plan

**Status:** implemented and live-verified · **Last updated:** 2026-08-08

How the reconciliation tool described in [`spec.md`](spec.md) was built. A living document:
update it when the feature is touched again.

---

## Approach

`notebooklm-sync` drives the installed `notebooklm` CLI (v0.7.3, a uv tool) as a subprocess rather
than talking to NotebookLM directly — there is no public API. Four choices carry the design:

1. **A per-notebook YAML manifest is the desired state.** `.env` maps a logical notebook name to
   an ID and a manifest path. Nothing else declares intent.
2. **One subprocess seam.** Every `notebooklm` invocation goes through `nlm.py`. That single
   boundary is what lets the rest of the codebase be pure and the whole suite run offline against
   a fake binary on `PATH`.
3. **Plan, then execute.** `engine.plan()` is a pure function of (manifest, remote sources) →
   `SyncPlan`. `execute()` performs it. `--dry-run` runs the identical `plan()` and stops, so the
   preview cannot drift from the real thing.
4. **SQLite for history.** Append-only run and event tables make every run, including dry runs,
   answerable after the fact.

### The upstream surface we depend on

Verified against `/home/nestor/.local/bin/notebooklm` (v0.7.3). Nearly every subcommand takes
`--json`, and `-n/--notebook` already reads the `NOTEBOOKLM_NOTEBOOK` env var.

| Need | Command |
|---|---|
| Resolve notebooks | `notebooklm list --json` |
| Read current sources | `notebooklm source list -n <id> --json` |
| Add | `notebooklm source add <url> -n <id> --type url --json` |
| Override | `notebooklm source refresh <source_id> -n <id> --json` |
| Freshness probe | `notebooklm source stale <source_id> --json` |
| Await ingestion | `notebooklm source wait <source_id> --timeout N` |

**The `Source` wire shape.** The upstream Python model (`notebooklm/_types/sources.py:137`) names
the field `kind` with uppercase statuses, but **the JSON on the wire uses `type` and lowercase
`status`** — captured live and pinned in `tests/fixtures/source_list.json`:

```json
{"index": 1, "id": "6240829a-…", "title": "Example Domain", "type": "web_page",
 "url": "https://example.com/", "status": "ready", "status_id": 2,
 "created_at": "2026-08-08T16:24:24"}
```

`url` is the matching key. `refresh` is only valid for URL/Drive-backed kinds.

**Auth reality.** NotebookLM has no public API and no API key. Auth is Google **session cookies**
(`SID` + `__Secure-1PSIDTS`) in `~/.notebooklm/profiles/<profile>/storage_state.json`. We select a
profile with `NOTEBOOKLM_PROFILE`; no secrets live in `.env`. `NOTEBOOKLM_AUTH_JSON` exists for CI
but is a full Google session credential — deliberately unused.

---

## Implementation

```
notebooklm-py/
├── AGENTS.md                    # engram rule, sonnet rule, architecture rules, sharp edges
├── README.md
├── pyproject.toml               # uv + hatchling; script: notebooklm-sync
├── .env.example                 # committed; .env gitignored
├── docs/spec/                   # constitution + features (this document)
├── sources/example.yaml
├── src/notebooklm_sync/
│   ├── cli.py        # Typer: sync, status, notebooks, history, init
│   ├── config.py     # .env → Settings / NotebookConfig / SyncPolicy
│   ├── manifest.py   # YAML load + validate
│   ├── nlm.py        # THE subprocess boundary
│   ├── matching.py   # URL normalization + match
│   ├── engine.py     # plan() pure, execute() effectful
│   ├── db.py         # schema, migrations, audit log
│   ├── models.py
│   └── errors.py     # typed errors → exit codes
├── tests/            # 85 tests, fully offline
│   ├── fake_notebooklm.py       # fake CLI shim placed on PATH
│   └── fixtures/source_list.json
└── .claude/
    ├── agents/{nlm-cli-contract,sync-verifier}.md   # both model: sonnet
    └── skills/{notebooklm-cli,sync-engine,test-fixtures}/SKILL.md
```

Built in this order:

1. **`models.py` / `errors.py`** — the vocabulary first: `SyncPolicy`, `Action`, `Outcome`,
   `WaitStatus`, `REFRESHABLE_KINDS`, and the `SyncError` hierarchy where each class carries its
   own exit code.
2. **`config.py`** — `NOTEBOOKS` plus `NOTEBOOK_<UPPER_NAME>_ID` / `_SOURCES` / `_POLICY` into a
   frozen `Settings`. `load_dotenv(override=False)` gives precedence `os.environ` > `.env` >
   defaults; bad values raise `ConfigError` listing what was valid.
3. **`manifest.py`** — `parse_manifest()` is IO-free so it can be tested against literals;
   `load_manifest()` does the read. A bare string is URL-only shorthand.
4. **`matching.py`** — normalization and indexing. Bad remote URLs degrade to unmatched rather
   than aborting: they are upstream's data, not the user's input.
5. **`nlm.py`** — `NlmClient`, auto-appending `--json`, injecting the profile, recording each argv
   in `self.calls`, and inspecting the in-band error envelope *before* the return code.
6. **`engine.py`** — `resolve_policy()`, pure `plan()` (including orphans and the
   override→skip degradation), and `execute()` catching `NlmError` per action so one failure never
   aborts a run.
7. **`db.py`** — `PRAGMA user_version = 1`, WAL, `foreign_keys=ON`, idempotent `init_db()`, and
   the `notebooks` / `sources` / `sync_runs` / `sync_events` tables. `upsert_source` never deletes.
8. **`cli.py`** — Typer commands and Rich rendering, auth preflight with `--test` on real runs,
   a persisted run row even for `--dry-run`.
9. **Tests, docs and agent tooling** — the fake shim and fixtures, then `AGENTS.md`, `README.md`,
   three skills and two read-only agents.

Phase 1 was scoped as "skeletons with real signatures", but the core ended up **fully
implemented** — config, manifest, matching, engine, db and the adapter all work end to end.

### Configuration

`NOTEBOOKS` lists logical names; each gets `NOTEBOOK_<UPPER_NAME>_ID` and `_SOURCES`, with an
optional `_POLICY`. Policy resolution: per-source `policy:` → notebook → `SYNC_POLICY` → `skip`.

### Exit codes

`0` ok · `1` an action failed · `2` config/manifest error · `3` auth failure

---

## Decisions

Locked in with the user, or forced by what the upstream CLI actually does.

- **Per-notebook YAML manifest, mapped from `.env`** — rejected a single global source list: the
  tool is multi-notebook, and one file per notebook keeps each notebook's intent reviewable on its
  own in git.
- **`override` refreshes in place rather than delete-and-re-add** — `notebooklm source refresh
  <id>` keeps the source ID, so citations in saved notes and chat history keep resolving.
  **Confirmed live:** IDs were byte-identical before and after a refresh run. Delete-and-re-add
  would have broken every existing citation, and would have required a delete call at all.
- **Orphans are reported, never deleted** — a typo in a manifest must not be able to destroy a
  notebook. Pruning is a separate, deliberately designed feature behind an explicit flag and a
  prompt, not a default.
- **All `notebooklm` calls behind `nlm.py`** — rejected calling `subprocess` where convenient. The
  seam is what makes 85 tests run with no network and no Google auth.
- **`plan()` is pure and `--dry-run` reuses it** — rejected a dry-run-only rendering path, which
  would drift from the real one and then misreport what a real run does.
- **Auth by `NOTEBOOKLM_PROFILE` + a preflight, no secrets in `.env`** — rejected
  `NOTEBOOKLM_AUTH_JSON`, which is a full Google session credential; it stays unused until there
  is a CI reason and an explicit request.
- **Branch on the JSON `stale` field, never on `source stale`'s exit code** — that command
  *inverts* its exit code under `--exit-on-stale` (0 = stale, 1 = fresh).
- **`source wait` exit 2 means `PENDING`, not failure** — exit 2 is a timeout, distinct from 1 =
  failure. Ingestion is merely slow; the next run reconciles it, so it must not fail the run.
- **The auth preflight passes `--test`** — `auth check` without it validates locally only and
  happily reported `{"status": "ok"}` while live calls were failing with expired auth.
- **Parse the payload before the return code** — errors arrive in band on stdout as
  `{"error": true, …}`, sometimes with exit code 0.
- **Distribution named `notebooklm-sync`, module `notebooklm_sync`** — the upstream package is
  also called `notebooklm-py`, which is unfortunately this directory's name. Nothing here is ever
  named `notebooklm_py`.

---

## Risks

- **Google session cookies stale out silently.** Signing in to Google *in Chrome* does **not**
  authenticate the CLI: `storage_state.json` stayed stale while `notebooklm profile list` still
  reported "authenticated" (that status is the local-only check, which lies). *Mitigation:* the
  preflight uses `auth check --test`; diagnose by comparing the file's mtime against the login
  time and checking `token_fetch` in `auth check --test --json`. Fix with
  `notebooklm login --browser-cookies chrome --account <email>` — note it prompts to overwrite and
  aborts on a non-TTY. For long runs, `notebooklm auth refresh` every 15–20 minutes.
- **Upstream CLI drift.** We depend on argv, JSON shapes and exit-code semantics of a third-party
  tool. *Mitigation:* `tests/fixtures/source_list.json` pins the real wire shape, unknown fields
  are ignored rather than rejected, and the `nlm-cli-contract` agent diffs the installed CLI
  against our assumptions after a version bump.
- **A manifest typo could orphan real sources.** *Mitigation:* the never-delete invariant — the
  worst case is a noisy orphan report, never data loss.
- **Errors that look like success.** In-band `{"error": true}` payloads with exit code 0 would be
  read as success by any naive wrapper. *Mitigation:* `_payload()` inspects the body first, and
  auth-shaped messages are promoted to `AuthError` with the `notebooklm login` hint.

---

## Verification

**Offline** — `uv sync` ✓ · `uv run pytest` → **85 passed** ✓ · `ruff check` clean ✓ ·
all 5 subcommands ✓ · `init` in a clean dir ✓ · subprocess confined to `nlm.py` ✓

Against the fake shim: dry-run issued only a read-only `source list` and wrote `dry_run=1`;
auth failure → exit 3 with the login hint; `wait` exit 2 → `pending`, exit 0.

**Live** — against a throwaway notebook (`3fb8dfb7-f168-4b35-a662-dcfdec05433d`,
"notebooklm-sync smoke test (safe to delete)"):

| Check | Result |
|---|---|
| Dry-run on empty notebook | 2 add planned, nothing written ✓ |
| Real sync | Both sources added, `status: ready` ✓ |
| Confirmed via upstream `source list` | 2 sources, correct URLs/titles ✓ |
| Re-run under `skip` | 0 add · 2 skip — idempotent ✓ |
| `--policy override` | 2 refresh, **source IDs unchanged** ✓ |
| Orphan (shrunk manifest) | Reported, still in the notebook ✓ |
| `history` | All 5 runs recorded, dry-run flagged ✓ |

**Cleanup done (2026-08-09):** the throwaway notebook was deleted with
`notebooklm delete -n 3fb8dfb7-f168-4b35-a662-dcfdec05433d -y --json` and confirmed absent from
`notebooklm list`. See [`tasks.md`](tasks.md) for the argv quirk it exposed.

### Commits

- `465c1f6` project scaffolding
- `5302313` test: pin real upstream wire shape

---

The loose ends this feature left behind are tracked in
[`../../constitution/roadmap.md`](../../constitution/roadmap.md) — concrete gaps as entry 002,
deferred ideas in the backlog. There is no separate backlog in this document.
