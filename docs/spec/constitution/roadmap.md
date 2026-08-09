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
