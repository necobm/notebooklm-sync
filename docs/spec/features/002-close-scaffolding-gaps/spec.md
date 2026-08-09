# 002 · Close the scaffolding gaps

**Status:** specced 📝 — not implemented

## What it does

Six loose ends left by [001](../001-project-scaffolding/), none of which change the architecture.
Together they turn already-written-but-unused code into user-visible behaviour, and give the repo
the test and CI coverage it is missing.

**1 · The local mirror records the source kind.** `sources.kind` is currently always NULL because
`cli.py` passes `kind=None`, even though the kind is right there in the source the plan matched
against. After this, `notebooklm-sync` knows the type of every source it has seen — which is what
makes "why did `override` skip this one?" answerable from the database.

**2 · `notebooks` becomes a real health check.** It calls `notebooklm list` and adds a **Remote**
column, so a `NOTEBOOK_*_ID` pointing at a notebook that no longer exists is flagged instead of
failing later mid-sync. When the call fails — expired cookies, no network — it prints a warning and
still renders the offline table.

**3 · `sync --only-stale`.** Under `override`, refresh only the sources upstream reports as stale
instead of all of them:

```bash
notebooklm-sync sync research --policy override --only-stale
```

**4 · CLI-level tests.** `tests/test_cli.py` drives the Typer app through `CliRunner`, covering exit
codes and flag plumbing — the layer where every typed error becomes an exit code, and the only layer
the suite does not currently touch.

**5 · `-v/--verbose`.** Print the argv of every upstream `notebooklm` invocation to stderr, so a
surprising result can be reproduced by hand.

**6 · CI.** A GitHub Actions job running `ruff check` and `pytest` on push and pull request. The
suite is fully offline, so it needs no secrets.

## Why

Each gap is a small, concrete cost being paid on every run:

- Two adapter methods (`nlm.list_notebooks()`, `nlm.is_stale()`) are implemented, tested and never
  called. Dead code either becomes used or becomes wrong — this makes them used.
- The `kind` column exists and is always NULL, so the mirror cannot answer questions about source
  types and the schema quietly lies about what we know.
- Under `override`, every matching source is refreshed on every run whether or not anything changed:
  needless upstream work, and needless waiting.
- The exit-code contract (`0/1/2/3`) is the tool's public interface for scripting, and nothing tests
  it. Every existing test stops below `cli.py`.
- When something goes wrong, the user cannot see what was actually run, even though `NlmClient`
  already records it.
- With no CI, "the suite is green" is a claim about one machine.

## Acceptance criteria

**Source kind in the mirror**

- [ ] After a sync that skips or refreshes an existing source, that source's row in `sources` has a
      non-NULL `kind` matching what `source list` reported.
- [ ] After a sync that adds a new source, its row records the kind returned by `source add` (or the
      manifest's `type:` when upstream returns none).
- [ ] A later run that cannot determine the kind leaves the previously recorded `kind` intact rather
      than nulling it.
- [ ] `PRAGMA user_version` is still `1` — no schema change.

**`notebooks` health check**

- [ ] `notebooks` calls `notebooklm list` and marks each configured notebook `ok` or `missing`
      against the IDs it returns.
- [ ] A `NOTEBOOK_*_ID` absent from the upstream list is flagged `missing`, and the command still
      exits 0.
- [ ] When the upstream call fails (auth expired, binary absent, timeout), the command prints a
      warning, renders the table with the remote column as `?`, and exits **0** — a broken session
      must not stop the diagnostic command from reporting configuration.

**`--only-stale`**

- [ ] With `--policy override --only-stale`, a source upstream reports as *not* stale is skipped
      with a reason mentioning staleness, and `source refresh` is never called for it.
- [ ] A source reported as stale is refreshed as usual.
- [ ] The decision is read from the JSON `stale` field, never from `source stale`'s exit code.
- [ ] If the staleness probe fails or returns nothing usable, the source is refreshed anyway
      (fail open) and the run does not fail.
- [ ] `--dry-run --only-stale` performs the same probing and previews the identical refresh/skip
      set a real run would produce, while still issuing no mutating call.
- [ ] `--only-stale` under `skip` or `create` is an accepted no-op.

**CLI tests**

- [ ] `tests/test_cli.py` exercises every command through `CliRunner`, offline, with no `.env` and
      no auth.
- [ ] Exit codes are asserted end to end: `0` success · `1` an action failed · `2` config or
      manifest error · `3` auth failure, with the `notebooklm login` hint in the message.
- [ ] `sync --dry-run` is asserted to issue **only** `source list` — no `auth check`, no `source
      add`, no `source refresh`.
- [ ] A `source wait` timeout is asserted to report `pending` with the run still exiting 0.
- [ ] `tests/fixtures/notebook_list.json` pins the real `notebooklm list --json` wire shape, the way
      `test_live_shape.py` pins the source shape.
- [ ] The whole suite still passes offline and `ruff check` is clean.

**`-v/--verbose`**

- [ ] `-v` on `sync`, `status` and `notebooks` prints one line per upstream invocation, showing the
      full argv including `--json` and the injected profile.
- [ ] The lines go to **stderr**, so piping stdout is unaffected.
- [ ] Without `-v` the output is byte-identical to today's.
- [ ] No print/console call is added to `nlm.py` — rendering stays in `cli.py`.

**CI**

- [ ] A GitHub Actions workflow runs `uv sync`, `uv run ruff check .` and `uv run pytest` on push
      and pull request, and passes.
- [ ] It declares no secrets and no Google credentials, and does not set `NOTEBOOKLM_AUTH_JSON`.
- [ ] It runs against both Python 3.11 (the declared floor) and 3.12.

## Out of scope

- **Orphan pruning** — still deliberately absent; see the never-delete rule in
  [`tech-stack.md`](../../constitution/tech-stack.md).
- **RSS / sitemap expansion**, **scheduled unattended runs**, **a type checker and a stricter ruff
  rule set** — roadmap backlog, unchanged by this feature.
- **Schema changes.** Everything here fits schema v1; nothing needs a migration.
- **New upstream commands.** Only `list` and `source stale` get wired up, and both wrappers already
  exist in `nlm.py`.
