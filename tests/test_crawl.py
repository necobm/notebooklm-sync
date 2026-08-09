"""The crawl-rule language. Pure: no network, no database, no fixtures needed."""

from __future__ import annotations

import pytest

from notebooklm_sync.crawl import filter_urls, parse_rule, rule_key
from notebooklm_sync.errors import ManifestError

BASE = "https://www.mundana.us"

#: Shaped like the real sitemap: top-level pages plus a /blog subtree.
SITE = [
    f"{BASE}/",
    f"{BASE}/store",
    f"{BASE}/nosotros",
    f"{BASE}/curso-pce",
    f"{BASE}/blog",
    f"{BASE}/blog/efecto-zeigarnik",
    f"{BASE}/blog/educacion-permanente",
    f"{BASE}/store/legacy/old-thing",
    f"{BASE}/blogging",
]


def resolved(raw: str, *, default_max: int = 100):
    """Parse ``raw`` and fill the configured default the way expand_entries does."""
    from dataclasses import replace

    rule = parse_rule(raw, where="t")
    assert rule is not None
    return replace(rule, max_urls=rule.max_urls or default_max)


def urls(raw: str, candidates=None, *, default_max: int = 100, exclude=frozenset()):
    kept, dropped = filter_urls(
        resolved(raw, default_max=default_max),
        list(candidates if candidates is not None else SITE),
        exclude_normalized=exclude,
    )
    return kept, dropped


# --- what is and is not a rule ------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/some-article",
        "https://example.com/",
        "https://youtu.be/dQw4w9WgXcQ?t=42",
        "https://example.com/a-star-is-born",
    ],
)
def test_plain_urls_are_not_rules(url):
    assert parse_rule(url, where="t") is None


def test_wildcard_at_root_is_a_rule():
    rule = parse_rule("https://www.mundana.us/*", where="t")
    assert rule is not None
    assert rule.base_url == "https://www.mundana.us/"
    assert rule.base_path == ""
    assert rule.host == "mundana.us"
    assert rule.level is None
    assert rule.excludes == ()
    # Unstated, so the configured default fills it in later — not hardcoded here.
    assert rule.max_urls is None


def test_wildcard_under_a_section_is_a_rule():
    rule = parse_rule("https://www.mundana.us/store/*", where="t")
    assert rule.base_url == "https://www.mundana.us/store"
    assert rule.base_path == "/store"


def test_base_url_keeps_the_hosts_www():
    """It is fetched *and* sent to `source add`, so it must not be normalized."""
    rule = parse_rule("https://www.mundana.us/*", where="t")
    assert rule.base_url.startswith("https://www.mundana.us")
    assert rule.host == "mundana.us"


# --- modifiers ----------------------------------------------------------------


def test_all_modifiers_parse_in_any_order():
    a = parse_rule("https://site.com/*[level=2][except=blog][max=300]", where="t")
    b = parse_rule("https://site.com/*[max=300][except=blog][level=2]", where="t")
    assert (a.level, a.max_urls, a.excludes) == (2, 300, ("/blog",))
    assert (b.level, b.max_urls, b.excludes) == (2, 300, ("/blog",))


def test_except_repeats():
    rule = parse_rule("https://site.com/*[except=blog][except=store/legacy]", where="t")
    assert rule.excludes == ("/blog", "/store/legacy")


@pytest.mark.parametrize(
    "value",
    ["blog", "/blog", "https://www.mundana.us/blog", "https://www.mundana.us/blog/"],
)
def test_except_accepts_paths_and_full_urls(value):
    rule = parse_rule(f"https://www.mundana.us/*[except={value}]", where="t")
    assert rule.excludes == ("/blog",)


def test_except_is_relative_to_the_base_unless_rooted():
    relative = parse_rule("https://site.com/store/*[except=legacy]", where="t")
    rooted = parse_rule("https://site.com/store/*[except=/legacy]", where="t")
    assert relative.excludes == ("/store/legacy",)
    assert rooted.excludes == ("/legacy",)


@pytest.mark.parametrize(
    "raw",
    [
        "https://site.com/*[level=2",
        "https://site.com/*[level]",
        "https://site.com/*[depth=2]",
        "https://site.com/*[level=two]",
        "https://site.com/*[level=0]",
        "https://site.com/*[level=-1]",
        "https://site.com/*[max=0]",
        "https://site.com/*[except=]",
        "https://site.com/*[except=/]",
        "https://site.com/*/more",
        "https://site.com/*/*",
        "ftp://site.com/*",
        "https://site.com/*?q=1",
    ],
)
def test_malformed_rules_raise(raw):
    with pytest.raises(ManifestError):
        parse_rule(raw, where="manifest.yaml sources[3]")


def test_error_messages_point_at_the_entry():
    with pytest.raises(ManifestError) as excinfo:
        parse_rule("https://site.com/*[depth=2]", where="manifest.yaml sources[3]")
    assert "manifest.yaml sources[3]" in str(excinfo.value)


# --- the cache key ------------------------------------------------------------


def test_rule_key_is_order_independent():
    a = resolved("https://site.com/*[level=2][max=300]")
    b = resolved("https://site.com/*[max=300][level=2]")
    assert rule_key(a) == rule_key(b)


def test_rule_key_ignores_except():
    """Excludes are a post-filter, so tightening one must not force a re-fetch."""
    plain = resolved("https://site.com/*")
    excluded = resolved("https://site.com/*[except=blog]")
    assert rule_key(plain) == rule_key(excluded)


def test_rule_key_separates_level_and_max():
    assert rule_key(resolved("https://site.com/*[level=1]")) != rule_key(
        resolved("https://site.com/*[level=2]")
    )
    assert rule_key(resolved("https://site.com/*[max=10]")) != rule_key(
        resolved("https://site.com/*[max=20]")
    )


# --- filtering ----------------------------------------------------------------


def test_wildcard_takes_the_base_page_and_every_descendant():
    kept, dropped = urls(f"{BASE}/*")
    assert dropped == 0
    assert f"{BASE}/" in kept
    assert f"{BASE}/blog/efecto-zeigarnik" in kept
    assert len(kept) == len(SITE)


def test_level_counts_segments_below_the_base():
    kept, _ = urls(f"{BASE}/*[level=1]")
    assert f"{BASE}/store" in kept
    assert f"{BASE}/blog" in kept
    assert f"{BASE}/blog/efecto-zeigarnik" not in kept


def test_level_is_relative_to_a_nested_base():
    kept, _ = urls(f"{BASE}/store/*[level=1]")
    assert kept == [f"{BASE}/store"]  # /store/legacy/old-thing is 2 below /store


def test_except_removes_a_subtree_on_segment_boundaries():
    kept, _ = urls(f"{BASE}/*[except=blog]")
    assert f"{BASE}/blog" not in kept
    assert f"{BASE}/blog/efecto-zeigarnik" not in kept
    # The boundary is what keeps /blog from swallowing /blogging.
    assert f"{BASE}/blogging" in kept


def test_urls_off_the_host_or_outside_the_base_are_discarded():
    candidates = [*SITE, "https://elsewhere.com/page", "https://www.mundana.us/../x"]
    kept, _ = urls(f"{BASE}/store/*", candidates)
    assert kept == [f"{BASE}/store", f"{BASE}/store/legacy/old-thing"]


def test_www_and_non_www_are_the_same_host():
    kept, _ = urls(f"{BASE}/*[level=1]", ["https://mundana.us/store"])
    assert kept == ["https://mundana.us/store"]


def test_original_urls_are_returned_not_normalized_ones():
    kept, _ = urls(f"{BASE}/*[level=1]", [f"{BASE}/store?utm_source=x"])
    assert kept == [f"{BASE}/store?utm_source=x"]


def test_duplicates_collapse_on_normalized_url():
    kept, _ = urls(
        f"{BASE}/*[level=1]",
        [f"{BASE}/store", f"{BASE}/store/", f"{BASE}/store?utm_source=news"],
    )
    assert kept == [f"{BASE}/store"]


def test_explicitly_listed_urls_are_left_to_the_hand_written_entry():
    from notebooklm_sync.matching import normalize_url

    kept, _ = urls(f"{BASE}/*[level=1]", exclude={normalize_url(f"{BASE}/store")})
    assert f"{BASE}/store" not in kept
    assert f"{BASE}/nosotros" in kept


# --- the cap ------------------------------------------------------------------


def test_default_cap_applies_only_when_max_is_absent():
    kept, dropped = urls(f"{BASE}/*", default_max=3)
    assert len(kept) == 3
    assert dropped == len(SITE) - 3


def test_inline_max_lowers_the_default():
    kept, dropped = urls(f"{BASE}/*[max=2]", default_max=100)
    assert len(kept) == 2
    assert dropped == len(SITE) - 2


def test_inline_max_raises_the_default():
    kept, dropped = urls(f"{BASE}/*[max=50]", default_max=3)
    assert len(kept) == len(SITE)
    assert dropped == 0


def test_truncation_keeps_the_shallowest_first_and_is_stable():
    kept, _ = urls(f"{BASE}/*[max=3]")
    assert kept[0] == f"{BASE}/"  # the base page is depth 0
    assert all("/blog/" not in url for url in kept)
    assert urls(f"{BASE}/*[max=3]")[0] == kept


def test_a_rule_under_its_cap_drops_nothing():
    kept, dropped = urls(f"{BASE}/*[except=blog][max=50]")
    assert dropped == 0
    assert len(kept) == 6  # 9 candidates minus /blog and its two posts
