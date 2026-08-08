"""Env/.env configuration loading."""

from __future__ import annotations

import pytest

from notebooklm_sync.config import load_settings
from notebooklm_sync.errors import ConfigError
from notebooklm_sync.models import SyncPolicy


def base_env(**overrides) -> dict:
    env = {
        "NOTEBOOKS": "research",
        "NOTEBOOK_RESEARCH_ID": "nb-1",
        "NOTEBOOK_RESEARCH_SOURCES": "./sources/research.yaml",
    }
    env.update(overrides)
    return env


def test_loads_notebooks():
    settings = load_settings(env_file=None, environ=base_env())
    assert settings.notebook("research").notebook_id == "nb-1"


def test_defaults_to_skip_policy():
    settings = load_settings(env_file=None, environ=base_env())
    assert settings.policy is SyncPolicy.SKIP


def test_notebook_policy_overrides_global():
    settings = load_settings(
        env_file=None,
        environ=base_env(SYNC_POLICY="create", NOTEBOOK_RESEARCH_POLICY="override"),
    )
    config = settings.notebook("research")
    assert settings.policy_for(config) is SyncPolicy.OVERRIDE


def test_hyphenated_names_map_to_underscored_keys():
    env = {
        "NOTEBOOKS": "my-notes",
        "NOTEBOOK_MY_NOTES_ID": "nb-2",
        "NOTEBOOK_MY_NOTES_SOURCES": "./x.yaml",
    }
    settings = load_settings(env_file=None, environ=env)
    assert settings.notebook("my-notes").notebook_id == "nb-2"


def test_missing_id_is_a_config_error():
    env = {"NOTEBOOKS": "research", "NOTEBOOK_RESEARCH_SOURCES": "./x.yaml"}
    with pytest.raises(ConfigError, match="NOTEBOOK_RESEARCH_ID"):
        load_settings(env_file=None, environ=env)


def test_missing_manifest_path_is_a_config_error():
    env = {"NOTEBOOKS": "research", "NOTEBOOK_RESEARCH_ID": "nb-1"}
    with pytest.raises(ConfigError, match="NOTEBOOK_RESEARCH_SOURCES"):
        load_settings(env_file=None, environ=env)


def test_invalid_policy_is_rejected_with_valid_values():
    with pytest.raises(ConfigError, match="override"):
        load_settings(env_file=None, environ=base_env(SYNC_POLICY="nonsense"))


def test_unknown_notebook_lists_configured_names():
    settings = load_settings(env_file=None, environ=base_env())
    with pytest.raises(ConfigError, match="research"):
        settings.notebook("nope")


def test_non_integer_timeout_is_rejected():
    with pytest.raises(ConfigError, match="SYNC_WAIT_TIMEOUT"):
        load_settings(env_file=None, environ=base_env(SYNC_WAIT_TIMEOUT="soon"))
