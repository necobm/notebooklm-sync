"""Typer entry point for ``notebooklm-sync``."""

from __future__ import annotations

import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import db as store
from . import engine
from .config import Settings, load_settings
from .errors import EXIT_ACTION_FAILED, EXIT_OK, ConfigError, ManifestError, SyncError
from .manifest import load_manifest
from .matching import normalize_url
from .models import Action, NotebookConfig, Outcome, SyncPolicy
from .nlm import NlmClient

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Sync a NotebookLM notebook from a declared list of web sources.",
)
console = Console()
err_console = Console(stderr=True)


def _load() -> Settings:
    settings = load_settings()
    if not settings.notebooks:
        raise ConfigError(
            "No notebooks configured. Set NOTEBOOKS in .env "
            "(copy .env.example to get started)."
        )
    return settings


def _client(settings: Settings) -> NlmClient:
    return NlmClient(profile=settings.profile, timeout=settings.cli_timeout)


def _pick_notebook(settings: Settings) -> NotebookConfig:
    """Prompt for a notebook when none was given on the command line."""
    names = sorted(settings.notebooks)
    if not sys.stdin.isatty():
        raise ConfigError(
            "No notebook given and stdin is not a TTY. "
            f"Pass one of: {', '.join(names)}"
        )
    console.print("[bold]Select a notebook to sync:[/bold]")
    for index, name in enumerate(names, start=1):
        console.print(f"  {index}. {name}")
    choice = typer.prompt("Notebook", default="1")
    if choice.isdigit() and 1 <= int(choice) <= len(names):
        return settings.notebook(names[int(choice) - 1])
    return settings.notebook(choice)


ENV_TEMPLATE = """\
# Auth: run `notebooklm login` once, then name the profile here.
NOTEBOOKLM_PROFILE=default

# For each name in NOTEBOOKS, define NOTEBOOK_<UPPER_NAME>_ID and _SOURCES.
NOTEBOOKS=research
NOTEBOOK_RESEARCH_ID=
NOTEBOOK_RESEARCH_SOURCES=./sources/example.yaml

# skip | override | create
SYNC_POLICY=skip
SYNC_DB_PATH=./notebooklm-sync.db
SYNC_WAIT_TIMEOUT=120
SYNC_CLI_TIMEOUT=300
SYNC_LOG_LEVEL=INFO
"""


def _safe_normalize(url: str | None) -> str | None:
    """Normalized form for the DB mirror; None when the URL can't be normalized."""
    if not url:
        return None
    try:
        return normalize_url(url)
    except ManifestError:
        return None


def _fail(exc: SyncError) -> None:
    err_console.print(f"[red]error:[/red] {exc}")
    raise typer.Exit(exc.exit_code)


@app.command()
def sync(
    notebook: str = typer.Argument(None, help="Configured notebook name."),
    policy: str = typer.Option(None, "--policy", "-p", help="Override the sync policy."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan only; change nothing."),
    no_wait: bool = typer.Option(False, "--no-wait", help="Don't wait for ingestion."),
) -> None:
    """Sync a notebook against its source manifest."""
    try:
        settings = _load()
        config = settings.notebook(notebook) if notebook else _pick_notebook(settings)
        default_policy = SyncPolicy(policy) if policy else settings.policy_for(config)

        entries = load_manifest(config.manifest_path)
        client = _client(settings)

        if not dry_run:
            # --test forces a real network round-trip; the local-only check reports
            # "ok" even when the session has expired.
            client.auth_check(test=True)

        remote = client.list_sources(config.notebook_id)
        plan_ = engine.plan(config, entries, remote, default_policy=default_policy)

        conn = store.connect(settings.db_path)
        notebook_pk = store.upsert_notebook(conn, config.name, config.notebook_id)
        run_id = store.start_run(conn, notebook_pk, default_policy.value, dry_run=dry_run)

        if dry_run:
            _render_plan(plan_, dry_run=True)
            for action in plan_.actions:
                store.record_event(conn, run_id, action)
            summary = store.summarize(plan_.actions)
            store.finish_run(conn, run_id, summary, "dry-run")
            raise typer.Exit(EXIT_OK)

        engine.execute(
            plan_,
            client,
            wait=not no_wait,
            wait_timeout=settings.wait_timeout,
        )

        for action in plan_.actions:
            store.record_event(conn, run_id, action)
            if action.source_id and action.action is not Action.ORPHAN:
                store.upsert_source(
                    conn,
                    notebook_pk,
                    source_id=action.source_id,
                    url=action.url,
                    normalized_url=_safe_normalize(action.url),
                    title=action.title,
                    kind=None,
                    status=action.outcome.value if action.outcome else None,
                    last_action=action.action.value,
                )

        summary = store.summarize(plan_.actions)
        failed = summary.failed > 0
        store.finish_run(conn, run_id, summary, "failed" if failed else "ok")
        store.mark_notebook_synced(conn, notebook_pk)

        _render_plan(plan_, dry_run=False)
        raise typer.Exit(EXIT_ACTION_FAILED if failed else EXIT_OK)

    except SyncError as exc:
        _fail(exc)


@app.command()
def status(notebook: str = typer.Argument(None, help="Configured notebook name.")) -> None:
    """Show drift between the manifest and the notebook. Changes nothing."""
    try:
        settings = _load()
        config = settings.notebook(notebook) if notebook else _pick_notebook(settings)
        entries = load_manifest(config.manifest_path)
        client = _client(settings)
        remote = client.list_sources(config.notebook_id)
        plan_ = engine.plan(
            config, entries, remote, default_policy=settings.policy_for(config)
        )
        _render_plan(plan_, dry_run=True)
    except SyncError as exc:
        _fail(exc)


@app.command()
def notebooks() -> None:
    """List configured notebooks and when they were last synced."""
    try:
        settings = _load()
        conn = store.connect(settings.db_path)
        table = Table(title="Configured notebooks")
        table.add_column("Name", style="bold")
        table.add_column("Notebook ID")
        table.add_column("Manifest")
        table.add_column("Policy")
        table.add_column("Last synced")
        for name in sorted(settings.notebooks):
            config = settings.notebooks[name]
            exists = "" if Path(config.manifest_path).exists() else " [red](missing)[/red]"
            table.add_row(
                name,
                config.notebook_id,
                f"{config.manifest_path}{exists}",
                settings.policy_for(config).value,
                store.last_synced_at(conn, name) or "never",
            )
        console.print(table)
    except SyncError as exc:
        _fail(exc)


@app.command()
def history(
    notebook: str = typer.Argument(None, help="Configured notebook name."),
    limit: int = typer.Option(20, "--limit", "-n", help="Rows to show."),
) -> None:
    """Show recent sync runs."""
    try:
        settings = _load()
        conn = store.connect(settings.db_path)
        notebook_pk = None
        if notebook:
            config = settings.notebook(notebook)
            notebook_pk = store.upsert_notebook(conn, config.name, config.notebook_id)

        table = Table(title="Sync history")
        for column in ("Run", "Notebook", "Started", "Policy", "Status", "Add", "Refresh", "Skip", "Fail"):
            table.add_column(column)
        for row in store.recent_runs(conn, notebook_pk, limit):
            status_text = row["status"] or ""
            if row["dry_run"]:
                status_text += " (dry-run)"
            table.add_row(
                str(row["id"]),
                row["notebook_name"],
                row["started_at"],
                row["policy"],
                status_text,
                str(row["n_added"]),
                str(row["n_refreshed"]),
                str(row["n_skipped"]),
                str(row["n_failed"]),
            )
        console.print(table)
    except SyncError as exc:
        _fail(exc)


@app.command()
def init() -> None:
    """Create a starter .env and an example manifest."""
    env_target = Path(".env")
    if env_target.exists():
        console.print("[yellow].env already exists, leaving it alone[/yellow]")
    else:
        # Prefer the repo's .env.example when running from a checkout; fall back to
        # the inline template, since a wheel install has no repo root to look in.
        example = Path(__file__).resolve().parent.parent.parent / ".env.example"
        content = example.read_text(encoding="utf-8") if example.exists() else ENV_TEMPLATE
        env_target.write_text(content, encoding="utf-8")
        console.print("[green]created[/green] .env — edit it with your notebook IDs")

    manifest_dir = Path("sources")
    manifest_dir.mkdir(exist_ok=True)
    sample = manifest_dir / "example.yaml"
    if not sample.exists():
        sample.write_text(
            "sources:\n  - url: https://example.com/some-article\n", encoding="utf-8"
        )
        console.print(f"[green]created[/green] {sample}")

    console.print(
        "\nNext: run [bold]notebooklm login[/bold] to authenticate, "
        "then [bold]notebooklm-sync sync --dry-run[/bold]."
    )


def _render_plan(plan_, *, dry_run: bool) -> None:
    """Print the plan (or its results) and a summary line."""
    table = Table(title="Sync plan" if dry_run else "Sync results")
    table.add_column("Action", style="bold")
    if not dry_run:
        table.add_column("Outcome")
    table.add_column("Title")
    table.add_column("URL")
    table.add_column("Reason")

    styles = {
        Action.ADD: "green",
        Action.REFRESH: "cyan",
        Action.SKIP: "dim",
        Action.ORPHAN: "yellow",
    }
    for action in plan_.actions:
        cells = [f"[{styles[action.action]}]{action.action.value}[/]"]
        if not dry_run:
            outcome = action.outcome.value if action.outcome else ""
            colour = "red" if action.outcome is Outcome.FAILED else "default"
            cells.append(f"[{colour}]{outcome}[/]")
        cells += [action.title or "", action.url or "", action.message or action.reason]
        table.add_row(*cells)

    console.print(table)

    summary = store.summarize(plan_.actions)
    console.print(
        f"[green]{summary.added} add[/green] · [cyan]{summary.refreshed} refresh[/cyan] · "
        f"{summary.skipped} skip · {summary.pending} pending · "
        f"[red]{summary.failed} failed[/red] · [yellow]{summary.orphans} orphan[/yellow]"
    )
    if summary.orphans:
        console.print("[dim]Orphans are in the notebook but not the manifest. Never deleted.[/dim]")
    if plan_.duplicates:
        console.print(f"[dim]{len(plan_.duplicates)} duplicate source(s) in the notebook.[/dim]")


if __name__ == "__main__":
    app()
