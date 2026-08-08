# AGENTS.md — notebooklm-sync

Agent-facing contract for this repo. Read this before touching anything.

`notebooklm-sync` is a CLI that keeps a NotebookLM notebook in sync with a declared set of
web sources, by driving the **already-installed `notebooklm` CLI** as a subprocess.

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

## Naming

The upstream dependency is published as **`notebooklm-py`** — which is unfortunately also the
name of this directory. They are unrelated.

- This project's distribution is `notebooklm-sync`, module `notebooklm_sync`, entry point
  `notebooklm-sync`.
- **Never** name anything here `notebooklm_py`.
- `notebooklm` in code/docs always means the upstream CLI we shell out to.

---

## Architecture rules

**All `notebooklm` invocations go through `src/notebooklm_sync/nlm.py`.** No `subprocess`
import anywhere else in `src/`. That boundary is what keeps the rest of the codebase pure and
testable offline — breaking it breaks the entire test strategy.

**The engine is split into plan then execute.** `--dry-run` runs the identical planning code
and stops before side effects. Never add a second, dry-run-only code path — it will drift from
the real one and lie to you.

**Sync never deletes.** Sources in the notebook that are absent from the manifest ("orphans")
are reported, never removed. A typo in a manifest must not be able to destroy a notebook.

---

## Upstream CLI sharp edges

Verified against `notebooklm` v0.7.3. These are counterintuitive; encode them, don't
rediscover them.

1. **`source stale` inverts its exit code** under `--exit-on-stale` (0 = stale, 1 = fresh).
   Never branch on its exit code — branch on the JSON `stale` field.
2. **`source wait` uses exit 2 for timeout**, distinct from 1 = failure. A timeout is
   retryable and means `PENDING`; a failure is not.
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
a broken test. `tests/fake_notebooklm.py` is a shim placed on `PATH` that replays canned JSON
keyed by argv. See the `test-fixtures` skill.

```bash
uv sync           # install
uv run pytest     # full suite, offline
uv run notebooklm-sync --help
```

Live verification against the real API is a **manual** step, gated on the user having run
`notebooklm login`. Never assume it works — check, and report honestly if auth is expired.

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
