# notebooklm-sync

Keep a [NotebookLM](https://notebooklm.google.com) notebook in sync with a declared list of
web sources.

You write the sources you *want* in a YAML manifest; `notebooklm-sync` reconciles that against
what the notebook actually has, and records every run in SQLite so you can see what changed and
when. It drives the [`notebooklm`](https://pypi.org/project/notebooklm-py/) CLI under the hood.

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
cp ./sources/example.yaml ./sources/your-sources.yaml
$EDITOR ./sources/your-sources.yaml
```

Cookies expire. If sync reports an auth failure, re-run `notebooklm login`. For unattended use,
schedule `notebooklm auth refresh` every 15–20 minutes.

## Usage

```bash
notebooklm-sync sync [notebook-name]          # sync one notebook
notebooklm-sync sync                          # pick a notebook interactively
notebooklm-sync sync [notebook-name] --dry-run
notebooklm-sync status [notebook-name]        # show drift, change nothing
notebooklm-sync expand [notebook-name]        # what do this manifest's crawl rules match?
notebooklm-sync notebooks                     # configured notebooks, health check + last sync
notebooklm-sync history [notebook-name]       # past runs
```

`[notebook-name]` is one of the names you listed in `NOTEBOOKS` in your `.env`.

Flags worth knowing:

| Flag | What it does |
|---|---|
| `--dry-run` | Plan only. Runs the same reconciliation and stops before any change. |
| `--only-stale` | Under `override`, refresh only the sources NotebookLM reports as stale. Works with `--dry-run`. |
| `--no-wait` | Don't wait for ingestion; a source still processing is reported as `pending`. |
| `--refresh-discovery` | Re-read the sites behind crawl rules instead of using the cached URL lists. |
| `--no-progress` | Don't show the live progress display. Implied by `-v`. |
| `-v` / `--verbose` | Print every `notebooklm` invocation and every HTTP request to stderr, so you can reproduce it by hand. |

### Progress

`sync`, `status` and `expand` show a live display while they work — the three commands that can run
for a while. Adding a source waits for NotebookLM to ingest it (up to `SYNC_WAIT_TIMEOUT`, 120s each
by default), and a crawl rule on a site with no sitemap reads pages one at a time.

```
  ✓ discovery   1 rule → 37 URL(s)
  ✓ auth        credentials ok
  ✓ sources     41 in notebook
  ⠹ syncing     ━━━━━━━━━━╸━━━━━━━━━  12/41  0:00:07
                add   https://mundana.us/blog/post-12
                ⠋ waiting for ingestion  0:00:04
```

It writes to **stderr** and wipes itself when the run ends, so `notebooklm-sync sync ... | cat`
gives you exactly what it always did. It also turns itself off automatically when stderr is not a
terminal — pipes, redirects, CI — and whenever `-v` is used, since both would be writing to the
same place. To disable it permanently, set `SYNC_PROGRESS=0` in your `.env`.

`notebooks` checks each configured `NOTEBOOK_<NAME>_ID` against NotebookLM and marks it `ok` or
`missing`. If it can't reach NotebookLM — expired cookies, no network — it says so and still shows
your configuration.

## Web crawl rules

A manifest entry can declare a **subtree of a website** instead of one page. Any `url:` whose path
ends in `/*` is a rule:

```yaml
sources:
  # Every page on the site except the blog.
  - url: https://www.mundana.us/*[except=blog]
```

| Form | Matches |
|---|---|
| `https://site.com/page` | only that page (no `/*` — nothing changes) |
| `https://site.com/*` | the base page and every descendant, any depth |
| `https://site.com/docs/*` | `/docs` and everything under it |
| `https://site.com/*[level=2]` | descendants at most 2 levels **below the base** |
| `https://site.com/*[except=blog]` | …minus `/blog` and its children — repeatable |
| `https://site.com/*[max=10]` | at most 10 URLs from this rule |

Modifiers combine in any order: `https://site.com/*[level=2][except=blog][max=300]`.

Rules are resolved from `robots.txt` and `/sitemap.xml`; only a site that publishes no sitemap
falls back to an HTML crawl. The resolved list is cached for `SYNC_DISCOVERY_TTL` seconds (24h by
default), so repeat runs cost no network.

A rule is capped at `SYNC_DISCOVERY_MAX` URLs (100 by default). Over the cap it keeps the
shallowest N and warns — it never fails. An inline `[max=N]` **always** overrides that default, in
both directions. `except` matches on segment boundaries, so `except=blog` never removes
`/blogging`, and a page you also list by hand keeps its own `title:` and `policy:`.

Run `notebooklm-sync expand [notebook-name]` before trusting a rule: it reads the website and nothing
else — no auth, no NotebookLM call — and prints exactly what would be added. Sync never deletes,
so a rule that matched too much cannot be undone by this tool.

The full syntax is documented in [`sources/example.yaml`](sources/example.yaml).

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

`0` ok · `1` one or more source actions failed, or a crawl rule matched nothing ·
`2` config/manifest error, including a malformed crawl rule · `3` auth failure

## Development

```bash
uv run pytest      # offline; needs no network and no Google auth
uv run ruff check .
```

CI runs both on every push and pull request, against Python 3.11 and 3.12. It needs no secrets.

See `AGENTS.md` for the project contract, and `docs/spec/` for the specs — the project's
constitution and one folder per feature.
