# 003 · Web crawl rules in the source manifest — Tasks

Derived from [`plan.md`](plan.md). All six groups shipped on 2026-08-09; each was green
(`uv run pytest` + `uv run ruff check .`) before the next one started. 212 tests pass offline,
up from 117.

## 1 · `crawl.py` — the rule language, pure

- [x] `CrawlRule` frozen dataclass: `raw`, `base_url`, `base_path`, `level`, `excludes`, `max_urls`.
- [x] `parse_rule(url, *, where)` — returns `None` for a plain URL; treats a string as a rule as
      soon as it contains `/*`, so a malformed one reports rather than passing as a URL.
- [x] Normalize `except` values to a host-relative `/path` at parse time, accepting `blog`, `/blog`
      and a full URL on the host.
- [x] `ManifestError` (exit 2) for: unclosed/malformed `[...]`, unknown key, non-integer value,
      `level < 1`, `max < 1`, `*` anywhere but the final segment. Messages carry the `where` prefix.
- [x] `rule_key(rule)` — canonical and order-independent.
- [x] `filter_urls(rule, candidates, *, exclude_normalized) -> (urls, dropped)` — host, base path,
      level, excludes, `exclude_normalized`, dedupe, sort `(depth, url)`, truncate to `max_urls`.
- [x] Confirm the module imports nothing that does IO.

## 2 · `discovery.py` — the HTTP seam

- [x] `Discoverer(*, timeout, delay, user_agent, fetch=None, on_fetch=None)` with an injectable
      `fetch` defaulting to `urllib.request`.
- [x] `discover(rule)` — one `robots.txt` fetch for both `Sitemap:` and `Disallow`, then
      `/sitemap.xml`, then one level of `<sitemapindex>`; `.gz` decompression; base URL prepended
      only when discovery found something, so an unreachable site raises instead of syncing one page.
- [x] `_crawl(rule)` fallback — BFS, same host, `text/html` only, `Disallow` honoured, `delay`
      between fetches, stopping at `max_urls` fetches or beyond `level`.
- [x] `Expansion` carries `matched`/`dropped`, recomputed each run (the cache holds raw
      candidates), so a cached run still warns with the true numbers.
- [x] `expand_entries(entries, *, discoverer, conn, ttl, refresh)` — cache lookup, in-place
      replacement inheriting `type`/`policy`, `exclude_normalized` built from all explicit entries
      first, closing `dedupe_entries()`.
- [x] `DiscoveryError` (exit 1) when a rule resolves to zero URLs or every fetch failed.
- [x] Confirm `discovery.py` is the only module under `src/` importing `urllib.request` /
      `http.client` / `socket`. (`urllib.parse` is pure string work and is used freely.)

## 3 · Existing modules

- [x] `errors.py`: `DiscoveryError(SyncError)` with `exit_code = EXIT_ACTION_FAILED`.
- [x] `models.py`: `ManifestEntry.rule: CrawlRule | None = None`.
- [x] `manifest.py`: call `crawl.parse_rule()` per entry; `ManifestError` when a rule carries
      `title:`; leave the rule unexpanded.
- [x] `matching.py`: `normalize_entry()` and `dedupe_entries()` skip entries with `rule is not None`.
- [x] `db.py`: `discovery_cache` table in `SCHEMA`, `SCHEMA_VERSION` 1 → 2,
      `get_discovery(conn, rule_key, ttl)` and `put_discovery(conn, rule_key, rule, urls, source,
      fetched_at)`. An expired row reads as absent but is not deleted.
- [x] `config.py`: `discovery_ttl` (`SYNC_DISCOVERY_TTL`, 86400), `discovery_max`
      (`SYNC_DISCOVERY_MAX`, 100), `http_timeout` (`SYNC_HTTP_TIMEOUT`, 30) via `_parse_int`.
- [x] Confirm `engine.py` is **not** modified and `plan()` is still pure.

## 4 · `cli.py`

- [x] `_discoverer(settings, verbose=…)` alongside `_client()`, printing `GET <url>` dimmed to
      `err_console`.
- [x] `sync` and `status`: `expand_entries()` between `load_manifest()` and `engine.plan()`.
- [x] `--refresh-discovery` on `sync`, `status` and `expand`.
- [x] `_render_plan()`: one line per `Expansion`, plus a warning line when `dropped > 0`.
- [x] `rich.markup.escape()` every printed rule and every error quoting one — square brackets are
      Rich markup, and an unescaped `[except=blog]` prints as nothing at all.
- [x] New `expand <notebook>` command — no `auth check`, no `NlmClient`, no notebook call.
- [x] `ENV_TEMPLATE` and `.env.example`: the three new variables, commented.

## 5 · Tests (all offline)

- [x] `tests/conftest.py`: `fake_http` fixture — URL → body mapping, records every requested URL.
- [x] `tests/fixtures/sitemap.xml`: scrubbed slice of the live `mundana.us` sitemap.
- [x] `tests/test_crawl.py`: grammar (every modifier, combinations, every malformed form) and
      `filter_urls` (level boundaries, `/blog` vs `/blogging`, cross-host, base-page inclusion,
      explicit-wins).
- [x] `tests/test_crawl.py`: the cap — `[max=10]` keeps the ten shallowest with `dropped == 266`,
      `[max=300]` keeps all 276 with `dropped == 0`, the default applies only when no `max=` is set.
- [x] `tests/test_discovery.py`: sitemap index recursion, `robots.txt`, `.gz`, crawl fallback, crawl
      stopping at `max` fetches, cache hit with zero requests, TTL expiry, `refresh=True`,
      `DiscoveryError` on an empty result.
- [x] `test_manifest.py`: rule detection, `title:` on a rule rejected, plain URLs unaffected.
- [x] `test_db.py`: cache round-trip, TTL boundary, `user_version == 2` on a fresh **and** an
      upgraded database.
- [x] `test_cli.py`: `expand` output, `--refresh-discovery`, `sync --dry-run` still issuing only
      `source list`.
- [x] Confirm the suite passes with no `.env`, no network and no Google auth.

## 6 · Docs and closing out

- [x] `sources/example.yaml`: a `# Web scraping rules` section covering every form, with the real
      `mundana.us` numbers.
- [x] `constitution/tech-stack.md`: the two new modules, the new hard limit, `user_version` 1 → 2,
      the three env vars, the `expand` command, a *Domain model* entry for the rule grammar.
- [x] `AGENTS.md`: data-flow diagram, the fourth invariant, and a *Things that will bite you* note on
      `level` being **relative** depth.
- [x] `README.md`: the rule syntax and the `expand` command.
- [x] Tick the acceptance criteria in [`spec.md`](spec.md) and set its status.
- [x] Move 003 to **Done** in [`../../constitution/roadmap.md`](../../constitution/roadmap.md), and
      drop "RSS / sitemap expansion" from the backlog (or narrow it to feeds only).
- [x] `engram save` the rule grammar, the relative-`level` semantics, the
      inline-overrides-the-default precedence rule, and the live `mundana.us` sitemap shape —
      always `--project notebooklm-sync`.

## Maintenance (recurring checklist)

Repeat these whenever this feature is touched again:

- [ ] `uv run pytest` and `uv run ruff check .` after any change under `src/`.
- [ ] Re-grep the two seams: `subprocess` only in `nlm.py`, `urllib.request`/`http.client`/`socket`
      only in `discovery.py`.
- [ ] If a rule's behaviour changes, re-check `sources/example.yaml` — it is documentation the test
      suite executes.
- [ ] Sitemaps drift. If `tests/fixtures/sitemap.xml` stops resembling reality, re-capture and
      scrub it.
- [ ] Update this plan's status and the roadmap when work lands.
