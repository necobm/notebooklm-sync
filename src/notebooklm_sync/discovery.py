"""Resolving a crawl rule into URLs — **the single HTTP boundary of this package**.

No other module under ``src/`` may open a network connection. That is the same
argument that keeps ``subprocess`` inside ``nlm.py``: one seam per kind of side
effect is what lets the whole test suite run offline, with ``fetch`` swapped for a
mapping of canned responses.

Resolution order is sitemap first, crawl second. A sitemap is the site telling us
what it publishes — on a 276-page site that is one request instead of 276 — so the
HTML crawl exists only for sites that publish no sitemap at all.
"""

from __future__ import annotations

import gzip
import sqlite3
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

from . import db
from .config import DEFAULT_DISCOVERY_MAX, DEFAULT_DISCOVERY_TTL, DEFAULT_HTTP_TIMEOUT
from .crawl import CrawlRule, filter_urls, in_scope, rule_key
from .errors import DiscoveryError, ManifestError
from .matching import dedupe_entries, normalize_url
from .models import ManifestEntry

USER_AGENT = "notebooklm-sync (+https://github.com/nestor/notebooklm-sync)"

DEFAULT_CRAWL_DELAY = 0.2

#: How deep a ``<sitemapindex>`` is followed. One level covers every sitemap shape
#: seen in the wild; unbounded recursion over a hostile sitemap is not worth the risk.
SITEMAP_INDEX_DEPTH = 1


@dataclass(frozen=True)
class FetchResult:
    """One HTTP response, reduced to what discovery actually needs."""

    url: str
    status: int
    body: bytes = b""
    content_type: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def is_html(self) -> bool:
        return "html" in self.content_type.lower()


@dataclass(frozen=True)
class Expansion:
    """What one rule resolved to, and how."""

    rule: CrawlRule
    urls: list[str]
    #: ``"sitemap"`` or ``"crawl"``.
    source: str
    fetched_at: datetime
    #: How many URLs matched before the cap was applied.
    matched: int
    #: How many the cap left behind. Non-zero means a warning is owed.
    dropped: int
    cached: bool = False


class _LinkParser(HTMLParser):
    """Collect ``href`` values from ``<a>`` tags. Deliberately not a DOM."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        for name, value in attrs:
            if name == "href" and value:
                self.hrefs.append(value)


def _local(tag: str) -> str:
    """Strip the XML namespace, so sitemap parsing does not depend on one."""
    return tag.rsplit("}", 1)[-1]


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


def _maybe_gunzip(body: bytes) -> bytes:
    if body.startswith(b"\x1f\x8b"):
        try:
            return gzip.decompress(body)
        except OSError:
            return body
    return body


def parse_robots(body: str, *, user_agent: str) -> tuple[list[str], list[str]]:
    """Return ``(sitemap_urls, disallowed_prefixes)`` from a robots.txt body.

    ``Sitemap:`` is a site-wide directive and is collected regardless of grouping;
    ``Disallow:`` is only collected for groups that apply to us.
    """
    sitemaps: list[str] = []
    disallowed: list[str] = []
    token = user_agent.split("/")[0].split()[0].lower()
    applies = False

    for line in body.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        key, separator, value = line.partition(":")
        if not separator:
            continue
        key, value = key.strip().lower(), value.strip()
        if key == "sitemap" and value:
            sitemaps.append(value)
        elif key == "user-agent":
            applies = value == "*" or value.lower() in (token, user_agent.lower())
        elif key == "disallow" and applies and value:
            disallowed.append(value)
    return sitemaps, disallowed


def parse_sitemap(body: bytes) -> tuple[list[str], list[str]]:
    """Return ``(page_urls, nested_sitemap_urls)`` from a sitemap document."""
    try:
        root = ET.fromstring(_maybe_gunzip(body))
    except ET.ParseError:
        return [], []
    locations = [
        element.text.strip()
        for element in root.iter()
        if _local(element.tag) == "loc" and element.text and element.text.strip()
    ]
    if _local(root.tag) == "sitemapindex":
        return [], locations
    return locations, []


def extract_links(body: bytes, *, base_url: str) -> list[str]:
    """Absolute ``<a href>`` targets found in ``body``."""
    parser = _LinkParser()
    try:
        parser.feed(body.decode("utf-8", errors="replace"))
    except (AssertionError, ValueError):
        return []
    links: list[str] = []
    for href in parser.hrefs:
        if href.startswith(("#", "mailto:", "javascript:", "tel:")):
            continue
        links.append(urljoin(base_url, href))
    return links


def urllib_fetch(url: str, *, timeout: int, user_agent: str) -> FetchResult:
    """The default fetcher. The only function in this package that opens a socket."""
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return FetchResult(
                url=url,
                status=int(response.status or 0),
                body=response.read(),
                content_type=response.headers.get("Content-Type", ""),
            )
    except urllib.error.HTTPError as exc:
        return FetchResult(url=url, status=int(exc.code))
    except (urllib.error.URLError, OSError, ValueError):
        # A failed fetch is data, not an exception: discovery tries several URLs and
        # only the empty end result is worth raising about.
        return FetchResult(url=url, status=0)


class Discoverer:
    """Turns a :class:`CrawlRule` into candidate URLs over HTTP.

    ``fetch`` is injectable so tests never open a socket, and ``on_fetch`` reports
    each request to whoever asked — which is how ``-v`` prints without this module
    ever importing Rich, exactly as ``NlmClient.on_call`` does for the CLI argv.
    """

    def __init__(
        self,
        *,
        timeout: int = DEFAULT_HTTP_TIMEOUT,
        delay: float = DEFAULT_CRAWL_DELAY,
        user_agent: str = USER_AGENT,
        fetch: Callable[[str], FetchResult] | None = None,
        on_fetch: Callable[[str], None] | None = None,
    ) -> None:
        self.timeout = timeout
        self.delay = delay
        self.user_agent = user_agent
        self.on_fetch = on_fetch
        self._fetch = fetch or (
            lambda url: urllib_fetch(url, timeout=self.timeout, user_agent=self.user_agent)
        )
        self.requests: list[str] = []

    def _get(self, url: str) -> FetchResult:
        self.requests.append(url)
        if self.on_fetch:
            self.on_fetch(url)
        return self._fetch(url)

    def discover(self, rule: CrawlRule) -> tuple[list[str], str]:
        """Return ``(candidate_urls, source)`` for ``rule``.

        ``robots.txt`` is read once, for both its ``Sitemap:`` lines and the
        ``Disallow:`` rules the crawl fallback has to honour.

        The base URL is added whenever discovery found *anything*, so a sitemap that
        omits the root still yields it — the user declared that page by writing the
        rule. When nothing at all was reachable the result stays empty, so a site
        that is down raises instead of quietly syncing its home page alone.
        """
        origin = _origin(rule.base_url)
        robots = self._get(f"{origin}/robots.txt")
        sitemap_urls: list[str] = []
        disallowed: list[str] = []
        if robots.ok:
            sitemap_urls, disallowed = parse_robots(
                robots.body.decode("utf-8", errors="replace"), user_agent=self.user_agent
            )

        candidates = self._from_sitemaps(sitemap_urls or [f"{origin}/sitemap.xml"])
        source = "sitemap"
        if not candidates:
            candidates = self._crawl(rule, disallowed)
            source = "crawl"
        if not candidates:
            return [], source
        return [rule.base_url, *candidates], source

    def _from_sitemaps(self, sitemap_urls: list[str]) -> list[str]:
        pages: list[str] = []
        pending = deque((url, 0) for url in sitemap_urls)
        visited: set[str] = set()
        while pending:
            url, depth = pending.popleft()
            if url in visited:
                continue
            visited.add(url)
            response = self._get(url)
            if not response.ok:
                continue
            found, nested = parse_sitemap(response.body)
            pages.extend(found)
            if depth < SITEMAP_INDEX_DEPTH:
                pending.extend((child, depth + 1) for child in nested)
        return pages

    def _crawl(self, rule: CrawlRule, disallowed: list[str]) -> list[str]:
        """Breadth-first over same-host ``<a href>`` links.

        The cap is a real fetch budget here, not a post-filter: ``[max=10]`` stops
        after ten pages rather than crawling a site and discarding the rest.
        """
        budget = rule.max_urls or DEFAULT_DISCOVERY_MAX

        queue: deque[str] = deque([rule.base_url])
        queued = {_safe_normalize(rule.base_url)}
        found: list[str] = []
        fetches = 0

        while queue and len(found) < budget and fetches < budget:
            url = queue.popleft()
            if any(urlsplit(url).path.startswith(prefix) for prefix in disallowed):
                continue
            if fetches and self.delay > 0:
                time.sleep(self.delay)
            response = self._get(url)
            fetches += 1
            if not response.ok or not response.is_html:
                continue
            found.append(url)
            for link in extract_links(response.body, base_url=url):
                if not in_scope(rule, link):
                    continue
                key = _safe_normalize(link)
                if not key or key in queued:
                    continue
                queued.add(key)
                queue.append(link)
        return found


def _safe_normalize(url: str) -> str:
    try:
        return normalize_url(url)
    except ManifestError:
        return ""


def resolve_rule(
    rule: CrawlRule,
    *,
    discoverer: Discoverer,
    conn: sqlite3.Connection | None = None,
    ttl: int = DEFAULT_DISCOVERY_TTL,
    refresh: bool = False,
    exclude_normalized: frozenset[str] | set[str] = frozenset(),
) -> Expansion:
    """Resolve one rule, using the cache when it is fresh enough.

    Filtering always runs on the candidates, cached or not, so tightening an
    ``except=`` takes effect immediately and the truncation warning stays accurate on
    a cache hit.
    """
    key = rule_key(rule)
    cached = None if (conn is None or refresh) else db.get_discovery(conn, key, ttl)

    if cached is not None:
        candidates, source, fetched_at = cached
        from_cache = True
    else:
        candidates, source = discoverer.discover(rule)
        fetched_at = datetime.now(UTC)
        from_cache = False
        if conn is not None and candidates:
            db.put_discovery(conn, key, rule.raw, candidates, source, fetched_at)

    urls, dropped = filter_urls(rule, candidates, exclude_normalized=exclude_normalized)
    if not urls:
        raise DiscoveryError(
            f"{rule.raw} matched no pages. Check the URL, loosen [level=]/[except=], "
            "or list the pages explicitly."
        )
    return Expansion(
        rule=rule,
        urls=urls,
        source=source,
        fetched_at=fetched_at,
        matched=len(urls) + dropped,
        dropped=dropped,
        cached=from_cache,
    )


def expand_entries(
    entries: list[ManifestEntry],
    *,
    discoverer: Discoverer,
    conn: sqlite3.Connection | None = None,
    ttl: int = DEFAULT_DISCOVERY_TTL,
    refresh: bool = False,
    default_max: int = DEFAULT_DISCOVERY_MAX,
) -> tuple[list[ManifestEntry], list[Expansion]]:
    """Replace every rule entry with the plain entries it declares.

    Runs between ``load_manifest()`` and ``engine.plan()``, so ``plan()`` keeps
    receiving a flat list of plain entries and stays a pure function.
    """
    explicit = {
        normalized
        for entry in entries
        if entry.rule is None and (normalized := _safe_normalize(entry.url))
    }

    expanded: list[ManifestEntry] = []
    expansions: list[Expansion] = []

    for entry in entries:
        if entry.rule is None:
            expanded.append(entry)
            continue

        # An inline [max=N] always wins; the configured default only fills a gap.
        rule = replace(entry.rule, max_urls=entry.rule.max_urls or default_max)
        expansion = resolve_rule(
            rule,
            discoverer=discoverer,
            conn=conn,
            ttl=ttl,
            refresh=refresh,
            exclude_normalized=explicit,
        )
        expansions.append(expansion)
        expanded.extend(
            ManifestEntry(url=url, type=entry.type, policy=entry.policy)
            for url in expansion.urls
        )

    deduped, _ = dedupe_entries(expanded)
    return deduped, expansions
