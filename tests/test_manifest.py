"""Manifest parsing and validation."""

from __future__ import annotations

import pytest

from notebooklm_sync.errors import ManifestError
from notebooklm_sync.manifest import load_manifest, parse_manifest
from notebooklm_sync.models import SyncPolicy


def test_parses_full_entry():
    entries = parse_manifest(
        {
            "sources": [
                {
                    "url": "https://example.com/a",
                    "title": "A",
                    "type": "url",
                    "policy": "override",
                }
            ]
        }
    )
    assert entries[0].title == "A"
    assert entries[0].policy is SyncPolicy.OVERRIDE


def test_bare_string_is_shorthand_for_url():
    entries = parse_manifest({"sources": ["https://example.com/a"]})
    assert entries[0].url == "https://example.com/a"
    assert entries[0].title is None


def test_missing_sources_key_is_an_error():
    with pytest.raises(ManifestError, match="no 'sources' key"):
        parse_manifest({})


def test_sources_must_be_a_list():
    with pytest.raises(ManifestError, match="must be a list"):
        parse_manifest({"sources": {"url": "https://example.com"}})


def test_entry_without_url_is_an_error():
    with pytest.raises(ManifestError, match="missing a 'url'"):
        parse_manifest({"sources": [{"title": "no url"}]})


def test_invalid_type_is_rejected():
    with pytest.raises(ManifestError, match="invalid type"):
        parse_manifest({"sources": [{"url": "https://example.com", "type": "hologram"}]})


def test_invalid_policy_is_rejected():
    with pytest.raises(ManifestError, match="invalid policy"):
        parse_manifest({"sources": [{"url": "https://example.com", "policy": "yolo"}]})


def test_bad_scheme_is_rejected_at_parse_time():
    with pytest.raises(ManifestError, match="scheme"):
        parse_manifest({"sources": [{"url": "ftp://example.com/x"}]})


def test_duplicate_urls_are_deduped_not_fatal(capsys):
    entries = parse_manifest(
        {"sources": ["https://example.com/a", "https://www.example.com/a/"]}
    )
    assert len(entries) == 1
    assert "more than once" in capsys.readouterr().out


def test_empty_manifest_is_an_error():
    with pytest.raises(ManifestError, match="empty"):
        parse_manifest(None)


def test_load_manifest_reports_missing_file():
    with pytest.raises(ManifestError, match="not found"):
        load_manifest("/nonexistent/path/to/manifest.yaml")


def test_load_manifest_reports_bad_yaml(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("sources: [\n  - unclosed", encoding="utf-8")
    with pytest.raises(ManifestError, match="Could not parse"):
        load_manifest(path)


def test_repo_example_manifest_is_valid():
    # The shipped example must actually work, or `init` hands users a broken file.
    entries = load_manifest("sources/example.yaml")
    assert len(entries) >= 1
