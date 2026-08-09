"""Resolving crawl rules over HTTP — offline, with `fetch` injected.

Nothing here opens a socket: `fake_http` is to `Discoverer` what `fake_cli` is to
`NlmClient`. A URL the test forgets to register comes back 404, so a missing stub
fails loudly instead of quietly reaching the real internet.
"""

from __future__ import annotations

import gzip
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from notebooklm_sync import db
from notebooklm_sync.crawl import parse_rule
from notebooklm_sync.discovery import (
    Discoverer,
    expand_entries,
    parse_robots,
    parse_sitemap,
    resolve_rule,
)
from notebooklm_sync.errors import DiscoveryError
from notebooklm_sync.models import ManifestEntry, SyncPolicy

BASE = "https://www.mundana.us"
ROBOTS = f"{BASE}/robots.txt"
SITEMAP = f"{BASE}/sitemap.xml"

FIXTURE = Path(__file__).parent / "fixtures" / "sitemap.xml"

PAGES = [f"{BASE}/store", f"{BASE}/nosotros", f"{BASE}/blog", f"{BASE}/blog/post-one"]


def rule(raw: str, *, default_max: int = 100):
    from dataclasses import replace

    parsed = parse_rule(raw, where="t")
    return replace(parsed, max_urls=parsed.max_urls or default_max)


def discoverer(fake_http, **kwargs) -> Discoverer:
    # delay=0: the politeness pause is real behaviour, but it must not slow the suite.
    return Discoverer(fetch=fake_http, delay=0, **kwargs)


# --- parsing helpers ----------------------------------------------------------


def test_parse_sitemap_reads_the_real_wire_shape():
    """Pins the live sitemap shape, as source_list.json pins the source shape."""
    pages, nested = parse_sitemap(FIXTURE.read_bytes())
    assert nested == []
    assert len(pages) == 14
    assert pages[0] == f"{BASE}/ebook-selectividad"
    assert sum(1 for url in pages if "/blog/" in url) == 6


def test_parse_sitemap_ignores_the_namespace_and_bad_xml():
    assert parse_sitemap(b"<urlset><url><loc>https://a.com/x</loc></url></urlset>") == (
        ["https://a.com/x"],
        [],
    )
    assert parse_sitemap(b"not xml at all") == ([], [])


def test_parse_robots_collects_sitemaps_and_our_disallows():
    sitemaps, disallowed = parse_robots(
        "Sitemap: https://a.com/sm.xml\n"
        "User-agent: *\n"
        "Disallow: /private\n"
        "User-agent: EvilBot\n"
        "Disallow: /everything\n"
        "# a comment\n",
        user_agent="notebooklm-sync (+https://example.com)",
    )
    assert sitemaps == ["https://a.com/sm.xml"]
    assert disallowed == ["/private"]


# --- discovery ----------------------------------------------------------------


def test_sitemap_is_found_via_the_conventional_path(fake_http):
    fake_http.add(ROBOTS, "", status=404)
    fake_http.add_sitemap(SITEMAP, PAGES)

    candidates, source = discoverer(fake_http).discover(rule(f"{BASE}/*"))

    assert source == "sitemap"
    assert set(PAGES) <= set(candidates)
    assert fake_http.requests == [ROBOTS, SITEMAP]


def test_robots_sitemap_directive_wins_over_the_conventional_path(fake_http):
    fake_http.add(ROBOTS, f"Sitemap: {BASE}/custom-sitemap.xml\n", content_type="text/plain")
    fake_http.add_sitemap(f"{BASE}/custom-sitemap.xml", PAGES)

    candidates, source = discoverer(fake_http).discover(rule(f"{BASE}/*"))

    assert source == "sitemap"
    assert SITEMAP not in fake_http.requests
    assert set(PAGES) <= set(candidates)


def test_sitemap_index_is_followed_one_level(fake_http):
    fake_http.add(ROBOTS, "", status=404)
    fake_http.add_sitemap_index(SITEMAP, [f"{BASE}/sm-1.xml", f"{BASE}/sm-2.xml"])
    fake_http.add_sitemap(f"{BASE}/sm-1.xml", PAGES[:2])
    fake_http.add_sitemap(f"{BASE}/sm-2.xml", PAGES[2:])

    candidates, _ = discoverer(fake_http).discover(rule(f"{BASE}/*"))

    assert set(PAGES) <= set(candidates)


def test_gzipped_sitemaps_are_decompressed(fake_http):
    fake_http.add(ROBOTS, f"Sitemap: {BASE}/sitemap.xml.gz\n", content_type="text/plain")
    body = (
        b'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + b"".join(f"<url><loc>{u}</loc></url>".encode() for u in PAGES)
        + b"</urlset>"
    )
    fake_http.add(f"{BASE}/sitemap.xml.gz", gzip.compress(body), content_type="application/gzip")

    candidates, _ = discoverer(fake_http).discover(rule(f"{BASE}/*"))

    assert set(PAGES) <= set(candidates)


def test_the_base_page_is_included_even_when_the_sitemap_omits_it(fake_http):
    fake_http.add(ROBOTS, "", status=404)
    fake_http.add_sitemap(SITEMAP, PAGES)  # no entry for the root

    candidates, _ = discoverer(fake_http).discover(rule(f"{BASE}/*"))

    assert f"{BASE}/" in candidates


# --- the crawl fallback -------------------------------------------------------


def html(*links: str) -> str:
    body = "".join(f'<a href="{href}">x</a>' for href in links)
    return f"<html><body>{body}</body></html>"


def test_crawl_fallback_runs_when_no_sitemap_exists(fake_http):
    fake_http.add(ROBOTS, "", status=404)
    fake_http.add(SITEMAP, "", status=404)
    fake_http.add(f"{BASE}/", html("/store", "/nosotros", "https://elsewhere.com/x"))
    fake_http.add(f"{BASE}/store", html("/store/legacy"))
    fake_http.add(f"{BASE}/nosotros", html())
    fake_http.add(f"{BASE}/store/legacy", html())

    candidates, source = discoverer(fake_http).discover(rule(f"{BASE}/*"))

    assert source == "crawl"
    assert f"{BASE}/store/legacy" in candidates
    assert "https://elsewhere.com/x" not in candidates


def test_crawl_honours_disallow(fake_http):
    fake_http.add(ROBOTS, "User-agent: *\nDisallow: /private\n", content_type="text/plain")
    fake_http.add(SITEMAP, "", status=404)
    fake_http.add(f"{BASE}/", html("/store", "/private/secret"))
    fake_http.add(f"{BASE}/store", html())
    fake_http.add(f"{BASE}/private/secret", html())

    candidates, _ = discoverer(fake_http).discover(rule(f"{BASE}/*"))

    assert f"{BASE}/private/secret" not in candidates
    assert f"{BASE}/private/secret" not in fake_http.requests


def test_crawl_only_follows_html(fake_http):
    fake_http.add(ROBOTS, "", status=404)
    fake_http.add(SITEMAP, "", status=404)
    fake_http.add(f"{BASE}/", html("/store", "/brochure.pdf"))
    fake_http.add(f"{BASE}/store", html())
    fake_http.add(f"{BASE}/brochure.pdf", b"%PDF-1.4", content_type="application/pdf")

    candidates, _ = discoverer(fake_http).discover(rule(f"{BASE}/*"))

    assert f"{BASE}/brochure.pdf" not in candidates


def test_the_cap_is_a_real_fetch_budget_in_the_crawl(fake_http):
    """[max=2] must stop after two pages, not crawl the site and discard the rest."""
    fake_http.add(ROBOTS, "", status=404)
    fake_http.add(SITEMAP, "", status=404)
    fake_http.add(f"{BASE}/", html(*[f"/page-{n}" for n in range(50)]))
    for n in range(50):
        fake_http.add(f"{BASE}/page-{n}", html())

    discoverer(fake_http).discover(rule(f"{BASE}/*[max=2]"))

    page_requests = [url for url in fake_http.requests if "/page-" in url]
    assert len(page_requests) <= 2


# --- resolve_rule, caching and errors -----------------------------------------


def test_resolve_reports_matches_source_and_truncation(fake_http, db_path):
    fake_http.add(ROBOTS, "", status=404)
    fake_http.add_sitemap(SITEMAP, PAGES)
    conn = db.connect(db_path)

    expansion = resolve_rule(rule(f"{BASE}/*[max=2]"), discoverer=discoverer(fake_http), conn=conn)

    assert expansion.source == "sitemap"
    assert expansion.cached is False
    assert len(expansion.urls) == 2
    assert expansion.matched == 5  # 4 sitemap pages + the base URL
    assert expansion.dropped == 3


def test_a_second_resolve_within_the_ttl_makes_no_request(fake_http, db_path):
    fake_http.add(ROBOTS, "", status=404)
    fake_http.add_sitemap(SITEMAP, PAGES)
    conn = db.connect(db_path)
    target = rule(f"{BASE}/*")

    resolve_rule(target, discoverer=discoverer(fake_http), conn=conn)
    before = len(fake_http.requests)

    second = resolve_rule(target, discoverer=discoverer(fake_http), conn=conn)

    assert len(fake_http.requests) == before
    assert second.cached is True
    assert second.urls


def test_an_expired_entry_is_refetched(fake_http, db_path):
    fake_http.add(ROBOTS, "", status=404)
    fake_http.add_sitemap(SITEMAP, PAGES)
    conn = db.connect(db_path)
    target = rule(f"{BASE}/*")

    resolve_rule(target, discoverer=discoverer(fake_http), conn=conn, ttl=0)
    before = len(fake_http.requests)
    resolve_rule(target, discoverer=discoverer(fake_http), conn=conn, ttl=0)

    assert len(fake_http.requests) > before


def test_refresh_bypasses_a_fresh_entry(fake_http, db_path):
    fake_http.add(ROBOTS, "", status=404)
    fake_http.add_sitemap(SITEMAP, PAGES)
    conn = db.connect(db_path)
    target = rule(f"{BASE}/*")

    resolve_rule(target, discoverer=discoverer(fake_http), conn=conn)
    before = len(fake_http.requests)
    again = resolve_rule(target, discoverer=discoverer(fake_http), conn=conn, refresh=True)

    assert len(fake_http.requests) > before
    assert again.cached is False


def test_tightening_except_reuses_the_cache_and_recomputes_the_warning(fake_http, db_path):
    """Excludes are a post-filter, so they must never force a re-fetch."""
    fake_http.add(ROBOTS, "", status=404)
    fake_http.add_sitemap(SITEMAP, PAGES)
    conn = db.connect(db_path)

    resolve_rule(rule(f"{BASE}/*"), discoverer=discoverer(fake_http), conn=conn)
    before = len(fake_http.requests)

    narrowed = resolve_rule(
        rule(f"{BASE}/*[except=blog]"), discoverer=discoverer(fake_http), conn=conn
    )

    assert len(fake_http.requests) == before
    assert narrowed.cached is True
    assert all("/blog" not in url for url in narrowed.urls)


def test_a_stale_row_reports_the_true_age(fake_http, db_path):
    conn = db.connect(db_path)
    old = datetime.now(UTC) - timedelta(hours=3)
    db.put_discovery(conn, "k", "raw", [f"{BASE}/x"], "sitemap", old)

    assert db.get_discovery(conn, "k", ttl=86_400)[2] == old.replace(microsecond=0)
    assert db.get_discovery(conn, "k", ttl=60) is None


def test_a_rule_that_matches_nothing_raises(fake_http, db_path):
    fake_http.add(ROBOTS, "", status=404)
    fake_http.add(SITEMAP, "", status=404)
    fake_http.add(f"{BASE}/", "", status=500)
    conn = db.connect(db_path)

    with pytest.raises(DiscoveryError) as excinfo:
        resolve_rule(rule(f"{BASE}/*"), discoverer=discoverer(fake_http), conn=conn)
    assert f"{BASE}/*" in str(excinfo.value)


def test_discovery_error_exits_one_not_two():
    """The rule parsed fine; this is a failure to reach the site, not bad config."""
    assert DiscoveryError("x").exit_code == 1


# --- expand_entries -----------------------------------------------------------


def test_expand_replaces_a_rule_in_place_and_inherits_type_and_policy(fake_http, db_path):
    fake_http.add(ROBOTS, "", status=404)
    fake_http.add_sitemap(SITEMAP, PAGES)
    conn = db.connect(db_path)

    entries = [
        ManifestEntry(url="https://example.com/first"),
        ManifestEntry(
            url=f"{BASE}/*[except=blog]",
            type="url",
            policy=SyncPolicy.OVERRIDE,
            rule=parse_rule(f"{BASE}/*[except=blog]", where="t"),
        ),
        ManifestEntry(url="https://example.com/last"),
    ]

    expanded, expansions = expand_entries(entries, discoverer=discoverer(fake_http), conn=conn)

    assert len(expansions) == 1
    urls_in_order = [e.url for e in expanded]
    assert urls_in_order[0] == "https://example.com/first"
    assert urls_in_order[-1] == "https://example.com/last"
    assert all(e.rule is None for e in expanded)

    from_rule = [e for e in expanded if "mundana" in e.url]
    assert from_rule
    assert all(e.type == "url" and e.policy is SyncPolicy.OVERRIDE for e in from_rule)
    assert all("/blog" not in e.url for e in from_rule)


def test_a_hand_written_entry_beats_the_rule_that_also_matches_it(fake_http, db_path):
    fake_http.add(ROBOTS, "", status=404)
    fake_http.add_sitemap(SITEMAP, PAGES)
    conn = db.connect(db_path)

    # The rule is listed *first*, so order alone would let it win.
    entries = [
        ManifestEntry(url=f"{BASE}/*", rule=parse_rule(f"{BASE}/*", where="t")),
        ManifestEntry(url=f"{BASE}/store", title="The Store", policy=SyncPolicy.OVERRIDE),
    ]

    expanded, _ = expand_entries(entries, discoverer=discoverer(fake_http), conn=conn)

    store_entries = [e for e in expanded if e.url.rstrip("/").endswith("/store")]
    assert len(store_entries) == 1
    assert store_entries[0].title == "The Store"
    assert store_entries[0].policy is SyncPolicy.OVERRIDE


def test_a_manifest_without_rules_is_returned_untouched(fake_http, db_path):
    conn = db.connect(db_path)
    entries = [ManifestEntry(url="https://example.com/a"), ManifestEntry(url="https://example.com/b")]

    expanded, expansions = expand_entries(entries, discoverer=discoverer(fake_http), conn=conn)

    assert expansions == []
    assert [e.url for e in expanded] == [e.url for e in entries]
    assert fake_http.requests == []


def test_an_inline_max_overrides_the_configured_default_downwards(fake_http, db_path):
    fake_http.add(ROBOTS, "", status=404)
    fake_http.add_sitemap(SITEMAP, PAGES)
    conn = db.connect(db_path)
    raw = f"{BASE}/*[max=2]"

    _, expansions = expand_entries(
        [ManifestEntry(url=raw, rule=parse_rule(raw, where="t"))],
        discoverer=discoverer(fake_http),
        conn=conn,
        default_max=100,
    )

    assert len(expansions[0].urls) == 2
    assert expansions[0].dropped == 3


def test_the_configured_default_applies_when_no_max_is_stated(fake_http, db_path):
    fake_http.add(ROBOTS, "", status=404)
    fake_http.add_sitemap(SITEMAP, PAGES)
    conn = db.connect(db_path)
    raw = f"{BASE}/*"

    _, expansions = expand_entries(
        [ManifestEntry(url=raw, rule=parse_rule(raw, where="t"))],
        discoverer=discoverer(fake_http),
        conn=conn,
        default_max=3,
    )

    assert len(expansions[0].urls) == 3
    assert expansions[0].dropped == 2
