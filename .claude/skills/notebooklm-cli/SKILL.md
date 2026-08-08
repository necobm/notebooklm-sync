---
name: notebooklm-cli
description: Reference for driving the installed upstream `notebooklm` CLI as a subprocess — commands, --json shapes, exit-code semantics, the Source model, and the Google cookie auth model. Use when calling, wrapping, or debugging any `notebooklm` command, when touching nlm.py, or when a notebooklm call returns something unexpected.
---

# Upstream `notebooklm` CLI

Written against **v0.7.3** (`/home/nestor/.local/bin/notebooklm`, a uv tool). Re-verify with
`notebooklm --version` before trusting this; on a version bump, run the `nlm-cli-contract`
subagent to diff reality against these notes.

Remember the engram rule from `AGENTS.md`: `engram search "<topic>" --project notebooklm-sync`
before you start, `engram save` after. Record new CLI quirks here **and** in engram.

## Commands we use

| Purpose | Command |
|---|---|
| List notebooks | `notebooklm list --json` |
| List sources | `notebooklm source list -n <nb> --json` |
| Add a source | `notebooklm source add <url> -n <nb> --type url --json` |
| Refresh in place | `notebooklm source refresh <source_id> -n <nb> --json` |
| Freshness probe | `notebooklm source stale <source_id> -n <nb> --json` |
| Await ingestion | `notebooklm source wait <source_id> -n <nb> --timeout N --json` |
| Auth preflight | `notebooklm auth check --test --json` |

Flags worth knowing:

- `-n/--notebook` also reads the **`NOTEBOOKLM_NOTEBOOK`** env var, and accepts **partial
  IDs** (`abc` matches `abc123...`). We always pass the full ID explicitly — partial matching
  is a footgun when two IDs share a prefix.
- `-p/--profile` selects the auth profile; `--quiet` suppresses status output.
- `source add --type` accepts `url|text|file|youtube`. Auto-detection exists, but a
  path-shaped string that doesn't exist on disk is silently ingested as **inline text**. Pass
  `--type` explicitly.
- `source add` rejects internal/private hosts by default (SSRF guard) and non-http(s) schemes
  outright.

## The four sharp edges

These are counterintuitive and have already cost time once.

1. **`source stale` inverts its exit code** under `--exit-on-stale` (0 = stale, 1 = fresh).
   The default is the normal convention (0 = check completed, 1 = error). Never branch on the
   exit code — read the JSON `stale` field.
2. **`source wait` uses exit 2 for timeout**, 1 for failure, 0 for ready. A timeout means
   *still processing* → treat as `PENDING` and reconcile next run. Collapsing 2 into 1 turns a
   slow ingest into a spurious hard failure.
3. **`auth check` without `--test` is local-only.** It checks that the storage file exists and
   parses. It returned `{"status": "ok"}` on this machine while live calls were failing with
   `Authentication expired`. Always pass `--test` for a real preflight.
4. **Errors come back in-band on stdout**, sometimes with exit code 0:
   ```json
   {"error": true, "code": "UNEXPECTED_ERROR", "message": "Authentication expired or invalid..."}
   ```
   Parse the payload and check for `error: true` before trusting the exit code.

## The Source model

From `notebooklm/_types/sources.py`. `source list --json` rows carry:

| Field | Notes |
|---|---|
| `id` | Source ID, used by refresh/delete/wait |
| `title` | May be null |
| `url` | **Our matching key.** Null for pasted text and uploaded files |
| `type` | The source kind — **on the wire the field is `type`, not `kind`** |
| `status` | Lowercase on the wire: `ready` / `processing` / `error` |
| `created_at` | ISO timestamp, may be null |

**Verified live wire shape** (v0.7.3) — the Python enum names are uppercase but
the JSON is not, and the rows are nested with notebook metadata:

```json
{"notebook_id": "...", "notebook_title": "...", "count": 2,
 "sources": [{"index": 1, "id": "6240829a-...", "title": "Example Domain",
              "type": "web_page", "url": "https://example.com/",
              "status": "ready", "status_id": 2,
              "created_at": "2026-08-08T16:24:24"}]}
```

`source_from_payload` in `nlm.py` reads `kind or type` for this reason. A captured
copy lives at `tests/fixtures/source_list.json`, guarded by `tests/test_live_shape.py`.

`SourceType` values: `web_page`, `youtube`, `google_docs`, `google_slides`,
`google_spreadsheet`, `google_drive_audio`, `google_drive_video`, `pdf`, `pasted_text`,
`markdown`, `docx`, `csv`, `epub`, `image`, `media`, `unknown`.

**`refresh` only works on URL/Drive-backed sources** (`web_page`, `youtube`, the `google_*`
kinds). Refreshing `pasted_text` or an uploaded file is invalid — the engine downgrades those
to SKIP with a warning rather than erroring.

## Auth model

There is **no NotebookLM public API and no API key**. Authentication is Google **session
cookies** — `SID` plus `__Secure-1PSIDTS` — in
`~/.notebooklm/profiles/<profile>/storage_state.json`.

- `notebooklm login` is **interactive** (launches a browser). An agent cannot complete it —
  ask the user to run it.
- Cookies rotate and expire. `notebooklm auth refresh` is the one-shot keepalive; 15–20 min is
  the recommended cadence for unattended use.
- `NOTEBOOKLM_AUTH_JSON` holds inline storage-state JSON for CI and bypasses the file. It is a
  **full Google session credential** — do not introduce it, log it, or write it to `.env`
  without the user explicitly asking.
- Other env vars: `NOTEBOOKLM_PROFILE`, `NOTEBOOKLM_NOTEBOOK`, `NOTEBOOKLM_HOME`.

## Commands we deliberately do not use

`source delete`, `source delete-by-title`, `source clean` — sync never deletes (see
`AGENTS.md`). `source add-research` (web/drive research import) is out of scope for now.
