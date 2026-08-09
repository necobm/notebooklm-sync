# Roadmap

Order and status of the features. Each entry points at its folder in `../features/`.

## Done ✅

1. **001 · [Project scaffolding](../features/001-project-scaffolding/)** — the declarative sync
   tool end to end: config, manifest, URL matching, plan/execute engine, the `notebooklm` adapter,
   SQLite audit log and five CLI commands. Verified offline and live.

2. **002 · [Close the scaffolding gaps](../features/002-close-scaffolding-gaps/)** — the loose ends
   left by 001: the source `kind` now reaches the local mirror, `notebooks` is a real health check
   built on `nlm.list_notebooks()`, `sync --only-stale` narrows `override` via `nlm.is_stale()`,
   `tests/test_cli.py` covers the exit-code contract through `CliRunner`, `-v/--verbose` echoes
   every upstream argv, and CI runs `ruff` + `pytest` offline on 3.11 and 3.12. 117 tests pass.
   *The workflow itself has not run yet — it needs a push to a remote.*

3. **003 · [Web crawl rules](../features/003-web-crawl-rules/)** — a manifest entry can declare a
   subtree of a website (`https://site.com/*[level=2][except=blog]`) instead of one page. Resolved
   from `robots.txt` / `sitemap.xml`, with an HTML crawl as the fallback, capped per rule and cached
   in SQLite (schema v2). Adds the project's second seam — `discovery.py`, the only module allowed
   to touch HTTP — the pure `crawl.py` rule language, and an `expand` command to preview what a rule
   matches before committing to sources sync can never remove. No new dependency; 212 tests pass.
   Verified live against `mundana.us`: `/*[except=blog]` → 37 of its 276 sitemap URLs.

4. **004 · [Progress reporting for the slow commands](../features/004-progress-reporting/)** —
   `sync`, `status` and `expand` show what they are doing while they do it: a phase list, a
   determinate bar over the execute loop, and a spinner with a live fetch counter for discovery,
   rendered on stderr and wiped when the run ends. The two silent paths it exists for are
   `source wait` (up to 120s per added source) and the crawl fallback (0.2s × up to 100 fetches per
   rule). Adds the project's third seam — `progress.py`, the only module allowed to render progress
   — reached through optional `on_*` callbacks on `engine` and `discovery` that print nothing.
   Follows the `tui-design` skill: 16-ANSI semantic slots, never colour alone, a bar only where the
   total is real, and a 200ms grace so a cache hit never flashes. Off automatically when stderr is
   not a TTY, which is why all 212 existing tests passed unmodified. No new dependency; 239 tests
   pass. Not verified in tmux, and not against a live NotebookLM ingest.

5. **005 · [Command and action logging](../features/005-command-logging/)** — every invocation
   writes a durable plain-text record to `var/log/<YYYY-MM-DD>-<command>.log`, so a day's `sync`
   runs share one file and each line carries a token identifying its run: the upstream argv with its
   exit code and duration, every executed action with its outcome, the plan and result counts, and
   the command's own exit code. Adds the project's fourth seam — `logs.py`, the only module allowed
   to attach a handler or open a log file — with every other module emitting through
   `logging.getLogger(__name__)`, which writes nothing while no session is open. That is also how
   the suite stays silent: an autouse fixture sets `SYNC_LOG=0`, so all 239 existing tests passed
   unmodified. Fills the gap between the SQLite tables, which record what `sync` *decided* and
   nothing at all about the other five commands, and stderr, which is gone as soon as the terminal
   scrolls. `SYNC_LOG_LEVEL=DEBUG` adds every HTTP fetch; retention is by age, not by size. No new
   dependency — stdlib `logging`, chosen over `structlog`/`loguru` because the queryable view of a
   run is already the SQLite audit log. 271 tests pass. Not yet exercised against a live
   NotebookLM run.

## Next 🔜

*Nothing scheduled. Pick the next item from the backlog and give it a `features/NNN-…/` folder.*

## Backlog / ideas 💡

- **RSS / Atom feed expansion** — the other half of the old "RSS / sitemap expansion" item, now that
  [003](../features/003-web-crawl-rules/) covers sites. A feed is a list, not a tree, so it wants
  its own rule shape rather than a `/*` pattern. Would need `feedparser`.
- **Orphan pruning** — one-way mirroring behind an explicit `--prune` flag *and* a confirmation
  prompt. Deliberately absent today; see the never-delete rule in `tech-stack.md`. Do not add
  casually.
- **Unattended / scheduled runs** — a systemd timer plus `notebooklm auth refresh` as a 15–20
  minute cookie keepalive, since Google sessions stale out.
- **Stricter tooling** — expand the ruff rule selection beyond the defaults and add a type
  checker; the code is already fully annotated.

> Every new feature is created as `features/NNN-feature-name/` with `spec.md`, `plan.md` and
> `tasks.md` before any code is touched.
