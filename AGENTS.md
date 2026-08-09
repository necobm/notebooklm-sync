# AGENTS.md — notebooklm-sync

Agent-facing contract for this repo. Read this before touching anything.

`notebooklm-sync` is a CLI that keeps a NotebookLM notebook in sync with a declared set of
web sources, by driving the **already-installed `notebooklm` CLI** as a subprocess.

> **This file is the master document.** Everything an agent needs to work here — commands,
> architecture, invariants, upstream quirks, auth, testing, memory — lives in this file and
> nowhere else. `CLAUDE.md` contains only a pointer to it and must never accumulate content of
> its own. When you learn something durable about how this repo works, add it **here** (and to
> engram); do not start a parallel doc, and do not copy a section of this file into another one.
> Two copies of a rule means one of them is already wrong.

---

## Memory (engram) — required

This project uses **engram** as persistent memory. It is already installed and configured on
this machine (`/usr/bin/engram`). This is not optional and not a fallback — it is the first
thing you do and the last thing you do.

**Always pass `--project notebooklm-sync`.** Never rely on engram's cwd detection: this
directory is named `notebooklm-py`, so auto-detection would misfile memories under a project
name that collides with the unrelated upstream package (see *Naming* below).

### Before starting anything — read memory first

```bash
engram context notebooklm-sync
engram search "<topic of the task>" --project notebooklm-sync
```

Do this **before** planning or writing code, not after you are already deep in. If memory
contradicts what the code actually says, trust the code and correct the memory.

### After finishing anything meaningful — write memory back

```bash
engram save "<short title>" "<what / why / how to apply>" \
  --type <architecture|discovery|bugfix|decision> \
  --project notebooklm-sync
```

**Record:** design decisions and their rationale, upstream `notebooklm` CLI quirks discovered
the hard way, schema/migration changes, and anything that cost real debugging time.

**Do not record:** what the code already says, routine edits, or secrets — never store cookie
or `storage_state` contents, and never store `.env` values.

Scope every entry to this project only.

---

## Docs layout — spec driven development

This project works spec-first: spec, then plan, then tasks, then code.

- **`AGENTS.md`** (this file) — the master agent doc. See the note at the top.
- **`CLAUDE.md`** — a reference to this file. Nothing else goes in it.
- **`docs/spec/`** — **the source of truth for what we build and why**, tracked in git.
  - `constitution/` — the stable rules: `mission.md` (what we build and for whom),
    `tech-stack.md` (technologies, domain mechanics, conventions, hard limits) and `roadmap.md`
    (feature order and status). **Read these before planning anything.**
  - `features/NNN-feature-name/` — one folder per feature: `spec.md` (behaviour + acceptance
    criteria), `plan.md` (technical approach) and `tasks.md` (checklist). Write them before
    touching code, and treat them as living documents — when work lands, update status and
    checkboxes rather than leaving them frozen at the moment they were approved. Finish by
    moving the feature to "Done" in `roadmap.md`.
  - **The constitution outranks a feature plan.** If a feature conflicts with `mission.md` or
    `tech-stack.md`, rework the feature, not the constitution.
- **`docs/sessions/`** — session notes and scratch write-ups. **Gitignored** via
  `/home/nestor/.gitignore_global`, so it is safe for working notes but never a place to put
  something that must survive for other people. Anything durable belongs in `docs/spec/`,
  this file, a skill, or engram.

## Naming

The upstream dependency is published as **`notebooklm-py`** — which is unfortunately also the
name of this directory. They are unrelated.

- This project's distribution is `notebooklm-sync`, module `notebooklm_sync`, entry point
  `notebooklm-sync`.
- **Never** name anything here `notebooklm_py`.
- `notebooklm` in code/docs always means the upstream CLI we shell out to.

---

## Commands

```bash
uv sync                                  # install (incl. dev group)
uv run pytest                            # full suite — offline, no auth, no network
uv run pytest tests/test_engine.py       # one file
uv run pytest -k wait_exit_two           # one test by name
uv run ruff check .                      # lint (line-length 100) — currently clean
uv run notebooklm-sync --help
uv run notebooklm-sync sync research --dry-run        # plan only, no side effects
uv run notebooklm-sync sync research -p override --only-stale   # refresh just what's stale
uv run notebooklm-sync sync research -v               # echo every notebooklm argv + HTTP GET
uv run notebooklm-sync expand research                # what do this manifest's /* rules match?
uv run notebooklm-sync sync research --refresh-discovery   # re-read sites, ignore the URL cache
```

CI (`.github/workflows/ci.yml`) runs `ruff check` and `pytest` on push and pull request against
Python 3.11 and 3.12, with **no secrets** — the suite is fully offline, so anything failing locally
is a real failure, and a test that needs a credential to pass in CI is a broken test.

`ruff check` passes; the code is **not** `ruff format`-clean and isn't meant to be — don't run
`ruff format` over the repo, it would reflow ~11 files into an unrelated diff.

---

## Architecture

One CLI that reconciles a **declared** YAML source manifest against what a NotebookLM notebook
**actually has**. Every run is recorded in SQLite so drift and history are inspectable.

The data flow through `src/notebooklm_sync/`, in order:

```
config.py     .env  →  Settings{notebooks, policy, timeouts, db_path, discovery}
manifest.py   YAML  →  list[ManifestEntry]          (validates + dedupes)
crawl.py      "https://site.com/*[except=blog]"  →  CrawlRule            ← PURE
discovery.py  CrawlRule  →  URLs, via sitemap or crawl  →  plain entries ← THE HTTP SEAM
nlm.py        notebooklm source list --json  →  list[RemoteSource]
engine.plan()  (entries, remote, policy) →  SyncPlan[PlannedAction]     ← PURE
engine.execute()  PlannedAction  →  Outcome, via nlm.py                 ← side effects
db.py         plan/outcomes  →  sync_runs + sync_events + sources mirror
cli.py        Typer commands: sync · status · expand · notebooks · history · init
```

### The four invariants

These carry the whole design. Each has a reason that is not obvious from the code, and breaking
any one of them breaks something far away from where you edited.

**All `notebooklm` invocations go through `nlm.py`.** No `subprocess` import anywhere else in
`src/`. That single seam is what lets `tests/fake_notebooklm.py` impersonate the upstream binary
on `PATH` and keep the entire suite offline. Breaking the boundary breaks the test strategy, not
just one test.

**All HTTP goes through `discovery.py`.** No `urllib.request`, `http.client` or `socket` anywhere
else in `src/` (`urllib.parse` is pure string work and is fine). Same argument as `nlm.py`: `fetch`
is injectable, so `tests/conftest.py`'s `fake_http` serves an entire website without a socket. This
is the newest seam and the easiest to erode — resist fetching "just this once" from elsewhere.

**`engine.plan()` is pure; `engine.execute()` mutates.** `--dry-run` and `status` run the
*identical* `plan()` and stop before side effects. Never add a second, dry-run-only code path —
it will drift from the real one and then lie to you about what a real run would do. Crawl rules
are resolved *before* `plan()`, in `cli._expand()`, which is precisely what keeps this true —
`engine.py` needed no change at all for that feature.

**Sync never deletes.** Sources in the notebook that are absent from the manifest ("orphans")
are reported, never removed — a typo in a manifest must not be able to destroy a notebook. This
holds in `db.py` too: `sources` is a mirror of believed notebook state, and `sync_runs` /
`sync_events` are append-only history. Schema is `SCHEMA_VERSION` / `PRAGMA user_version`,
applied idempotently in `init_db()`; future migrations branch on `current` before stamping.

### Things that will bite you

**The upstream wire shape is not the upstream Python model.** `source list --json` sends `type`
(not `kind`) and lowercase `status` (`"ready"`, not `"READY"`), plus extra fields.
`source_from_payload()` in `nlm.py` is the bridge — `payload.get("kind") or payload.get("type")`.
`tests/test_live_shape.py` pins this against a real captured payload
(`tests/fixtures/source_list.json`); if you change source parsing, that file is the guard that
catches it.

**Normalized URLs are for comparison only.** `matching.normalize_url()` strips `www.`, tracking
params, trailing slashes and default ports, and collapses every YouTube shape onto
`https://youtube.com/watch?v=<id>`. `notebooklm source add` always receives the user's
**original** URL. The normalized form only ever lives in `normalized_url` fields and the DB
mirror. `normalize_url()` also raises `ManifestError` on non-http(s), which is why manifest
validation gets URL checking for free via `dedupe_entries()`.

**`override` degrades, it doesn't fail.** Only kinds in `models.REFRESHABLE_KINDS` can be
refreshed; anything else (pasted text, uploads) becomes a `SKIP` with a reason, so one odd source
can't block the notebook. Refresh is deliberately in-place rather than delete-and-re-add — the
source keeps its ID, so citations in saved notes and chats keep resolving.

**Crawl-rule `level` is *relative* depth, and the cap truncates rather than fails.** Under
`https://site.com/*`, `/docs` is level 1 and `/docs/intro` is level 2; under
`https://site.com/docs/*`, `/docs/intro` is level 1. `except=blog` matches on segment boundaries,
so it removes `/blog` and `/blog/x` but never `/blogging`. Over `max`, the shallowest N are kept
and a warning names both counts — an inline `[max=N]` **always** overrides `SYNC_DISCOVERY_MAX`,
in both directions, including downwards. The discovery cache is keyed by base URL + `level` +
`max` and deliberately **not** by `except`, so tightening an exclusion re-uses the cache.

**Errors are typed and map to exit codes** (`errors.py`): `1` action failed, or a rule that
resolved to nothing (`DiscoveryError`) · `2` config/manifest (`ConfigError`, `ManifestError`,
including every malformed crawl rule) · `3` auth (`AuthError`). `cli.py` catches `SyncError` at
each command boundary and exits with `exc.exit_code` — new failure modes should get a `SyncError`
subclass, not an ad-hoc `typer.Exit`.

### Configuration shape

`.env` declares `NOTEBOOKS=research,marketing`, then per name `NOTEBOOK_<UPPER_NAME>_ID`,
`_SOURCES` (manifest path) and optional `_POLICY`. `config._env_key()` does the name→key
mangling. Precedence is `os.environ` > `.env` > defaults (`load_dotenv(override=False)`), so CI
can override anything without editing files. Policy resolution, first match wins: manifest entry
`policy:` → `NOTEBOOK_<NAME>_POLICY` → `SYNC_POLICY` → `skip`.

---

## Upstream CLI sharp edges

Verified against `notebooklm` v0.7.3. These are counterintuitive; encode them, don't
rediscover them.

1. **`source stale` inverts its exit code** under `--exit-on-stale` (0 = stale, 1 = fresh).
   Never branch on its exit code — branch on the JSON `stale` field.
2. **`source wait` uses exit 2 for timeout**, distinct from 1 = failure. A timeout means the
   source is still ingesting: it maps to `Outcome.PENDING`, the next run reconciles it, and the
   run's exit code stays 0. A failure is none of those. Collapsing 2 into 1 turns a slow ingest
   into a spurious hard failure.
3. **`auth check` validates locally only** and will happily report `{"status": "ok"}` while
   live API calls fail with expired auth. Any real preflight must pass `--test` to force a
   network round-trip.
4. **Errors arrive in-band on stdout** as `{"error": true, "code": ..., "message": ...}`,
   sometimes with exit code 0. Parse the payload; do not trust the exit code alone.

## Auth model

NotebookLM has **no public API and no API key**. The upstream CLI authenticates with Google
**session cookies** (`SID` + `__Secure-1PSIDTS`) stored in
`~/.notebooklm/profiles/<profile>/storage_state.json`.

- We select a profile with `NOTEBOOKLM_PROFILE` in `.env`. No secrets live in `.env`.
- Setup is a one-time interactive `notebooklm login` (opens a browser). If you need it,
  **ask the user to run it** — you cannot complete a browser login yourself.
- Cookies stale out. `notebooklm auth refresh` is the keepalive (15–20 min cadence).
- `NOTEBOOKLM_AUTH_JSON` (inline storage-state JSON) exists for CI, but it is a full Google
  session credential. Do not introduce it without the user explicitly asking.

---

## Testing

**Tests never hit the network and never need auth.** A test that requires live credentials is
a broken test — the cookies expire, so a suite depending on them fails randomly on someone
else's machine.

`tests/conftest.py` provides the fixtures:

- `fake_cli` — writes an executable `notebooklm` shim into a tmp dir and prepends it to `PATH`,
  so `nlm.py`'s subprocess resolves to `tests/fake_notebooklm.py`. It records every argv and
  replays canned JSON keyed by subcommand (`"source list"`, `"auth check"`). Scenario values are
  either a bare response object or `{"stdout": ..., "exit": N}` when the exit code matters.
- `fake_http` — the network counterpart: a mapping of URL → canned response, passed to
  `Discoverer(fetch=…)` (or patched over `discovery.urllib_fetch` for CLI-level tests, which keeps
  the flags and the cache under test). An unregistered URL returns 404, so a forgotten stub fails
  loudly instead of quietly reaching the real internet. Helpers: `add`, `add_sitemap`,
  `add_sitemap_index`; `.requests` is the log.
- `clean_env` — strips inherited `NOTEBOOK*` / `SYNC_*` so a test sees only what it sets.
- `db_path` — a tmp database, never the repo's `./notebooklm-sync.db`.

See the `test-fixtures` skill for the scenario recipes that reproduce each sharp edge above.

Live verification against the real API is a **manual** step, gated on the user having run
`notebooklm login`. Never assume it works — check with `notebooklm auth check --test`, and report
honestly if auth is expired.

---

## Subagents

**Spawn subagents with Sonnet.** Pass `model: sonnet` on every `Agent` call in this project.
The repo's own agent definitions also pin `model: sonnet` in their frontmatter, so they stay
on Sonnet even when invoked directly — but set it explicitly on ad-hoc spawns too, since those
otherwise inherit the parent's model.

- `.claude/agents/nlm-cli-contract.md` — read-only drift check of `nlm.py` against the
  installed upstream CLI.
- `.claude/agents/sync-verifier.md` — offline dry-run + DB assertions against fixtures.

## Skills

Load these rather than re-deriving their content:

- `.claude/skills/notebooklm-cli/` — upstream CLI surface, JSON shapes, exit codes, auth.
- `.claude/skills/sync-engine/` — policies, URL matching, orphans, exit codes.
- `.claude/skills/test-fixtures/` — how to test offline with the fake shim.
