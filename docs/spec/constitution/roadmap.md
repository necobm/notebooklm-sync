# Roadmap

Order and status of the features. Each entry points at its folder in `../features/`.

## Done ✅

1. **001 · [Project scaffolding](../features/001-project-scaffolding/)** — the declarative sync
   tool end to end: config, manifest, URL matching, plan/execute engine, the `notebooklm` adapter,
   SQLite audit log and five CLI commands. Verified offline and live.

## Next 🔜

2. **002 · Close the scaffolding gaps** — the known loose ends left by 001, none of which change
   the architecture:
   - `cli.py` passes `kind=None` to `upsert_source`, so the local `sources` mirror never records
     the source type even though we have it.
   - `nlm.list_notebooks()` is implemented but unused — wiring it into the `notebooks` command
     would turn it into a real health check that flags a `NOTEBOOK_*_ID` that no longer exists.
   - `nlm.is_stale()` is implemented but unused — an `--only-stale` mode for `override` would
     refresh just the sources that need it instead of all of them.
   - No CLI-level tests; Typer's `CliRunner` would cover exit codes and flag plumbing.
   - No `-v/--verbose` to surface the argv captured in `NlmClient.calls`.
   - No CI. The suite is fully offline, so a GitHub Actions job needs no secrets.

   *Not specced yet — the folder gets created when the feature starts.*

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
