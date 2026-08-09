"""Configuration loaded from the environment and ``.env``.

Precedence is ``os.environ`` > ``.env`` > defaults, so CI can override anything
without editing files.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from .errors import ConfigError
from .models import NotebookConfig, SyncPolicy

DEFAULT_DB_PATH = "./notebooklm-sync.db"
DEFAULT_WAIT_TIMEOUT = 120
DEFAULT_CLI_TIMEOUT = 300
DEFAULT_HTTP_TIMEOUT = 30

#: How long a crawl rule's discovered URL list stays usable before it is re-fetched.
DEFAULT_DISCOVERY_TTL = 86_400

#: How many URLs one crawl rule may contribute when it states no ``[max=N]``.
#: A rule that *does* state one always wins, in both directions — see ``crawl.py``.
DEFAULT_DISCOVERY_MAX = 100


@dataclass(frozen=True)
class Settings:
    """Everything the tool needs, resolved from the environment."""

    notebooks: dict[str, NotebookConfig]
    policy: SyncPolicy = SyncPolicy.SKIP
    profile: str | None = None
    db_path: Path = Path(DEFAULT_DB_PATH)
    wait_timeout: int = DEFAULT_WAIT_TIMEOUT
    cli_timeout: int = DEFAULT_CLI_TIMEOUT
    http_timeout: int = DEFAULT_HTTP_TIMEOUT
    discovery_ttl: int = DEFAULT_DISCOVERY_TTL
    discovery_max: int = DEFAULT_DISCOVERY_MAX
    log_level: str = "INFO"

    def notebook(self, name: str) -> NotebookConfig:
        """Look up a configured notebook by name, case-insensitively."""
        found = self.notebooks.get(name) or self.notebooks.get(name.lower())
        if found is None:
            known = ", ".join(sorted(self.notebooks)) or "(none configured)"
            raise ConfigError(f"Unknown notebook {name!r}. Configured notebooks: {known}")
        return found

    def policy_for(self, notebook: NotebookConfig) -> SyncPolicy:
        """Notebook-level policy if set, else the global one."""
        return notebook.policy or self.policy


def _env_key(name: str) -> str:
    """``my-notebook`` -> ``MY_NOTEBOOK``, for building NOTEBOOK_<NAME>_* keys."""
    return "".join(ch if ch.isalnum() else "_" for ch in name).upper()


def _parse_policy(raw: str | None, *, where: str) -> SyncPolicy | None:
    if raw is None or not raw.strip():
        return None
    try:
        return SyncPolicy(raw.strip().lower())
    except ValueError:
        valid = ", ".join(p.value for p in SyncPolicy)
        raise ConfigError(f"Invalid policy {raw!r} in {where}. Valid values: {valid}") from None


def _parse_int(raw: str | None, default: int, *, where: str) -> int:
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{where} must be an integer, got {raw!r}") from None


def load_settings(env_file: str | Path | None = ".env", environ: dict | None = None) -> Settings:
    """Load settings from ``env_file`` plus the process environment.

    ``load_dotenv`` never overrides variables already set, which is what gives the
    documented precedence.
    """
    if env_file is not None and Path(env_file).exists():
        load_dotenv(env_file, override=False)
    env = environ if environ is not None else os.environ

    global_policy = _parse_policy(env.get("SYNC_POLICY"), where="SYNC_POLICY") or SyncPolicy.SKIP

    notebooks: dict[str, NotebookConfig] = {}
    raw_names = [n.strip() for n in (env.get("NOTEBOOKS") or "").split(",") if n.strip()]
    for name in raw_names:
        key = _env_key(name)
        notebook_id = (env.get(f"NOTEBOOK_{key}_ID") or "").strip()
        manifest = (env.get(f"NOTEBOOK_{key}_SOURCES") or "").strip()
        if not notebook_id:
            raise ConfigError(
                f"Notebook {name!r} is listed in NOTEBOOKS but NOTEBOOK_{key}_ID is not set"
            )
        if not manifest:
            raise ConfigError(
                f"Notebook {name!r} is listed in NOTEBOOKS but NOTEBOOK_{key}_SOURCES is not set"
            )
        notebooks[name] = NotebookConfig(
            name=name,
            notebook_id=notebook_id,
            manifest_path=manifest,
            policy=_parse_policy(
                env.get(f"NOTEBOOK_{key}_POLICY"), where=f"NOTEBOOK_{key}_POLICY"
            ),
        )

    return Settings(
        notebooks=notebooks,
        policy=global_policy,
        profile=(env.get("NOTEBOOKLM_PROFILE") or "").strip() or None,
        db_path=Path((env.get("SYNC_DB_PATH") or DEFAULT_DB_PATH).strip()),
        wait_timeout=_parse_int(
            env.get("SYNC_WAIT_TIMEOUT"), DEFAULT_WAIT_TIMEOUT, where="SYNC_WAIT_TIMEOUT"
        ),
        cli_timeout=_parse_int(
            env.get("SYNC_CLI_TIMEOUT"), DEFAULT_CLI_TIMEOUT, where="SYNC_CLI_TIMEOUT"
        ),
        http_timeout=_parse_int(
            env.get("SYNC_HTTP_TIMEOUT"), DEFAULT_HTTP_TIMEOUT, where="SYNC_HTTP_TIMEOUT"
        ),
        discovery_ttl=_parse_int(
            env.get("SYNC_DISCOVERY_TTL"), DEFAULT_DISCOVERY_TTL, where="SYNC_DISCOVERY_TTL"
        ),
        discovery_max=_parse_int(
            env.get("SYNC_DISCOVERY_MAX"), DEFAULT_DISCOVERY_MAX, where="SYNC_DISCOVERY_MAX"
        ),
        log_level=(env.get("SYNC_LOG_LEVEL") or "INFO").strip().upper(),
    )
