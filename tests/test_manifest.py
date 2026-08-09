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


# -- crawl rules ---------------------------------------------------------------


def test_a_wildcard_url_becomes_a_rule_not_a_source():
    entries = parse_manifest({"sources": ["https://www.mundana.us/*[except=blog]"]})
    assert entries[0].rule is not None
    assert entries[0].rule.excludes == ("/blog",)
    # It is a declaration, not a URL: nothing normalizes it until it is expanded.
    assert entries[0].normalized_url == ""


def test_a_rule_works_in_the_mapping_form_and_inherits_type_and_policy():
    entries = parse_manifest(
        {"sources": [{"url": "https://site.com/*", "type": "url", "policy": "override"}]}
    )
    assert entries[0].rule is not None
    assert entries[0].type == "url"
    assert entries[0].policy is SyncPolicy.OVERRIDE


def test_a_rule_may_not_carry_a_title():
    with pytest.raises(ManifestError, match="cannot carry a 'title'"):
        parse_manifest({"sources": [{"url": "https://site.com/*", "title": "Everything"}]})


def test_a_malformed_rule_is_a_manifest_error_not_a_url():
    with pytest.raises(ManifestError, match="unknown modifier"):
        parse_manifest({"sources": ["https://site.com/*[depth=2]"]})


def test_plain_urls_still_have_no_rule():
    entries = parse_manifest({"sources": ["https://example.com/a"]})
    assert entries[0].rule is None
    assert entries[0].normalized_url == "https://example.com/a"


def test_rules_are_not_deduped_against_each_other_before_expansion():
    entries = parse_manifest(
        {"sources": ["https://site.com/*[except=a]", "https://site.com/*[except=b]"]}
    )
    assert len(entries) == 2
