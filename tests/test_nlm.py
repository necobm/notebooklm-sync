"""The subprocess adapter, exercised against the fake CLI on PATH.

These tests cover the upstream quirks that are easy to get wrong: in-band error
envelopes, the three `source wait` exit codes, and `auth check --test`.
"""

from __future__ import annotations

import pytest

from notebooklm_sync.errors import AuthError, NlmError
from notebooklm_sync.models import WaitStatus
from notebooklm_sync.nlm import NlmClient


def test_list_sources_parses_rows(fake_cli):
    fake_cli.scenario(
        {
            "source list": {
                "sources": [
                    {
                        "id": "s1",
                        "url": "https://example.com/a",
                        "title": "A",
                        "kind": "web_page",
                        "status": "READY",
                    }
                ]
            }
        }
    )
    sources = NlmClient().list_sources("nb-1")
    assert len(sources) == 1
    assert sources[0].id == "s1"
    assert sources[0].is_refreshable


def test_in_band_error_envelope_raises_even_on_exit_zero(fake_cli):
    # Upstream reports failures on stdout, sometimes with exit code 0.
    fake_cli.scenario(
        {"source list": {"stdout": {"error": True, "code": "BOOM", "message": "kaboom"}, "exit": 0}}
    )
    with pytest.raises(NlmError) as excinfo:
        NlmClient().list_sources("nb-1")
    assert "kaboom" in str(excinfo.value)


def test_expired_auth_becomes_auth_error_with_login_hint(fake_cli):
    fake_cli.scenario(
        {
            "source list": {
                "stdout": {
                    "error": True,
                    "code": "UNEXPECTED_ERROR",
                    "message": "Authentication expired or invalid.",
                },
                "exit": 0,
            }
        }
    )
    with pytest.raises(AuthError) as excinfo:
        NlmClient().list_sources("nb-1")
    assert "notebooklm login" in str(excinfo.value)


def test_auth_check_passes_test_flag(fake_cli):
    # Without --test the check is local-only and reports ok while the session is dead.
    fake_cli.scenario({"auth check": {"status": "ok"}})
    NlmClient().auth_check(test=True)
    assert "--test" in fake_cli.calls[0]


def test_wait_exit_zero_is_ready(fake_cli):
    fake_cli.scenario({"source wait": {"stdout": {"status": "READY"}, "exit": 0}})
    assert NlmClient().wait_source("nb-1", "s1").status is WaitStatus.READY


def test_wait_exit_two_is_timeout_not_failure(fake_cli):
    fake_cli.scenario({"source wait": {"stdout": {"status": "PROCESSING"}, "exit": 2}})
    assert NlmClient().wait_source("nb-1", "s1").status is WaitStatus.TIMEOUT


def test_wait_exit_one_is_failure(fake_cli):
    fake_cli.scenario({"source wait": {"stdout": {"status": "ERROR"}, "exit": 1}})
    assert NlmClient().wait_source("nb-1", "s1").status is WaitStatus.FAILED


def test_stale_reads_json_field_not_exit_code(fake_cli):
    # `source stale --exit-on-stale` inverts its exit code, so we must read the field.
    fake_cli.scenario({"source stale": {"stale": True}})
    assert NlmClient().is_stale("nb-1", "s1") is True
    assert "--exit-on-stale" not in fake_cli.calls[0]


def test_add_source_passes_title_and_type(fake_cli):
    fake_cli.scenario({"source add": {"id": "s9", "url": "https://example.com/a"}})
    created = NlmClient().add_source(
        "nb-1", "https://example.com/a", title="A Paper", type_="url"
    )
    assert created is not None and created.id == "s9"
    argv = fake_cli.calls[0]
    assert "--title" in argv and "A Paper" in argv
    assert "--type" in argv and "url" in argv


def test_profile_is_passed_through(fake_cli):
    fake_cli.scenario({"source list": {"sources": []}})
    NlmClient(profile="work").list_sources("nb-1")
    argv = fake_cli.calls[0]
    assert "--profile" in argv and "work" in argv


def test_missing_binary_is_a_clear_error(monkeypatch):
    client = NlmClient(binary="definitely-not-a-real-binary-xyz")
    with pytest.raises(NlmError) as excinfo:
        client.list_sources("nb-1")
    assert "not found on PATH" in str(excinfo.value)
