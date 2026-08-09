"""The crawl-rule language: what a wildcard manifest entry *means*.

A manifest ``url:`` becomes a **crawl rule** when its path ends in ``/*``, optionally
followed by a block of ``[key=value]`` modifiers::

    https://site.com/*                          the base page + every descendant
    https://site.com/section/*[level=2]         at most 2 segments below /section
    https://site.com/*[except=blog][max=300]    minus /blog and its children, capped

This module is **pure**: it parses rules and filters candidate URLs, and it never
fetches anything. Turning a rule into candidates over HTTP is ``discovery.py``'s job,
which is the only module allowed to touch the network.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from .errors import ManifestError
from .matching import normalize_url

#: What marks a string as a rule rather than a plain URL. Checked before any strict
#: validation, so that a malformed rule reports its problem instead of being silently
#: mistaken for a (very strange) URL.
WILDCARD = "/*"

VALID_MODIFIERS = frozenset({"level", "except", "max"})


@dataclass(frozen=True)
class CrawlRule:
    """A parsed wildcard entry.

    ``max_urls`` is ``None`` when the rule did not state one, so that the caller can
    fill in the configured default. An inline ``[max=N]`` always wins over that
    default, in both directions — it is a value, not a ceiling.
    """

    #: The manifest string verbatim, for messages and display.
    raw: str
    #: The URL the ``*`` hangs off, e.g. ``https://www.mundana.us/``.
    base_url: str
    #: Its host, lowercased and ``www.``-stripped, matching ``normalize_url``.
    host: str
    #: Its path with no trailing slash; ``""`` for the site root.
    base_path: str
    level: int | None = None
    excludes: tuple[str, ...] = ()
    max_urls: int | None = None


def _segments(path: str) -> list[str]:
    return [segment for segment in path.split("/") if segment]


def _under(path: str, prefix: str) -> bool:
    """True when ``path`` is ``prefix`` or sits beneath it, on a segment boundary.

    The boundary is what keeps ``/blog`` from swallowing ``/blogging``.
    """
    if prefix == "":
        return True
    return path == prefix or path.startswith(prefix + "/")


def _clean_path(path: str) -> str:
    """Normalize a path for comparison: no trailing slash, ``""`` for the root."""
    return path.rstrip("/") if path not in ("", "/") else ""


def _split_modifiers(raw: str, *, where: str) -> tuple[str, list[str]]:
    """Peel a trailing ``[a=1][b=2]`` block off ``raw``.

    Returns ``(base_part, modifier_bodies)``. Raises on an unbalanced bracket, which
    is only reachable once we already know this string is meant to be a rule.
    """
    base_part = raw
    bodies: list[str] = []
    while base_part.endswith("]"):
        open_index = base_part.rfind("[")
        if open_index == -1:
            raise ManifestError(f"{where}: unbalanced ']' in rule {raw!r}")
        bodies.insert(0, base_part[open_index + 1 : -1])
        base_part = base_part[:open_index]
    if "[" in base_part or "]" in base_part:
        raise ManifestError(f"{where}: unbalanced bracket in rule {raw!r}")
    return base_part, bodies


def _parse_int(value: str, key: str, *, where: str, raw: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise ManifestError(
            f"{where}: {key}= must be a whole number in rule {raw!r}, got {value!r}"
        ) from None
    if number < 1:
        raise ManifestError(f"{where}: {key}= must be at least 1 in rule {raw!r}, got {number}")
    return number


def _parse_except(value: str, *, base_path: str, where: str, raw: str) -> str:
    """Normalize an ``except=`` value to a host-absolute path.

    Accepts a full URL, a ``/``-rooted path, or a path relative to the rule's base —
    so under ``https://site.com/store/*``, ``except=legacy`` means ``/store/legacy``
    while ``except=/legacy`` means ``/legacy``.
    """
    candidate = value.strip()
    if not candidate:
        raise ManifestError(f"{where}: empty except= in rule {raw!r}")

    if "://" in candidate:
        path = _clean_path(urlsplit(candidate).path)
    elif candidate.startswith("/"):
        path = _clean_path(candidate)
    else:
        path = _clean_path(f"{base_path}/{candidate}")

    if path == "":
        raise ManifestError(
            f"{where}: except= in rule {raw!r} excludes the whole site; "
            "remove the rule instead"
        )
    return path


def parse_rule(url: str, *, where: str = "<manifest>") -> CrawlRule | None:
    """Parse ``url`` as a crawl rule, or return ``None`` if it is a plain URL.

    A string is treated as a rule as soon as it contains ``/*``; from that point on
    every problem is reported rather than shrugged off, so a typo in a rule can never
    be mistaken for an ordinary URL.
    """
    raw = (url or "").strip()
    if WILDCARD not in raw:
        return None

    base_part, bodies = _split_modifiers(raw, where=where)
    parts = urlsplit(base_part)

    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise ManifestError(f"{where}: rule {raw!r} must be http(s), got {scheme or '(none)'!r}")
    host = (parts.hostname or "").lower().removeprefix("www.")
    if not host:
        raise ManifestError(f"{where}: rule {raw!r} has no host")
    if parts.query or parts.fragment:
        raise ManifestError(f"{where}: rule {raw!r} may not carry a query string or fragment")

    path = parts.path
    if not path.endswith(WILDCARD) or path.count("*") != 1:
        raise ManifestError(
            f"{where}: rule {raw!r} may only use '*' as its final path segment, "
            "as in https://site.com/section/*"
        )
    base_path = _clean_path(path[: -len(WILDCARD)])

    # The netloc is kept verbatim rather than normalized: base_url is both what gets
    # fetched and what reaches `source add` as the base page, and dropping a `www.`
    # the user wrote would violate the never-send-a-normalized-URL rule. `host` above
    # is the stripped form, and comparison uses only that.
    base_url = urlunsplit((scheme, parts.netloc.lower(), base_path or "/", "", ""))

    level: int | None = None
    max_urls: int | None = None
    excludes: list[str] = []

    for body in bodies:
        key, separator, value = body.partition("=")
        key = key.strip().lower()
        if not separator:
            raise ManifestError(f"{where}: modifier [{body}] in rule {raw!r} needs a key=value")
        if key not in VALID_MODIFIERS:
            valid = ", ".join(sorted(VALID_MODIFIERS))
            raise ManifestError(
                f"{where}: unknown modifier {key!r} in rule {raw!r}. Valid keys: {valid}"
            )
        if key == "level":
            level = _parse_int(value.strip(), "level", where=where, raw=raw)
        elif key == "max":
            max_urls = _parse_int(value.strip(), "max", where=where, raw=raw)
        else:
            excludes.append(_parse_except(value, base_path=base_path, where=where, raw=raw))

    return CrawlRule(
        raw=raw,
        base_url=base_url,
        host=host,
        base_path=base_path,
        level=level,
        excludes=tuple(dict.fromkeys(excludes)),
        max_urls=max_urls,
    )


def rule_key(rule: CrawlRule) -> str:
    """The cache key for ``rule``'s *discovery*, not for its filtering.

    Deliberately covers only what bounds the fetch — the base URL, ``level`` and
    ``max`` — and **not** ``except``. Excludes are a pure post-filter, so tightening
    one re-uses the cached candidates instead of re-fetching the site.
    """
    level = "*" if rule.level is None else rule.level
    cap = "*" if rule.max_urls is None else rule.max_urls
    return f"{rule.base_url}|level={level}|max={cap}"


def relative_depth(rule: CrawlRule, path: str) -> int:
    """Segments ``path`` sits below the rule's base. The base itself is 0."""
    return len(_segments(_clean_path(path))) - len(_segments(rule.base_path))


def in_scope(rule: CrawlRule, url: str) -> bool:
    """True when ``url`` is on the rule's host, under its base, and within ``level``.

    Used both to filter a sitemap and to decide whether the crawl fallback should
    follow a link, so the two paths agree by construction.
    """
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https"):
        return False
    host = (parts.hostname or "").lower().removeprefix("www.")
    if host != rule.host:
        return False
    path = _clean_path(parts.path)
    if not _under(path, rule.base_path):
        return False
    return not (rule.level is not None and relative_depth(rule, path) > rule.level)


def filter_urls(
    rule: CrawlRule,
    candidates: list[str],
    *,
    exclude_normalized: frozenset[str] | set[str] = frozenset(),
) -> tuple[list[str], int]:
    """Reduce discovered ``candidates`` to the URLs ``rule`` actually declares.

    Returns ``(urls, dropped)``, where ``dropped`` counts what the cap left behind —
    a warning for the caller to print, never an error. ``urls`` holds the **original**
    URLs, since those are what ``source add`` must receive.

    Ordering is ``(depth, normalized url)``: shallowest first. That is what makes a
    cap useful rather than arbitrary — ``[max=10]`` keeps the ten most top-level
    pages, and the same input yields the same ten on every run.
    """
    seen: set[str] = set()
    kept: list[tuple[int, str, str]] = []

    for candidate in candidates:
        try:
            normalized = normalize_url(candidate)
        except ManifestError:
            continue
        if not in_scope(rule, candidate):
            continue
        path = _clean_path(urlsplit(candidate).path)
        if any(_under(path, excluded) for excluded in rule.excludes):
            continue
        if normalized in exclude_normalized or normalized in seen:
            continue
        seen.add(normalized)
        kept.append((relative_depth(rule, path), normalized, candidate))

    kept.sort(key=lambda item: (item[0], item[1]))

    dropped = 0
    if rule.max_urls is not None and len(kept) > rule.max_urls:
        dropped = len(kept) - rule.max_urls
        kept = kept[: rule.max_urls]

    return [candidate for _, _, candidate in kept], dropped
