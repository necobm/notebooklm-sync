"""The Typer layer: exit codes, flag plumbing and what actually reaches the CLI.

This is the only place the exit-code contract (0/1/2/3) is asserted end to end.
Everything runs against the fake `notebooklm` on PATH, with configuration coming
from the environment and a tmp working directory — no `.env`, no auth, no network,
and never the repo's own database.
"""

from __future__ import annotations

import sqlite3

import pytest
from typer.testing import CliRunner

from notebooklm_sync.cli import app

runner = CliRunner()

NOTEBOOK_ID = "nb-1"
REMOTE_SOURCE = {
    "id": "s1",
    "url": "https://example.com/a",
    "title": "A",
    "type": "web_page",
    "status": "ready",
}


@pytest.fixture
def project(tmp_path, clean_env, fake_cli):
    """A configured notebook in a tmp cwd, with a one-entry manifest."""
    manifest = tmp_path / "sources.yaml"
    manifest.write_text("sources:\n  - url: https://example.com/a\n", encoding="utf-8")

    clean_env.chdir(tmp_path)  # no real .env can be picked up
    clean_env.setenv("NOTEBOOKS", "research")
    clean_env.setenv("NOTEBOOK_RESEARCH_ID", NOTEBOOK_ID)
    clean_env.setenv("NOTEBOOK_RESEARCH_SOURCES", str(manifest))
    clean_env.setenv("SYNC_DB_PATH", str(tmp_path / "sync.db"))

    fake_cli.db_path = tmp_path / "sync.db"
    fake_cli.manifest = manifest
    return fake_cli


def rows(db_path, sql):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


# -- dry run ------------------------------------------------------------------


def test_dry_run_is_read_only(project):
    project.scenario({"source list": {"sources": []}})
    result = runner.invoke(app, ["sync", "research", "--dry-run"])

    assert result.exit_code == 0
    # No auth check (it is a network round-trip), and above all nothing mutating.
    assert project.commands() == ["source list"]
    assert rows(project.db_path, "SELECT dry_run FROM sync_runs")[0]["dry_run"] == 1


# -- exit codes ---------------------------------------------------------------


def test_successful_sync_exits_zero_and_adds_the_original_url(project):
    project.manifest.write_text(
        "sources:\n  - url: https://www.example.com/a/?utm_source=news\n", encoding="utf-8"
    )
    project.scenario(
        {
            "source list": {"sources": []},
            "source add": {"id": "s9", "url": "https://example.com/a", "type": "web_page"},
            "source wait": {"stdout": {"status": "READY"}, "exit": 0},
        }
    )
    result = runner.invoke(app, ["sync", "research"])

    assert result.exit_code == 0
    add = next(call for call in project.calls if "add" in call)
    assert "https://www.example.com/a/?utm_source=news" in add  # never the normalized form

    source = rows(project.db_path, "SELECT * FROM sources")[0]
    assert source["source_id"] == "s9"
    assert source["kind"] == "web_page"
    assert source["normalized_url"] == "https://example.com/a"


def test_auth_failure_exits_three_with_the_login_hint(project):
    project.scenario(
        {
            "auth check": {
                "stdout": {
                    "error": True,
                    "code": "UNEXPECTED_ERROR",
                    "message": "Authentication expired or invalid.",
                },
                "exit": 0,
            }
        }
    )
    result = runner.invoke(app, ["sync", "research"])

    assert result.exit_code == 3
    assert "notebooklm login" in result.output
    assert "source add" not in project.commands()


def test_failed_action_exits_one_without_aborting_the_run(project):
    project.manifest.write_text(
        "sources:\n  - url: https://example.com/bad\n  - url: https://example.com/a\n",
        encoding="utf-8",
    )
    project.scenario(
        {
            "source list": {"sources": [REMOTE_SOURCE]},
            "source add": {
                "stdout": {"error": True, "code": "FETCH_FAILED", "message": "could not fetch"},
                "exit": 0,
            },
        }
    )
    result = runner.invoke(app, ["sync", "research"])

    assert result.exit_code == 1
    run = rows(project.db_path, "SELECT * FROM sync_runs")[0]
    assert run["status"] == "failed"
    assert run["n_failed"] == 1
    # The second entry still matched and was skipped — one failure aborts nothing.
    assert run["n_skipped"] == 1


def test_ingestion_timeout_is_pending_and_still_exits_zero(project):
    project.scenario(
        {
            "source list": {"sources": []},
            "source add": {"id": "s9", "url": "https://example.com/a"},
            "source wait": {"stdout": {"status": "PROCESSING"}, "exit": 2},
        }
    )
    result = runner.invoke(app, ["sync", "research"])

    assert result.exit_code == 0
    run = rows(project.db_path, "SELECT * FROM sync_runs")[0]
    assert run["n_pending"] == 1 and run["n_failed"] == 0


def test_unknown_notebook_exits_two(project):
    result = runner.invoke(app, ["sync", "marketing"])
    assert result.exit_code == 2
    assert "Unknown notebook" in result.output


def test_no_notebooks_configured_exits_two(project, clean_env):
    clean_env.delenv("NOTEBOOKS")
    result = runner.invoke(app, ["sync", "research"])
    assert result.exit_code == 2


def test_missing_manifest_exits_two(project):
    project.manifest.unlink()
    result = runner.invoke(app, ["sync", "research", "--dry-run"])
    assert result.exit_code == 2


# -- --only-stale -------------------------------------------------------------


def test_only_stale_skips_a_fresh_source(project):
    project.scenario({"source list": {"sources": [REMOTE_SOURCE]}, "source stale": {"stale": False}})
    result = runner.invoke(
        app, ["sync", "research", "--policy", "override", "--only-stale"]
    )

    assert result.exit_code == 0
    assert "source refresh" not in project.commands()
    assert rows(project.db_path, "SELECT * FROM sync_runs")[0]["n_skipped"] == 1


def test_only_stale_refreshes_a_stale_source(project):
    project.scenario({"source list": {"sources": [REMOTE_SOURCE]}, "source stale": {"stale": True}})
    result = runner.invoke(
        app, ["sync", "research", "--policy", "override", "--only-stale"]
    )

    assert result.exit_code == 0
    assert "source refresh" in project.commands()


def test_only_stale_probes_in_dry_run_too(project):
    # Read-only, so the preview shows the same set a real run would produce.
    project.scenario({"source list": {"sources": [REMOTE_SOURCE]}, "source stale": {"stale": False}})
    result = runner.invoke(
        app, ["sync", "research", "--policy", "override", "--only-stale", "--dry-run"]
    )

    assert result.exit_code == 0
    assert project.commands() == ["source list", "source stale"]


def test_only_stale_is_a_no_op_under_skip(project):
    project.scenario({"source list": {"sources": [REMOTE_SOURCE]}})
    result = runner.invoke(app, ["sync", "research", "--only-stale", "--dry-run"])

    assert result.exit_code == 0
    assert "source stale" not in project.commands()


# -- notebooks ----------------------------------------------------------------


def test_notebooks_marks_a_configured_id_ok(project):
    project.scenario({"list": {"notebooks": [{"id": NOTEBOOK_ID, "title": "Research"}]}})
    result = runner.invoke(app, ["notebooks"])

    assert result.exit_code == 0
    assert "ok" in result.output


def test_notebooks_flags_an_id_that_no_longer_exists(project):
    project.scenario({"list": {"notebooks": [{"id": "someone-elses-nb", "title": "Other"}]}})
    result = runner.invoke(app, ["notebooks"])

    assert result.exit_code == 0
    assert "missing" in result.output


def test_notebooks_degrades_when_upstream_cannot_be_reached(project):
    # The command you run *because* auth broke must still show your configuration.
    project.scenario(
        {
            "list": {
                "stdout": {"error": True, "code": "UNEXPECTED_ERROR", "message": "Not authenticated"},
                "exit": 0,
            }
        }
    )
    result = runner.invoke(app, ["notebooks"])

    assert result.exit_code == 0
    assert "could not reach notebooklm" in result.output
    assert "research" in result.output


# -- other commands -----------------------------------------------------------


def test_status_renders_the_plan_without_touching_the_notebook(project):
    project.scenario({"source list": {"sources": [REMOTE_SOURCE]}})
    result = runner.invoke(app, ["status", "research"])

    assert result.exit_code == 0
    assert project.commands() == ["source list"]


def test_history_lists_past_runs(project):
    project.scenario({"source list": {"sources": []}})
    runner.invoke(app, ["sync", "research", "--dry-run"])
    result = runner.invoke(app, ["history", "research"])

    assert result.exit_code == 0
    assert "dry-run" in result.output


def test_init_scaffolds_and_is_idempotent(project, tmp_path):
    first = runner.invoke(app, ["init"])
    assert first.exit_code == 0
    assert (tmp_path / ".env").exists()
    assert (tmp_path / "sources" / "example.yaml").exists()

    (tmp_path / ".env").write_text("EDITED=1", encoding="utf-8")
    second = runner.invoke(app, ["init"])
    assert second.exit_code == 0
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "EDITED=1"


# -- --verbose ----------------------------------------------------------------


def test_verbose_prints_the_argv_of_every_invocation(project):
    project.scenario({"source list": {"sources": []}})
    result = runner.invoke(app, ["sync", "research", "--dry-run", "-v"])

    assert result.exit_code == 0
    assert "$ notebooklm" in result.output
    assert "--json" in result.output


def test_without_verbose_no_argv_is_printed(project):
    project.scenario({"source list": {"sources": []}})
    result = runner.invoke(app, ["sync", "research", "--dry-run"])

    assert "$ notebooklm" not in result.output
