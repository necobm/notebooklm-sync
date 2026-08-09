---
name: sync-engine
description: Reconciliation rules for notebooklm-sync — the three sync policies (skip/override/create), URL normalization and matching, orphan handling, the plan/execute split, and exit codes. Use when changing engine.py, matching.py, models.py, or any policy behaviour.
---

# Sync engine rules

Domain rules that live in **our** code, not upstream's. For the upstream CLI surface see the
`notebooklm-cli` skill.

Remember the engram rule from `AGENTS.md`: read memory before you start
(`engram search "<topic>" --project notebooklm-sync`), write it after.

## Plan, then execute

`engine.plan()` is pure: it takes the manifest plus the notebook's current sources and returns
a `SyncPlan` of `PlannedAction`s. `engine.execute()` performs them.

`--dry-run` runs the **identical** `plan()` and stops. Never write a second dry-run-only path —
it drifts from the real one and then lies to you about what a real run would do.

## Policies

Policy resolution order, first match wins:

1. per-source `policy:` in the manifest entry
2. `NOTEBOOK_<NAME>_POLICY`
3. `SYNC_POLICY`
4. `skip`

Decision table, per manifest entry:

| Situation | Action |
|---|---|
| No matching source in notebook | `ADD` |
| Match, policy `skip` | `SKIP` |
| Match, policy `override` | `REFRESH` — `notebooklm source refresh <id>` |
| Match, policy `override`, but source kind is not refreshable | `SKIP` + warning |
| Match, policy `create` | `ADD` (intentional duplicate) |
| In notebook, not in manifest | `ORPHAN` |

`override` refreshes **in place** rather than delete-and-re-add: the source keeps its ID, so
citations in saved notes and chat history keep resolving. Only URL/Drive-backed kinds can be
refreshed — see the `notebooklm-cli` skill for the list.

### `--only-stale`

`engine.apply_stale_filter(plan_, client)` narrows `override` to the sources upstream reports as
stale, rewriting the rest to `SKIP` with `reason="not stale"`.

It sits **between** `plan()` and `execute()` rather than inside `plan()`, which must stay pure and
client-free. The probe is read-only, so it runs under `--dry-run` too — that is deliberate: one
code path serves both modes and the preview matches the real run.

It **fails open.** `is_stale()` returning `None`, or raising `NlmError`, leaves the `REFRESH` in
place. A wasted refresh costs one call; a wrongly skipped one leaves the notebook serving stale
content, which is the thing this tool exists to prevent.

## The never-delete invariant

**Sync never deletes anything.** Orphans are recorded and reported, never removed. A typo in a
manifest must not be able to destroy a notebook. Do not add pruning, and do not call
`source delete`, `source delete-by-title`, or `source clean` — if a user asks for pruning, it
needs an explicit opt-in flag *and* a confirmation prompt, designed deliberately.

## URL normalization and matching

Matching is by **normalized URL**, since `Source.url` is the only stable identity NotebookLM
gives us. `matching.normalize_url()` applies, in order:

1. strip whitespace; reject non-`http(s)` schemes
2. lowercase scheme and host (never the path — paths can be case-significant)
3. drop a leading `www.`
4. drop the default port (`:80` for http, `:443` for https)
5. drop tracking params: `utm_*`, `fbclid`, `gclid`, `mc_eid`, `ref`, `ref_src`
6. drop the fragment
7. drop a trailing slash on non-empty paths
8. sort the remaining query params so ordering doesn't defeat matching
9. YouTube special case: any `youtu.be/<id>`, `/shorts/<id>`, `/embed/<id>`, or
   `watch?v=<id>&t=…` collapses to `https://youtube.com/watch?v=<id>`

Normalization is for **comparison only** — always send the user's original URL to
`source add`, never the normalized form.

Manifest entries are de-duplicated by normalized URL before planning. If the notebook itself
contains duplicates of the same normalized URL, match the **first** one and report the rest as
duplicates in the summary.

## Ingestion outcomes

After an `ADD`, `source wait` decides the outcome:

- exit 0 → `READY`
- exit 1 → `FAILED` (counts toward the failure exit code)
- exit 2 → `PENDING`, **not** a failure — ingestion is just slow; the next run reconciles it

Per-source failures never abort the run. Record the error, continue, and reflect it in the
summary and the exit code.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Everything succeeded (skips and pending count as success) |
| 1 | One or more source actions failed |
| 2 | Config or manifest error (bad env, missing/invalid YAML) |
| 3 | Auth failure — message must tell the user to run `notebooklm login` |

One deliberate exception: **`notebooks` degrades instead of exiting.** Its upstream health check
warns, renders `?`, and still exits 0 — it is the command you run *because* auth broke, so it must
keep showing your configuration when everything else is failing.

## What lands in SQLite

- `sources` — local mirror of what we believe is in the notebook, plus `last_seen_at` and
  `last_action`.
- `sync_runs` / `sync_events` — append-only audit log. Never rewrite history; a re-run adds
  rows. Dry runs are recorded too, with `dry_run=1`.
