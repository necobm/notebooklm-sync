# 001 · Project scaffolding

**Status:** implemented ✅

## What it does

You declare the sources a NotebookLM notebook should contain in a YAML manifest, map that
manifest to a notebook in `.env`, and run:

```bash
notebooklm-sync sync research
```

The tool reads what the notebook currently holds, compares it against the manifest, and reconciles
the difference — adding what is missing, and applying the configured policy (`skip`, `override` or
`create`) to anything that already exists. `--dry-run` shows exactly what a real run would do
without touching the notebook.

Every run is recorded, so you can ask what the state is and what happened before:

```bash
notebooklm-sync status research      # current drift, changes nothing
notebooklm-sync notebooks            # configured notebooks and last sync
notebooklm-sync history research     # past runs
notebooklm-sync init                 # scaffold .env and a manifest in a fresh directory
```

## Why

Keeping a research notebook up to date was manual: open NotebookLM in a browser, paste URLs one by
one, and keep no record of what was added when or whether a source had gone stale. There was no way
to reproduce a notebook's source set, no way to tell what had drifted, and no history.

This feature makes the desired source set **declarative** (a file in git), reconciliation
**repeatable** (an explicit policy decides every match), and every run **auditable** (SQLite).

## Acceptance criteria

- [x] A dry run against an empty notebook plans the manifest's sources as additions and writes
      nothing to the notebook.
- [x] A real run adds the manifest's sources and they reach `status: ready`, confirmed by
      `notebooklm source list`.
- [x] Re-running under the default `skip` policy is a no-op: 0 added, everything skipped.
- [x] Running with `--policy override` refreshes matching sources **in place** — their source IDs
      are unchanged afterwards, so citations in saved notes and chats keep resolving.
- [x] `override` on a source whose kind cannot be refreshed degrades to a skip with a warning
      rather than failing.
- [x] A source present in the notebook but absent from the manifest is reported as an orphan and
      is still in the notebook after the run.
- [x] URLs that differ only by tracking parameters, `www.`, trailing slash, query order, fragment
      or YouTube URL shape are recognised as the same source.
- [x] The URL sent to `notebooklm source add` is the one the user wrote, not the normalized form.
- [x] Exit codes: `0` on success, `1` if one or more source actions failed, `2` on a config or
      manifest error, `3` on auth failure — and an auth failure tells the user to run
      `notebooklm login`.
- [x] An ingestion timeout is reported as `pending`, not `failed`, and does not make the run exit
      non-zero.
- [x] A failure on one source does not abort the run; the remaining actions still execute.
- [x] Every run appears in `history`, including dry runs, which are flagged as such.
- [x] The full test suite passes with no network access and no Google credentials.

## Out of scope

- **Orphan pruning** — deliberately absent; see the never-delete rule in the constitution and the
  roadmap backlog.
- **RSS / sitemap expansion**, **scheduled unattended runs** — roadmap backlog.
- **CLI-level tests, CI, `--verbose`, `--only-stale`, recording the source kind in the local
  mirror** — the known gaps, closed by
  [002 · Close the scaffolding gaps](../002-close-scaffolding-gaps/).
