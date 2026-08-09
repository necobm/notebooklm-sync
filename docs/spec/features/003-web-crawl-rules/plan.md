# 003 · Web crawl rules in the source manifest — Plan

**Status:** implemented ✅ · **Last updated:** 2026-08-09

How the rule language in [`spec.md`](spec.md) gets built. A living document: update it when the work
lands.

---

## Approach

The feature introduces the first **network** access this project has ever had, into a codebase whose
entire test strategy rests on having exactly one seam per kind of side effect. So the shape of the
solution is decided before any of its details: **a new seam, built like the existing one.**

`nlm.py` is the only module allowed to import `subprocess`, which is what lets `fake_notebooklm.py`
impersonate the upstream binary and keep 117 tests offline. `discovery.py` becomes the only module
allowed to touch HTTP, for exactly the same reason and with exactly the same payoff.

The second decision is **where expansion happens**. It cannot be inside `plan()` — `plan()` is pure,
and a pure function cannot fetch a sitemap. It must not be a branch inside `execute()` either, or
`--dry-run` would stop previewing what a real run does. So expansion is a step **between**
`load_manifest()` and `engine.plan()`, in `cli.py`, and `plan()` never learns rules exist: it
receives a flat list of plain `ManifestEntry` exactly as it does today. All three invariants survive
untouched, and `engine.py` is not modified at all by this feature.

Built in dependency order, each green before the next starts:

1. **`crawl.py`** — the rule language as a pure function. Everything the syntax *means*, testable
   against literals with no network and no database.
2. **`discovery.py`** — turning a rule into URLs over HTTP, plus the cache. The only new IO.
3. **Existing modules** — the small edits that let a rule travel from YAML to the expansion step.
4. **`cli.py`** — the wiring, the flags, and the new `expand` command.
5. **Tests** — the `fake_http` fixture and the two new test modules.
6. **Docs** — `example.yaml`, the constitution, `AGENTS.md`.

`crawl.py` comes first because it is where every semantic argument gets settled — relative depth,
segment-boundary exclusion, truncation order — and settling those against literals is far cheaper
than settling them against a stubbed fetcher.

---

## Implementation

### 1 · `crawl.py` — the rule language, pure

No imports beyond the stdlib and `matching.normalize_url`. No IO, no database, no console.

```python
@dataclass(frozen=True)
class CrawlRule:
    raw: str                      # the manifest string, verbatim, for messages and display
    base_url: str                 # the URL the '*' hangs off, e.g. https://www.mundana.us/
    base_path: str                # its path, normalized without a trailing slash
    host: str                     # lowercased, www-stripped — comparison only
    level: int | None = None      # max segments below the base; None = unbounded
    excludes: tuple[str, ...] = ()
    max_urls: int | None = None   # None = "not stated", filled from SYNC_DISCOVERY_MAX later
```

`max_urls` is `None` rather than `100` on purpose: "the rule did not say" and "the rule said 100"
are different facts, and only the first may be overridden by configuration. `expand_entries()` fills
it, which is also why `manifest.py` needs no access to `Settings`.

`base_url` keeps the host **verbatim** (`www.` and all): it is both what gets fetched and what
reaches `source add` as the base page, so normalizing it would break the never-send-a-normalized-URL
rule. `host` carries the stripped form, and comparison uses only that.

- `parse_rule(url, *, where) -> CrawlRule | None`

  Peel the trailing `[...]` block off, then parse the remainder as a URL. Return `None` for a plain
  URL — but decide that on whether the raw string contains `/*` **before** validating anything, so
  a malformed rule reports its problem instead of being silently mistaken for a very strange URL.

  Raises `ManifestError` (exit 2) for: an unclosed or malformed `[...]`, an unknown key, a
  non-integer `level`/`max`, `level < 1`, `max < 1`, and a `*` anywhere other than the final
  segment. Every message is prefixed with the `where` string `manifest.py` already builds
  (`<file> sources[3]`), so a bad rule points at its line.

  `except` values are normalized at parse time to a host-absolute path, so `blog`, `/blog` and
  `https://www.mundana.us/blog` all become `/blog`. A bare value is relative to the *base*, so under
  `/store/*`, `except=legacy` means `/store/legacy`; a leading `/` makes it host-absolute.

- `rule_key(rule) -> str` — the key for a rule's **discovery**, not for its filtering:
  `base_url|level=…|max=…`, deliberately **without** `except`. Excludes are a pure post-filter, so
  tightening one must re-use the cached candidates rather than re-read the site. `level` and `max`
  are in the key because they bound what a *crawl* actually fetches.

- `filter_urls(rule, candidates, *, exclude_normalized) -> (list[str], int)`

  The whole meaning of a rule, in one pure function:

  1. drop anything `normalize_url()` rejects, and anything whose normalized host differs from the
     base's — `www.` equivalence is free, because `normalize_url` already strips it;
  2. drop anything whose path is not at or under `base_path`, on a segment boundary;
  3. drop anything deeper than `level` segments below the base;
  4. drop anything under an `except` subtree, again on a segment boundary — `/blog` excludes
     `/blog` and `/blog/x`, never `/blogging`;
  5. drop anything already claimed by a hand-written entry (`exclude_normalized`);
  6. deduplicate on normalized URL, keeping the first original;
  7. sort by `(relative_depth, url)` and truncate to `rule.max_urls`, returning the overflow count.

  Step 7's order is the reason `[max=10]` is useful rather than arbitrary: the ten kept URLs are the
  ten most top-level pages, and the same input yields the same ten every run.

### 2 · `discovery.py` — the HTTP seam

```python
class Discoverer:
    def __init__(self, *, timeout=30, delay=0.2, user_agent=USER_AGENT,
                 fetch=None, on_fetch=None) -> None: ...
```

`fetch` is injectable — the default is a `urllib.request` implementation, and tests pass a mapping
instead, so no test ever opens a socket. `on_fetch` receives each URL before it is requested; it is
how `-v` prints without `discovery.py` ever importing Rich, mirroring `NlmClient.on_call`.

- `discover(rule) -> (candidates, source)`
  1. `GET <origin>/robots.txt` **once**, for both its `Sitemap:` lines and the `Disallow` rules the
     crawl fallback has to honour.
  2. No `Sitemap:` directive ⇒ try `<origin>/sitemap.xml`.
  3. Parse with `xml.etree.ElementTree`, namespace-agnostically; a `<sitemapindex>` is followed
     **one level** deep (deeper nesting is vanishingly rare and unbounded recursion over a hostile
     sitemap is not a risk worth taking). `.gz` bodies are decompressed with `gzip`.
  4. No sitemap reachable ⇒ `_crawl(rule)`.
  5. Prepend `rule.base_url` to the candidates — but **only if discovery found something**. A
     sitemap that omits the root still yields it, while a site that is entirely unreachable stays
     empty and raises, instead of quietly syncing its home page alone.

- `_crawl(rule)` — breadth-first from `base_url`: same host only, `text/html` responses only,
  `<a href>` extracted with a small `html.parser` subclass, `Disallow` honoured, `delay` seconds
  between fetches, and stopping once `rule.max_urls` URLs are collected or the depth exceeds
  `rule.level`. Here the cap is a genuine fetch budget, not a post-filter.

```python
@dataclass(frozen=True)
class Expansion:
    rule: CrawlRule
    urls: list[str]
    source: str          # "sitemap" | "crawl"
    fetched_at: datetime
    matched: int         # before truncation
    dropped: int         # matched - len(urls)
    cached: bool
```

`matched` and `dropped` are **computed on every invocation**, not stored: the cache holds the raw
candidates and `filter_urls()` re-runs over them, cached or not. That is what keeps the truncation
warning honest on a cache hit and lets a changed `except=` take effect without a re-fetch.

- `expand_entries(entries, *, discoverer, conn, ttl, refresh) -> (list[ManifestEntry], list[Expansion])`

  Walks the entries in order. A plain entry passes through. A rule entry is resolved — from
  `db.get_discovery()` when a fresh cache row exists and `refresh` is false, otherwise over the
  network and then written back with `db.put_discovery()` — and replaced **in place** by one plain
  `ManifestEntry` per URL, inheriting the rule's `type` and `policy`. The `exclude_normalized` set
  passed to `filter_urls` is built from *all* hand-written entries first, which is what makes
  explicit entries win regardless of manifest order. Finally the combined list goes through the
  existing `matching.dedupe_entries()`.

  A rule resolving to zero URLs, or whose fetches all failed, raises `DiscoveryError` (exit 1).
  Truncation is never an error: it is reported through `Expansion.dropped` and printed by `cli.py`.

### 3 · Existing modules

| File | Change |
|---|---|
| `errors.py` | `DiscoveryError(SyncError)`, `exit_code = EXIT_ACTION_FAILED` |
| `models.py` | `ManifestEntry` gains `rule: CrawlRule \| None = None` |
| `manifest.py` | call `crawl.parse_rule()` per entry; reject `title:` on a rule; leave it unexpanded |
| `matching.py` | `normalize_entry()` / `dedupe_entries()` skip entries with `rule is not None` |
| `db.py` | `discovery_cache` table; `SCHEMA_VERSION` 1 → 2; `get_discovery()` / `put_discovery()` |
| `config.py` | `discovery_ttl`, `discovery_max`, `http_timeout` on `Settings` |

`matching.py`'s change is load-bearing rather than cosmetic: a rule string is not a URL, and
`normalize_url()` would raise on `/*[except=blog]`. Rule entries carry `normalized_url = ""` and are
passed through untouched until expansion replaces them.

The new table needs no migration branch — `SCHEMA` is applied with `CREATE TABLE IF NOT EXISTS`
through `executescript`, so an existing v1 database gains the table and the stamp on next open:

```sql
CREATE TABLE IF NOT EXISTS discovery_cache (
    rule_key   TEXT PRIMARY KEY,
    rule       TEXT NOT NULL,   -- the raw manifest string, for display
    urls       TEXT NOT NULL,   -- JSON array of raw candidates, pre-filter
    source     TEXT NOT NULL,   -- 'sitemap' | 'crawl'
    fetched_at TEXT NOT NULL
);
```

An expired row reads as absent rather than being deleted: the next successful fetch overwrites it,
and a failed one leaves the last known discovery where a human can still inspect it.

It deliberately has no foreign key to `notebooks`: a rule resolves to the same URLs whichever
notebook asked, so the cache is global and two notebooks sharing a rule share one fetch.

`config.py` reads `SYNC_DISCOVERY_TTL` (86400), `SYNC_DISCOVERY_MAX` (100) and `SYNC_HTTP_TIMEOUT`
(30) through the existing `_parse_int`. `SYNC_DISCOVERY_MAX` is only ever the fallback for a rule
that states no `max=`; it never clamps one that does.

### 4 · `cli.py`

- `sync` and `status`: build a `Discoverer`, call `expand_entries()` between `load_manifest()` and
  `engine.plan()`, and hand `plan()` the expanded list.
- `--refresh-discovery` on `sync`, `status` and `expand`.
- `_client()` gains a sibling `_discoverer(settings, verbose=…)`, whose `on_fetch` prints
  `GET <url>` dimmed to `err_console` — the same treatment `-v` already gives `notebooklm` argv.
- `_render_plan()` prints one line per `Expansion`
  (`rule …/*[except=blog] → 37 URLs (sitemap, cached 2h ago)`), and a warning line when
  `dropped > 0`.
- **`expand <notebook>`** — load, expand, print a table of rules and their URLs, exit 0. No
  `auth check`, no `NlmClient`, no notebook call: it reads the website and nothing else, which is
  what makes it safe to run before committing to sources sync can never remove.
- `ENV_TEMPLATE` and `.env.example` gain the three new variables, commented.

### 5 · Tests

- `tests/conftest.py` — a `fake_http` fixture: a mapping of URL → body (or a `(status, body,
  content_type)` tuple), returned as a `fetch` callable, recording every requested URL. It is to
  `Discoverer` exactly what `fake_cli` is to `NlmClient`.
- `tests/fixtures/sitemap.xml` — a scrubbed slice of the live `mundana.us` sitemap, pinning the real
  wire shape the way `source_list.json` pins the source shape.
- `tests/test_crawl.py` — the grammar (every modifier, every combination, every malformed form) and
  `filter_urls` (level boundaries, `/blog` vs `/blogging`, cross-host rejection, base-page
  inclusion, explicit-wins, and the cap in all three directions).
- `tests/test_discovery.py` — sitemap index recursion, `robots.txt` discovery, `.gz`, crawl
  fallback, the crawl stopping at `max` fetches, cache hit issuing zero requests, TTL expiry,
  `refresh=True`, and `DiscoveryError` on an empty result.
- Additions to `test_manifest.py` (rule detection, `title:` rejection, plain URLs unaffected),
  `test_db.py` (round-trip, TTL, `user_version == 2` on fresh **and** upgraded databases) and
  `test_cli.py` (`expand`, `--refresh-discovery`, and `sync --dry-run` still issuing only
  `source list`).

### 6 · Docs

`sources/example.yaml` gains a `# Web scraping rules` section covering every form, with the real
`mundana.us` numbers (276 URLs, 239 under `/blog`, 37 left). The suite already validates that file,
so the documented examples are executable rather than decorative.

`constitution/tech-stack.md`: the two new modules under *Key files*, the new hard limit, schema
`user_version` 1 → 2, the three env vars, the `expand` command, and a *Domain model* entry for the
rule grammar. `AGENTS.md`: the data-flow diagram, the fourth invariant, and a *Things that will bite
you* note on `level` being relative depth.

---

## Decisions

- **HTTP gets its own seam, and `discovery.py` is it.** The alternative — fetching from wherever it
  is convenient — was rejected outright. The one-seam-per-side-effect rule is the only reason this
  suite runs offline, and network access is exactly the kind of thing that quietly spreads across a
  codebase until nothing can be tested without it.
- **Expansion sits between the manifest and `plan()`, not inside either.** Inside `plan()` it would
  destroy purity; inside `execute()` it would give `--dry-run` a different input from a real run,
  which is the drift the "no dry-run-only path" limit exists to prevent. As a separate step,
  `engine.py` needs no change at all.
- **The inline DSL, not structured YAML keys.** `crawl:`/`depth:`/`exclude:` keys would validate
  more easily and need no string parsing, but they split one concept across four keys and make a
  plain URL and a rule look like different kinds of entry. The rule *is* a URL pattern, and keeping
  it in `url:` keeps the manifest scannable. The cost — a hand-written parser — is paid once, in a
  pure module, with exhaustive tests.
- **An inline modifier always overrides the default, in both directions.** `[max=10]` lowers the cap
  below the configured 100 just as `[max=300]` raises it above. A default that could not be lowered
  would be a floor, and a rule that says 10 and gets 100 is a lie. This generalises: any modifier
  added later inherits the same precedence.
- **Over the cap truncates with a warning; it does not fail.** The alternative — a hard
  `ManifestError` — was considered and rejected. `max` reads as a budget, and the repo already has
  the right precedent: duplicate manifest URLs warn and continue rather than blocking the run. The
  danger the cap guards against is *unbounded* growth, and truncation stops that just as well while
  still syncing something useful.
- **Truncation is shallowest-first, then alphabetical.** Arbitrary truncation would make `[max=10]`
  useless and non-reproducible. Depth order means the kept pages are the ones nearest the root,
  which is what someone capping a rule almost always wants, and it keeps churn at the deep end when
  a site grows.
- **Sitemap first, crawl second.** A crawl of `mundana.us` is 276 requests to learn what one request
  already says, and a sitemap is the site telling you what it wants indexed. The crawl exists only
  so that a site without one is not unusable.
- **Stdlib `urllib`, not `httpx`.** The common path is one request and the fallback is capped and
  cached, so there is nothing for connection pooling or concurrency to win. Four runtime
  dependencies stay four, and CI is unchanged. If a genuinely large crawl ever matters, swapping the
  injected `fetch` is a one-file change — which is the point of the seam.
- **The cache is global, not per notebook.** A rule resolves to the same URLs whoever asked. Keying
  it by notebook would multiply identical fetches for no benefit.
- **`title:` on a rule is an error, not a prefix or a template.** One title cannot name N pages, and
  inventing a templating mini-language to fix that is a larger feature than this one. Upstream
  derives per-page titles anyway.
- **`expand` is a separate command, not a `--expand-only` flag on `sync`.** It makes no notebook call
  and needs no auth, which a flag on `sync` would obscure. It is the thing you run *before* you
  trust a rule.
- **Every printed rule is `rich.markup.escape()`d.** Found in live verification, not in the tests:
  `[except=blog]` is valid Rich markup, so an unescaped rule prints as `https://…/*` with its
  modifiers silently deleted — worst in the error messages that exist to tell you what you typed
  wrong. Escaping happens at the three print sites, not by disabling markup, so the surrounding
  colour still works.

---

## Risks

- **A silent truncation reads as full coverage.** A rule capped at 100 that matched 276 has quietly
  omitted 176 pages. *Mitigation:* the warning is unconditional, states both numbers, and is
  reprinted on cached runs — the cache stores raw candidates and filtering re-runs every time, so
  `matched` is always the true count rather than a stale one.
- **Truncation order shifting under the user.** A newly published shallow page can displace a
  previously-included deeper one, and the displaced source becomes an orphan. *Mitigation:* orphans
  are reported and never deleted, so nothing is lost; the `(depth, url)` sort confines churn to the
  deep end rather than reshuffling the whole set.
- **Stale or incomplete sitemaps.** A page the sitemap omits is never added, and the tool cannot
  tell the difference between "not published" and "not listed". *Mitigation:* documented in
  `example.yaml`; a URL can always be listed by hand alongside the rule, and the explicit entry now
  takes precedence.
- **A rule silently shrinking.** Pages that disappear from a sitemap stop being declared and their
  sources become orphans. *Mitigation:* none needed — orphans are reported and never removed, which
  is the existing invariant working as designed. Worth knowing, not worth changing.
- **The crawl fallback on a hostile or infinite site** — calendars, faceted search, session ids in
  paths. *Mitigation:* the cap is a hard fetch budget, `level` bounds depth, only same-host
  `text/html` is followed, `Disallow` is honoured, and there is a delay between requests. The
  fallback only runs when no sitemap exists at all.
- **The first network access in a repo whose suite is offline by construction.** A careless test
  could reach the real internet and pass locally while failing in CI. *Mitigation:* `fetch` is
  injectable and every test injects it; the acceptance criteria require a grep proving `urllib`
  appears nowhere in `src/` but `discovery.py`.

---

## Verification

**Offline (required)**

All done 2026-08-09.

- [x] `uv run pytest` — **212 passed** (117 before), no network, no auth.
- [x] `uv run ruff check .` — clean. `ruff format` deliberately not run.
- [x] `grep -rn "urllib.request\|^import http\|socket" src/` matches only `discovery.py`.
      (`urllib.parse` is pure string work; it is used in `matching.py` and `crawl.py` by design.)
- [x] `grep -rn "^import subprocess\|^from subprocess" src/` still matches only `nlm.py`.
- [x] `PRAGMA user_version` is `2` on a fresh database **and** on a v1 database reopened after the
      bump — `test_db.py::test_a_v1_database_is_upgraded_in_place`.
- [x] Against the fake shim: `sync --dry-run` issues exactly one `source list` and no mutating call,
      with a crawl rule in the manifest.
- [x] A second `expand` within the TTL records zero requests on the `fake_http` fixture.
- [ ] The `sync-verifier` agent. **Not run** — the session's standing instruction is not to spawn
      subagents unless asked. Its two assertions are covered directly by `test_cli.py`
      (dry-run issues only `source list`) and by the `sync_events` row-count assertion.

**Live (manual, read-only — needs no NotebookLM auth)**

Run against a throwaway config in a tmp directory, never the user's `.env`, and with a fake
notebook id since `expand` makes no notebook call.

- [x] `expand mundana -v` with `https://www.mundana.us/*[except=blog]` → **37 URLs**, source
      `sitemap`, from one `robots.txt` + one `sitemap.xml` fetch.
- [x] Re-run: `(sitemap, cached 53s ago)`, zero HTTP requests.
- [x] `[level=1]` → **38** URLs (the depth-1 pages, `/blog` among them).
- [x] `[except=blog][max=10]` → **10** URLs plus `matched 37 URLs; keeping the first 10`.
- [x] This run is what caught the Rich-markup bug: the rule printed as `https://www.mundana.us/*`
      with `[except=blog]` swallowed. Fixed, and now covered by two regression tests.
- [x] Nothing was synced into a real notebook — sync never deletes, so that needs asking first.

**CI** — unchanged. No new dependency, no new secret, no new job; the existing `ruff` + `pytest`
matrix covers this feature because the feature is offline-testable by construction.

---

Progress is tracked in [`tasks.md`](tasks.md). When this lands, move 003 to "Done" in
[`../../constitution/roadmap.md`](../../constitution/roadmap.md).
