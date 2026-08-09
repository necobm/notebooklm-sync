# notebooklm-sync

Keep a [NotebookLM](https://notebooklm.google.com) notebook in sync with a declared list of
web sources.

You write the sources you *want* in a YAML manifest; `notebooklm-sync` reconciles that against
what the notebook actually has, and records every run in SQLite so you can see what changed and
when. It drives the [`notebooklm`](https://pypi.org/project/notebooklm-py/) CLI under the hood.

> Unrelated to the upstream `notebooklm-py` package, despite this directory's name.

## Install

```bash
uv sync
```

## Setup

NotebookLM has no public API or API key — the upstream CLI authenticates with your **Google
session cookies**. Log in once, interactively:

```bash
notebooklm login          # opens a browser
notebooklm auth check --test
```

Then configure:

```bash
cp .env.example .env      # edit: notebook names, IDs, manifest paths
$EDITOR sources/research.yaml
```

Cookies expire. If sync reports an auth failure, re-run `notebooklm login`. For unattended use,
schedule `notebooklm auth refresh` every 15–20 minutes.

## Usage

```bash
notebooklm-sync sync research          # sync one notebook
notebooklm-sync sync                   # pick a notebook interactively
notebooklm-sync sync research --dry-run
notebooklm-sync status research        # show drift, change nothing
notebooklm-sync notebooks              # configured notebooks + last sync
notebooklm-sync history research       # past runs
```

## Sync policies

When a manifest URL already exists in the notebook, `SYNC_POLICY` decides:

| Policy | Behaviour |
|---|---|
| `skip` | Leave the existing source alone. Default, safest. |
| `override` | `notebooklm source refresh` it in place — same source ID, so citations in your notes and chats survive. |
| `create` | Add another copy anyway. |

Resolution order: per-source `policy:` in the manifest → `NOTEBOOK_<NAME>_POLICY` →
`SYNC_POLICY` → `skip`.

**Sync never deletes.** Sources in the notebook but not in your manifest are reported as
orphans and left untouched.

## Exit codes

`0` ok · `1` one or more source actions failed · `2` config/manifest error · `3` auth failure

## Development

```bash
uv run pytest      # offline; needs no network and no Google auth
```

See `AGENTS.md` for the project contract, and `docs/spec/` for the specs — the project's
constitution and one folder per feature.
