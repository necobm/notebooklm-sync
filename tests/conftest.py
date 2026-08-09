"""Shared fixtures. Nothing here may touch the network or real Google auth."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from fake_notebooklm import command_of

FAKE_SCRIPT = Path(__file__).parent / "fake_notebooklm.py"


class FakeCli:
    """Handle to the fake ``notebooklm`` binary installed on PATH."""

    def __init__(self, bin_dir: Path, scenario_path: Path, calls_path: Path) -> None:
        self.bin_dir = bin_dir
        self.scenario_path = scenario_path
        self.calls_path = calls_path

    def scenario(self, mapping: dict) -> None:
        """Set the canned responses, keyed by subcommand (e.g. ``"source list"``)."""
        self.scenario_path.write_text(json.dumps(mapping), encoding="utf-8")

    @property
    def calls(self) -> list[list[str]]:
        """argv of every invocation, in order."""
        if not self.calls_path.exists():
            return []
        return [
            json.loads(line)
            for line in self.calls_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def commands(self) -> list[str]:
        """Just the subcommand of each call ("source list"), for coarse assertions.

        Flags *and their values* are dropped, so a notebook or source id passed via
        ``-n`` never leaks into the summary.
        """
        return [command_of(call) for call in self.calls]


@pytest.fixture
def fake_cli(tmp_path, monkeypatch) -> FakeCli:
    """Put a fake ``notebooklm`` on PATH so no test can reach the real API."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    shim = bin_dir / "notebooklm"
    shim.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_SCRIPT}" "$@"\n', encoding="utf-8"
    )
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    scenario_path = tmp_path / "scenario.json"
    calls_path = tmp_path / "calls.jsonl"
    scenario_path.write_text("{}", encoding="utf-8")

    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_NLM_SCENARIO", str(scenario_path))
    monkeypatch.setenv("FAKE_NLM_CALLS", str(calls_path))

    return FakeCli(bin_dir, scenario_path, calls_path)


@pytest.fixture
def clean_env(monkeypatch):
    """Strip inherited config so tests see only what they set themselves."""
    for key in list(os.environ):
        if key.startswith(("NOTEBOOK", "SYNC_")):
            monkeypatch.delenv(key, raising=False)
    return monkeypatch


@pytest.fixture
def db_path(tmp_path) -> Path:
    """A throwaway database — never the repo's ./notebooklm-sync.db."""
    return tmp_path / "test.db"
