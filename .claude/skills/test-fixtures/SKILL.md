---
name: test-fixtures
description: How to test notebooklm-sync offline — the fake notebooklm CLI shim, canned JSON fixtures, and simulating error envelopes, timeouts, and failed sources. Use when writing, fixing, or debugging tests, or when a test seems to want network or Google auth.
---

# Testing offline

**A test that needs live auth or network is a broken test.** No exceptions. The upstream CLI
talks to Google with session cookies that expire — a suite depending on that is a suite that
fails randomly on someone else's machine.

Remember the engram rule from `AGENTS.md`: read memory before you start, write it after.

## How the shim works

`tests/fake_notebooklm.py` is a standalone script that impersonates the upstream CLI. The
`fake_cli` fixture in `conftest.py` writes an executable `notebooklm` wrapper into a tmp dir
and **prepends that dir to `PATH`**, so `nlm.py`'s `subprocess` call resolves to the fake.

The shim dispatches on argv, reads a JSON scenario file pointed at by `FAKE_NLM_SCENARIO`, and
prints the matching canned response. This works precisely because of the `AGENTS.md` rule that
**all** upstream calls funnel through `nlm.py` — one seam to fake.

```python
def test_something(fake_cli):
    fake_cli.scenario({
        "source list": {"sources": [{"id": "s1", "url": "https://a.com", "status": "READY"}]},
    })
    ...
```

## Scenario format

Keys are matched against the joined subcommand (`"source list"`, `"source add"`,
`"auth check"`). Each value is either a response object, or `{"stdout": ..., "exit": N}` when
the exit code matters.

## Simulating the cases that actually bite

These mirror the sharp edges in the `notebooklm-cli` skill — every one needs a test.

**In-band error envelope** (note: exit code 0, error in the payload):
```python
{"auth check": {"stdout": {"error": True, "code": "UNEXPECTED_ERROR",
                           "message": "Authentication expired"}, "exit": 0}}
```

**`source wait` timeout → `PENDING`, not a failure:**
```python
{"source wait": {"stdout": {"status": "PROCESSING"}, "exit": 2}}
```

**`source wait` hard failure:**
```python
{"source wait": {"stdout": {"status": "ERROR"}, "exit": 1}}
```

**A source that can't be refreshed** — give it `"kind": "pasted_text"` in the `source list`
response and assert the engine downgrades `override` to `SKIP` rather than calling refresh.

## Rules

- **Never** invoke the real `notebooklm` binary from a test. If `PATH` isn't stubbed, that's a
  bug in the test, not a reason to skip it.
- Assert on the **argv the shim received** (`fake_cli.calls`) to prove we pass the right flags
  — e.g. that `auth check` is called with `--test`, and that `source add` receives the
  *original* URL rather than the normalized one.
- Keep `plan()` tests pure: they need no shim at all, just a manifest and a source list.
- Use a tmp-path SQLite DB per test; never touch `./notebooklm-sync.db`.

## Capturing real fixtures

If you need a realistic payload, capture it once from a live run (requires the user to be
logged in) and commit it under `tests/fixtures/`:

```bash
notebooklm source list -n <id> --json > tests/fixtures/source_list.json
```

**Scrub before committing** — payloads can carry notebook IDs, titles, and URLs the user may
not want in git. Never commit anything from `auth check` or `storage_state.json`.
