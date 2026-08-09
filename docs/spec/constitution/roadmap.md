# Roadmap

Order and status of the features. Each entry points at its folder in `../features/`.

## Done ✅

1. **001 · [Project scaffolding](../features/001-project-scaffolding/)** — the declarative sync
   tool end to end: config, manifest, URL matching, plan/execute engine, the `notebooklm` adapter,
   SQLite audit log and five CLI commands. Verified offline and live.

## Next 🔜

2. **002 · [Close the scaffolding gaps](../features/002-close-scaffolding-gaps/)** — the known loose
   ends left by 001, none of which change the architecture: record the source `kind` in the local
   mirror, turn `notebooks` into a real health check with `nlm.list_notebooks()`, add `--only-stale`
   for `override` with `nlm.is_stale()`, cover the CLI with `CliRunner`, add `-v/--verbose`, and add
   an offline GitHub Actions job.

   *Specced 📝 — see [`spec.md`](../features/002-close-scaffolding-gaps/spec.md),
   [`plan.md`](../features/002-close-scaffolding-gaps/plan.md) and
   [`tasks.md`](../features/002-close-scaffolding-gaps/tasks.md). Not implemented.*

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
