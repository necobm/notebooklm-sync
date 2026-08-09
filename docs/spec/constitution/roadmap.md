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

## Next 🔜

*Nothing scheduled. Pick the next item from the backlog and give it a `features/NNN-…/` folder.*

## Backlog / ideas 💡

- **RSS / sitemap expansion** — let a manifest name a feed and expand it to article URLs, so a
  notebook grows as a source publishes. Needs `feedparser` and a fetch step.
- **Orphan pruning** — one-way mirroring behind an explicit `--prune` flag *and* a confirmation
  prompt. Deliberately absent today; see the never-delete rule in `tech-stack.md`. Do not add
  casually.
- **Unattended / scheduled runs** — a systemd timer plus `notebooklm auth refresh` as a 15–20
  minute cookie keepalive, since Google sessions stale out.
- **Stricter tooling** — expand the ruff rule selection beyond the defaults and add a type
  checker; the code is already fully annotated.

> Every new feature is created as `features/NNN-feature-name/` with `spec.md`, `plan.md` and
> `tasks.md` before any code is touched.
