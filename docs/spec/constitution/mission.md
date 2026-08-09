# Mission

## What we build

`notebooklm-sync` is a command-line tool that keeps a [NotebookLM](https://notebooklm.google.com)
notebook in sync with a declared list of web sources. You write the sources you *want* in a YAML
manifest; the tool reconciles that against what the notebook actually has, driving the
already-installed upstream `notebooklm` CLI as a subprocess.

It has three pieces:

1. **Declarative manifest** — a per-notebook YAML file listing the desired sources, mapped from
   `.env`. This is the desired state; nothing else is.
2. **Reconciliation engine** — compares manifest against notebook and produces a plan
   (add / refresh / skip / orphan), then executes it. A policy decides what happens on a match.
3. **Audit log** — every run, including dry runs, is recorded in SQLite, so you can see what
   changed and when.

## Who it is for

- **The operator maintaining research notebooks** — one person, on their own machine, who wants
  a notebook's sources to be reproducible from a file in git instead of remembered.
- **Coding agents working in this repo** — the contract in `AGENTS.md`, the skills under
  `.claude/skills/`, and this constitution exist so an agent can act correctly without
  rediscovering the upstream CLI's quirks.

## Principles

- **Declarative** — the manifest is the source of truth for what the notebook should contain.
  You edit the file, not the notebook.
- **Repeatable and idempotent** — every match is resolved by an explicit policy, so re-running a
  sync under the default `skip` policy changes nothing. The same input always produces the same
  plan.
- **Auditable** — nothing happens invisibly. Every run lands in SQLite as an append-only record,
  dry runs included, and `--dry-run` reuses the exact planning code a real run would.
- **Non-destructive** — sync never deletes. A typo in a manifest must not be able to destroy a
  notebook; sources present in the notebook but absent from the manifest are reported, never
  removed.

## What it is NOT

- **Not a NotebookLM API client.** There is no public API and no API key. This tool wraps a
  third-party CLI that authenticates with Google session cookies, and inherits that CLI's limits.
- **Not a two-way mirror.** It reconciles in one direction only. Pruning is deliberately absent
  and would need an explicit opt-in flag *and* a confirmation prompt.
- **Not a hosted or multi-user service.** It is a local tool, run by one person against their own
  Google session. No server, no shared state.
- **Not the upstream `notebooklm-py` package.** Despite this directory's name, they are unrelated
  — see the naming rule in [tech-stack.md](tech-stack.md).
