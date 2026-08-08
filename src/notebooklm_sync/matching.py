"""URL normalization and source matching.

``Source.url`` is the only stable identity NotebookLM gives us, so matching is by
normalized URL. Normalization is for *comparison only* — the original URL is always
what gets sent to ``notebooklm source add``.

The rule list is documented in the sync-engine skill; keep the two in step.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .errors import ManifestError
from .models import ManifestEntry, RemoteSource

#: Query parameters dropped before comparison — they identify the click, not the page.
TRACKING_PARAMS = frozenset({"fbclid", "gclid", "mc_eid", "msclkid", "ref", "ref_src"})
TRACKING_PREFIXES = ("utm_",)

DEFAULT_PORTS = {"http": "80", "https": "443"}

_YOUTUBE_HOSTS = {"youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
_YOUTUBE_PATH_ID = re.compile(r"^/(?:shorts|embed|v|live)/([A-Za-z0-9_-]{6,})")


def _is_tracking(key: str) -> bool:
    lowered = key.lower()
    return lowered in TRACKING_PARAMS or lowered.startswith(TRACKING_PREFIXES)


def _youtube_video_id(host: str, path: str, query: list[tuple[str, str]]) -> str | None:
    """Return the video ID for any recognized YouTube URL shape, else None."""
    if host == "youtu.be":
        candidate = path.lstrip("/").split("/")[0]
        return candidate or None
    match = _YOUTUBE_PATH_ID.match(path)
    if match:
        return match.group(1)
    if path == "/watch":
        for key, value in query:
            if key == "v" and value:
                return value
    return None


def normalize_url(url: str) -> str:
    """Return a canonical form of ``url`` for equality comparison.

    Raises ``ManifestError`` for anything that is not http(s) — upstream rejects
    those schemes anyway, and catching it here gives a better message.
    """
    raw = (url or "").strip()
    if not raw:
        raise ManifestError("Empty URL")

    parts = urlsplit(raw)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise ManifestError(f"Unsupported URL scheme {scheme or '(none)'!r}: {url}")

    host = (parts.hostname or "").lower()
    if not host:
        raise ManifestError(f"URL has no host: {url}")
    host = host.removeprefix("www.")

    port = parts.port
    netloc = host if port is None or str(port) == DEFAULT_PORTS.get(scheme) else f"{host}:{port}"

    # Preserve empty values (?a=) so they don't silently vanish from the key.
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _is_tracking(k)]

    video_id = _youtube_video_id(host, parts.path, query)
    if video_id:
        # Collapse every YouTube URL shape onto one canonical form so short links,
        # /shorts, embeds and ?t= timestamps all match the same existing source.
        return f"https://youtube.com/watch?v={video_id}"

    # Case matters in paths, so only the host was lowercased above.
    path = parts.path.rstrip("/") if parts.path not in ("", "/") else ""

    return urlunsplit((scheme, netloc, path, urlencode(sorted(query)), ""))


def normalize_entry(entry: ManifestEntry) -> ManifestEntry:
    """Return ``entry`` with ``normalized_url`` populated."""
    from dataclasses import replace

    return replace(entry, normalized_url=normalize_url(entry.url))


def normalize_source(source: RemoteSource) -> RemoteSource:
    """Return ``source`` with ``normalized_url`` populated where possible.

    Sources without a URL (pasted text, uploaded files) simply stay unmatched, as do
    any whose URL we cannot normalize — those are upstream's data, not the user's
    input, so a bad one must not abort the run.
    """
    from dataclasses import replace

    if not source.url:
        return source
    try:
        return replace(source, normalized_url=normalize_url(source.url))
    except ManifestError:
        return source


def index_sources(sources: list[RemoteSource]) -> tuple[dict[str, RemoteSource], list[str]]:
    """Index sources by normalized URL.

    Returns ``(index, duplicates)``. The **first** source wins for a given normalized
    URL; later ones are reported as duplicates rather than silently dropped.
    """
    index: dict[str, RemoteSource] = {}
    duplicates: list[str] = []
    for source in sources:
        normalized = normalize_source(source).normalized_url
        if not normalized:
            continue
        if normalized in index:
            duplicates.append(normalized)
        else:
            index[normalized] = normalize_source(source)
    return index, duplicates


def dedupe_entries(entries: list[ManifestEntry]) -> tuple[list[ManifestEntry], list[str]]:
    """Drop manifest entries that repeat a normalized URL, keeping the first."""
    seen: dict[str, ManifestEntry] = {}
    duplicates: list[str] = []
    for entry in entries:
        normalized = normalize_entry(entry)
        if normalized.normalized_url in seen:
            duplicates.append(normalized.normalized_url)
            continue
        seen[normalized.normalized_url] = normalized
    return list(seen.values()), duplicates
