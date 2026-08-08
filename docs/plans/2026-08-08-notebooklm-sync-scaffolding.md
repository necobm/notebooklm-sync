# notebooklm-sync — Scaffolding Plan

**Status:** Phase 1 complete and live-verified · **Last updated:** 2026-08-08

A living document. Phase 1 (scaffolding) is done; the "Pending" section at the end is
the backlog for Phase 2.

---

## Context

`notebooklm-sync` is a Python CLI that keeps a NotebookLM notebook in sync with a declared
set of web sources, driving the installed `notebooklm` CLI (v0.7.3, a uv tool) as a
subprocess. Before it, this was manual: open NotebookLM, paste URLs, and keep no record of
what was added when or whether a source went stale. The tool makes the desired source set
**declarative** (a per-notebook YAML manifest), reconciliation **repeatable** (a policy
decides what happens on a match), and every run **auditable** (SQLite history).

The project lives in `/home/nestor/Apps/lab/notebooklm-py` — which was empty at the start
and, confusingly, shares a name with the unrelated upstream package.

### Decisions locked in with the user
- **Source list**: per-notebook YAML manifest; `.env` maps notebook name → manifest path.
- **Auth**: `NOTEBOOKLM_PROFILE` + preflight; no secrets in `.env`.
- **`override` policy**: `notebooklm source refresh <id>` in place, keeping the source ID
  so citations in notes and chats survive. **Confirmed live** — IDs were byte-identical
  before and after a refresh run.
- **Orphans** (in notebook, not in manifest): never deleted, reported only.
- **Memory**: engram, always `--project notebooklm-sync`. Read before starting work,
  write after finishing.
- **Subagents**: always spawned with `model: sonnet`.

---

## Findings that shape the design

Verified against the installed CLI at `/home/nestor/.local/bin/notebooklm` (v0.7.3).

**The dependency is a good citizen.** Nearly every subcommand takes `--json`, and
`-n/--notebook` already reads the `NOTEBOOKLM_NOTEBOOK` env var.

| Need | Command |
|---|---|
| Resolve notebooks | `notebooklm list --json` |
| Read current sources | `notebooklm source list -n <id> --json` |
| Add | `notebooklm source add <url> -n <id> --type url --json` |
| Override | `notebooklm source refresh <source_id> -n <id> --json` |
| Freshness probe | `notebooklm source stale <source_id> --json` |
| Await ingestion | `notebooklm source wait <source_id> --timeout N` |

**The `Source` wire shape.** The Python model (`notebooklm/_types/sources.py:137`) names the
field `kind` with uppercase statuses, but **the JSON on the wire uses `type` and lowercase
`status`** — captured live and pinned in `tests/fixtures/source_list.json`:

```json
{"index": 1, "id": "6240829a-…", "title": "Example Domain", "type": "web_page",
 "url": "https://example.com/", "status": "ready", "status_id": 2,
 "created_at": "2026-08-08T16:24:24"}
```

`url` is the matching key. `refresh` is only valid for URL/Drive-backed kinds.

**Four sharp edges, encoded in the adapter rather than rediscovered:**
1. `source stale` inverts its exit code under `--exit-on-stale` (0 = stale). Branch on the
   JSON `stale` field, never the exit code.
2. `source wait` uses exit **2 = timeout**, distinct from 1 = failure. A timeout means
   *still processing* → `PENDING`, not a failure.
3. `auth check` without `--test` is local-only and reported `{"status": "ok"}` while live
   calls were failing. The preflight must pass `--test`.
4. Errors arrive **in band** on stdout as `{"error": true, …}`, sometimes with exit code 0.

**Auth reality.** NotebookLM has no public API and no API key. Auth is Google **session
cookies** (`SID` + `__Secure-1PSIDTS`) in `~/.notebooklm/profiles/<profile>/storage_state.json`.
`NOTEBOOKLM_AUTH_JSON` exists for CI but is a full Google session credential — deliberately
unused.

> **Gotcha that cost real time:** signing in to Google *in Chrome* does **not** authenticate
> the CLI. `storage_state.json` stayed stale while `notebooklm profile list` still reported
> "authenticated" (that status is the local-only check, which lies). Diagnose by comparing
> the file's mtime against the login time and checking `token_fetch` in
> `auth check --test --json`. Fix: `notebooklm login --browser-cookies chrome --account <email>`
> — note it prompts to overwrite and aborts on a non-TTY.

**Naming collision.** The upstream package is also `notebooklm-py`. This project's
distribution is `notebooklm-sync`, module `notebooklm_sync` — never `notebooklm_py`.

---

## What was built

```
notebooklm-py/
├── AGENTS.md                    # engram rule, sonnet rule, architecture rules, sharp edges
├── README.md
├── pyproject.toml               # uv + hatchling; script: notebooklm-sync
├── .env.example                 # committed; .env gitignored
├── docs/plans/                  # tracked
├── docs/sessions/               # gitignored globally
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

Phase 1 was scoped as "skeletons with real signatures", but the core ended up **fully
implemented** — config, manifest, matching, engine, db, and the adapter all work end to end.

### Key invariants
- **All `notebooklm` calls go through `nlm.py`.** No other module in `src/` imports
  `subprocess`. That single seam is what makes the suite testable offline.
- **`plan()` is pure; `--dry-run` reuses it.** There is no dry-run-only code path.
- **Sync never deletes.** Orphans are reported, never removed.

### Configuration
`NOTEBOOKS` lists logical names; each gets `NOTEBOOK_<UPPER_NAME>_ID` and `_SOURCES`, with
an optional `_POLICY`. Policy resolution: per-source `policy:` → notebook → `SYNC_POLICY` →
`skip`. Precedence is `os.environ` > `.env` > defaults.

### Exit codes
`0` ok · `1` an action failed · `2` config/manifest error · `3` auth failure

---

## Verification results

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

**Cleanup still owed:** the throwaway notebook was left in place — delete with
`notebooklm delete 3fb8dfb7 -y`.

### Commits
- `465c1f6` project scaffolding
- `5302313` test: pin real upstream wire shape

---

## Pending

### Gaps in what exists
- **`sources` mirror stores `kind=None`.** `cli.py` passes `kind=None` to `upsert_source`,
  so the local mirror never records the source type even though we have it. Small fix,
  meaningful for `status` reporting.
- **`nlm.list_notebooks()` is unused.** `notebooklm-sync notebooks` reads only `.env`, so it
  can't show live titles or flag a `NOTEBOOK_*_ID` that no longer exists. Wiring it in
  would make the command a real health check.
- **`nlm.is_stale()` is unused.** Nothing consults `source stale`. A `--only-stale` mode for
  `override` would refresh just the sources that actually need it, instead of all of them.
- **No CLI-level tests.** Command wiring was verified by hand through a shell harness;
  Typer's `CliRunner` would cover exit codes and flag plumbing in the suite itself.
- **No `-v/--verbose`.** Adapter argv is captured in `NlmClient.calls` but never surfaced.

### Deferred features
- **RSS/sitemap expansion** — let a manifest name a feed and expand it to article URLs, so a
  notebook grows as a source publishes. Needs `feedparser` and a fetch step.
- **Orphan pruning** — one-way mirroring behind an explicit `--prune` flag *and* a
  confirmation prompt. Deliberately absent; do not add casually.
- **Unattended/scheduled runs** — a systemd timer plus `notebooklm auth refresh` as a
  15–20 min cookie keepalive, since sessions stale out.
- **CI** — nothing runs the suite automatically. It is fully offline, so a plain GitHub
  Actions job would work with no secrets.

### Housekeeping
- Delete the throwaway smoke-test notebook (above).
- No remote configured; `main` is the working branch.
