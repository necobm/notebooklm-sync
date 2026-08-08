"""URL normalization and matching. Pure — no shim needed."""

from __future__ import annotations

import pytest

from notebooklm_sync.errors import ManifestError
from notebooklm_sync.matching import dedupe_entries, index_sources, normalize_url
from notebooklm_sync.models import ManifestEntry, RemoteSource


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://example.com", "https://example.com"),
        ("https://example.com/", "https://example.com"),
        ("https://www.example.com/a", "https://example.com/a"),
        ("HTTPS://Example.COM/a", "https://example.com/a"),
        ("https://example.com:443/a", "https://example.com/a"),
        ("http://example.com:80/a", "http://example.com/a"),
        ("https://example.com/a#section", "https://example.com/a"),
        ("https://example.com/a/", "https://example.com/a"),
    ],
)
def test_normalize_basic(raw, expected):
    assert normalize_url(raw) == expected


def test_path_case_is_preserved():
    # Hosts are case-insensitive; paths are not.
    assert normalize_url("https://example.com/CaseSensitive") == "https://example.com/CaseSensitive"


def test_tracking_params_dropped_but_real_ones_kept():
    assert (
        normalize_url("https://example.com/a?utm_source=x&id=7&fbclid=z")
        == "https://example.com/a?id=7"
    )


def test_query_order_does_not_matter():
    assert normalize_url("https://example.com/a?b=2&a=1") == normalize_url(
        "https://example.com/a?a=1&b=2"
    )


@pytest.mark.parametrize(
    "raw",
    [
        "https://youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ?t=42",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=90s",
        "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/shorts/dQw4w9WgXcQ",
        "https://youtube.com/embed/dQw4w9WgXcQ",
    ],
)
def test_youtube_shapes_collapse(raw):
    assert normalize_url(raw) == "https://youtube.com/watch?v=dQw4w9WgXcQ"


@pytest.mark.parametrize("raw", ["ftp://example.com/x", "file:///etc/passwd", "", "   "])
def test_bad_schemes_rejected(raw):
    with pytest.raises(ManifestError):
        normalize_url(raw)


def test_index_sources_first_wins_and_reports_duplicates():
    sources = [
        RemoteSource(id="a", url="https://example.com/x"),
        RemoteSource(id="b", url="https://www.example.com/x/"),  # same after normalizing
        RemoteSource(id="c", url=None),  # pasted text: unmatched, not an error
    ]
    index, duplicates = index_sources(sources)
    assert index["https://example.com/x"].id == "a"
    assert duplicates == ["https://example.com/x"]


def test_index_survives_unnormalizable_remote_url():
    # Upstream data we don't control must never abort a run.
    index, _ = index_sources([RemoteSource(id="a", url="not a url")])
    assert index == {}


def test_dedupe_entries_keeps_first():
    entries = [
        ManifestEntry(url="https://example.com/a", title="first"),
        ManifestEntry(url="https://example.com/a/", title="second"),
    ]
    deduped, duplicates = dedupe_entries(entries)
    assert len(deduped) == 1
    assert deduped[0].title == "first"
    assert duplicates == ["https://example.com/a"]
