# docs/spec/ — Spec Driven Development

> How this project is documented and how work gets done: the spec comes first, then the plan,
> then the tasks, and only then the code.

## Structure

```
docs/spec/
├── constitution/            ← stable project rules (rarely change)
│   ├── mission.md           ← what we build and for whom
│   ├── tech-stack.md        ← technologies, conventions and hard limits
│   └── roadmap.md           ← order and status of the features
└── features/                ← one folder per feature
    └── NNN-feature-name/
        ├── spec.md          ← what it does + acceptance criteria
        ├── plan.md          ← how it is implemented
        └── tasks.md         ← task checklist
```

## Flow for a new feature

1. Create `features/NNN-feature-name/` with the next free number (`001`, `002`, …).
2. Write `spec.md`: what it does, why, and measurable acceptance criteria.
3. Write `plan.md`: technical approach and decisions, respecting `constitution/tech-stack.md`.
4. Break it down in `tasks.md` and track progress there.
5. Implement and validate: `uv run pytest` and `uv run ruff check` must both be clean, and
   anything that touches the live NotebookLM API is verified manually (see `tech-stack.md`).
6. Update `constitution/roadmap.md` — move the feature to "Done".

A feature folder is created when the feature starts, not before. Entries sitting in the roadmap's
"Next" or "Backlog" have no folder yet.

> **The constitution wins.** If a feature conflicts with `mission.md` or `tech-stack.md`, the
> feature gets reworked, not the constitution.

Agents working in this repo should read `constitution/` before planning; `AGENTS.md` at the repo
root carries the operational contract (memory, subagents, testing gates).
