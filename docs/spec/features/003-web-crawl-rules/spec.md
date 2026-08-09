# 003 · Web crawl rules in the source manifest

**Status:** implemented ✅ — 2026-08-09 · 212 tests pass offline (was 117) · ruff clean ·
verified live against `mundana.us`

## What it does

A manifest entry can declare a **subtree of a website** instead of a single page. The tool resolves
that rule to a concrete list of URLs and syncs them as ordinary sources.

**1 · A wildcard rule in `url:`.** An entry whose path ends in `/*` — optionally followed by
`[key=value]` modifiers — means "this page and its descendants" rather than "this page":

```yaml
sources:
  - url: https://www.mundana.us/*[except=blog]
```

Anything without the trailing `/*` is a plain URL and behaves exactly as it does today.

**2 · Three modifiers, combinable in any order.** `[level=N]` bounds how many segments below the
base a page may be. `[except=path]` removes a subtree and may be repeated. `[max=N]` sets how many
URLs the rule may contribute.

**3 · Sitemap first, crawl as a fallback.** Resolution reads `robots.txt` for `Sitemap:` lines, then
tries `/sitemap.xml`, following one level of `<sitemapindex>`. Only when a site publishes no sitemap
does the tool fetch HTML and follow same-host links. On `mundana.us` that is **one request for 276
URLs** instead of 276 page fetches.

**4 · Expansion is cached in SQLite.** Each rule's resolved URL list is stored with a timestamp and
reused for `SYNC_DISCOVERY_TTL` seconds (24h by default), so repeat syncs and `status` cost no
network. `--refresh-discovery` forces a re-fetch.

**5 · A new `expand` command.** `notebooklm-sync expand <notebook>` prints exactly which URLs each
rule resolves to, without planning, without auth and without touching the notebook — the check you
run before committing to sources that sync can never remove.

**6 · The rules are documented in `sources/example.yaml`.** Every form above, with worked numbers
from a real site. That file is already validated by the test suite, so the examples cannot rot.

## Why

- **A manifest of hand-listed URLs is a copy of a sitemap that nobody updates.** `mundanav2.yaml`
  names three `mundana.us` pages; the site has 276. Every page published after the manifest was
  written is silently missing, and nothing in the tool reports that.
- **The interesting selection is almost always a subtree minus an exception.** "The whole site
  except the blog" is one line as a rule and 37 lines as a list — and the 37 are wrong the moment a
  page is added.
- **This is the roadmap's own backlog item.** "RSS / sitemap expansion" has been listed since 001;
  this promotes it, scoped to sites rather than feeds.
- **Doing it by hand is where the mistakes are.** Transcribing 37 URLs invites typos, and because
  sync never deletes, a typo that adds a wrong source cannot be undone by the tool.

## Acceptance criteria

**Rule grammar**

- [x] A `url:` whose path ends in `/*`, with or without a trailing `[...]` block, parses to a
      `CrawlRule`; every other string is a plain URL and reaches the engine unchanged.
- [x] `[level=N]`, `[except=path]` (repeatable) and `[max=N]` parse in any order and combination.
- [x] Malformed rules raise `ManifestError` (exit 2) naming the entry: unclosed `[`, unknown key,
      non-integer value, `level < 1`, `max < 1`, and `*` in any position but the final segment.
- [x] A rule carrying `title:` is a `ManifestError` — one title cannot name N pages.
- [x] `type:` and `policy:` on a rule are inherited by every URL it expands to.

**Rule semantics**

- [x] `https://site.com/*` includes `https://site.com/` itself, whether or not the sitemap lists it.
- [x] `level` counts segments **below the base**: for base `/`, `/store` is level 1 and `/blog/x` is
      level 2; for base `/store`, `/store/a` is level 1.
- [x] `except=blog` removes `/blog` and everything under it, and does **not** remove `/blogging`.
- [x] An `except` value is accepted as a bare path, a `/`-prefixed path, or a full URL on the host.
- [x] URLs on another host, or outside the base path, are discarded even when the sitemap lists them.
- [x] A URL both hand-listed and matched by a rule keeps the hand-written entry, with its `title:`
      and `policy:`, regardless of manifest order.
- [x] The expanded set is deduplicated by normalized URL, and `source add` still receives the
      **original** URL, never the normalized one.

**The cap**

- [x] With no `max=`, a rule is capped at `SYNC_DISCOVERY_MAX` (default 100).
- [x] An inline `[max=N]` overrides that default **in both directions** — `[max=10]` lowers the cap
      to 10 and `[max=300]` raises it to 300.
- [x] Exceeding the cap takes the first N and prints a warning naming the rule, how many matched and
      how many were kept. It is never an error and never stops the run.
- [x] Truncation order is shallowest-first then alphabetical, so the same input yields the same N
      on every run.
- [x] In the crawl fallback the cap is a real fetch budget: `[max=10]` stops after ten page fetches
      rather than crawling the site and discarding the rest.

**Discovery**

- [x] `robots.txt` `Sitemap:` lines are read first, then `/sitemap.xml`; a `<sitemapindex>` is
      followed one level deep; `.gz` sitemaps are decompressed.
- [x] A site with no reachable sitemap falls back to a breadth-first HTML crawl of same-host
      `<a href>` links, honouring `Disallow` for our user agent and pausing between fetches.
- [x] A rule that resolves to zero URLs, or whose fetches all fail, raises `DiscoveryError`
      (exit 1) naming the rule — it never silently syncs nothing.
- [x] All HTTP lives in `discovery.py`; no other module under `src/` imports `urllib.request`,
      `http.client` or `socket`. (`urllib.parse` is pure string work and is used freely, in
      `matching.py` and `crawl.py`.)

**Caching**

- [x] A rule resolved once is served from `discovery_cache` for `SYNC_DISCOVERY_TTL` seconds, and a
      second run within the TTL issues **zero** HTTP requests.
- [x] An entry older than the TTL is re-fetched and overwritten.
- [x] `--refresh-discovery` on `sync`, `status` and `expand` bypasses a fresh entry.
- [x] The cache key is order-independent, so `[level=2][except=blog]` and `[except=blog][level=2]`
      share one entry.
- [x] The key covers base URL, `level` and `max` but **not** `except`, so tightening an exclusion
      re-uses the cached candidates. Filtering re-runs on every invocation, cached or not, which is
      what keeps the truncation warning accurate on a cache hit.
- [x] `PRAGMA user_version` reads `2`, on a fresh database and on an existing v1 one.

**CLI**

- [x] Expansion happens between `load_manifest()` and `engine.plan()`. `plan()` takes plain entries,
      is unchanged, and stays pure.
- [x] `sync --dry-run` still issues exactly one `source list` and no mutating upstream call.
- [x] `expand <notebook>` prints each rule, its URL count, whether it came from the sitemap or a
      crawl, and the resolved URLs — with no `auth check` and no notebook call at all.
- [x] `-v` prints every HTTP request to **stderr**, alongside the existing `notebooklm` argv lines.
- [x] `status` and `sync` show one line per rule, plus the truncation warning when one applied.
- [x] A rule's `[modifiers]` survive rendering. Square brackets are Rich markup, so every printed
      rule — and every error quoting one — is escaped rather than silently swallowed.

**Tests and lint**

- [x] The whole suite still runs offline with no network and no Google auth; the HTTP seam is
      stubbed by a `fake_http` fixture the way `fake_cli` stubs the subprocess seam.
- [x] `tests/fixtures/sitemap.xml` pins the real sitemap wire shape, as `source_list.json` pins the
      source shape.
- [x] `uv run ruff check .` is clean, and no new runtime dependency is added to `pyproject.toml`.

## Out of scope

- **RSS and Atom feeds.** The roadmap item covered both; this feature is sites only. Feeds have a
  different shape (items, not a tree) and deserve their own rule.
- **Globs inside `except`.** `except=` takes a subtree prefix. Pattern matching can be added later
  without changing the grammar.
- **Concurrent fetching.** The sitemap path is one request and the crawl path is capped and cached,
  so sequential stdlib `urllib` is enough. `httpx` stays out of `pyproject.toml`.
- **Orphan pruning.** Still deliberately absent; see the never-delete rule in
  [`tech-stack.md`](../../constitution/tech-stack.md). A shrinking rule reports orphans like
  anything else.
- **Rendering JavaScript.** Only server-returned HTML is parsed. A site that needs a browser to
  reveal its links must publish a sitemap or be listed by hand.
- **Per-page titles.** Expanded sources are added without a `title:`; upstream derives one. Fetching
  every page for its `<title>` would defeat the point of the sitemap path.
