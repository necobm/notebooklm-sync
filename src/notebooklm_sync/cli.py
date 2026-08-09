"""Typer entry point for ``notebooklm-sync``."""

from __future__ import annotations

import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from . import db as store
from . import engine
from . import progress as progress_ui
from .config import Settings, load_settings
from .discovery import Discoverer, Expansion, expand_entries
from .errors import EXIT_ACTION_FAILED, EXIT_OK, ConfigError, ManifestError, SyncError
from .manifest import load_manifest
from .matching import normalize_url
from .models import Action, ManifestEntry, NotebookConfig, Outcome, SyncPolicy
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


def _client(settings: Settings, *, verbose: bool = False) -> NlmClient:
    def echo(argv: list[str]) -> None:
        # stderr, so piping stdout stays clean.
        err_console.print(f"[dim]$ {' '.join(argv)}[/dim]")

    return NlmClient(
        profile=settings.profile,
        timeout=settings.cli_timeout,
        on_call=echo if verbose else None,
    )


def _discoverer(settings: Settings, *, verbose: bool = False, on_fetch=None) -> Discoverer:
    def notify(url: str) -> None:
        if verbose:
            err_console.print(f"[dim]GET {url}[/dim]")
        if on_fetch is not None:
            on_fetch(url)

    return Discoverer(timeout=settings.http_timeout, on_fetch=notify)


def _reporter(settings: Settings, *, no_progress: bool, verbose: bool) -> progress_ui.Reporter:
    """Decide once whether this run gets a live display.

    ``-v`` wins and turns it off: a scrolling argv/``GET`` stream and a live region
    compete for the same stderr, and ``-v`` exists to be read closely. Everything
    else is delegated to ``make_reporter``, which also refuses a non-TTY.
    """
    enabled = settings.progress and not no_progress and not verbose
    return progress_ui.make_reporter(err_console, enabled=enabled)


class _DiscoveryProgress:
    """Bridges ``discovery``'s two callbacks onto the reporter.

    Discovery has no honest total — a sitemap's length is unknown until it is parsed
    and the crawl cap is a ceiling, not an estimate — so this reports a live fetch
    count rather than a percentage.
    """

    def __init__(self, reporter: progress_ui.Reporter) -> None:
        self.reporter = reporter
        self.fetches = 0
        self.label = ""

    def on_rule(self, rule, index: int, total: int) -> None:
        self.fetches = 0
        self.label = f"rule {index}/{total}"
        # No escape() needed: the reporter renders through rich Text, which takes its
        # content literally — so a rule's [modifiers] cannot be eaten as markup here.
        self.reporter.detail(rule.raw)
        self._show()

    def on_fetch(self, url: str) -> None:
        self.fetches += 1
        self._show()

    def _show(self) -> None:
        pages = "page" if self.fetches == 1 else "pages"
        self.reporter.busy(f"{self.label} · {self.fetches} {pages} fetched")


def _expand(
    entries: list[ManifestEntry],
    settings: Settings,
    conn: sqlite3.Connection,
    *,
    refresh: bool = False,
    verbose: bool = False,
    reporter: progress_ui.Reporter | None = None,
) -> tuple[list[ManifestEntry], list[Expansion]]:
    """Resolve crawl rules to plain entries, between the manifest and ``plan()``.

    A manifest with no rules short-circuits, so nothing that worked before this
    feature existed now touches the network — and no phase is reported either.
    """
    if not any(entry.rule is not None for entry in entries):
        return entries, []

    reporter = reporter or progress_ui.NullReporter()
    bridge = _DiscoveryProgress(reporter)
    reporter.phase("discovery")
    expanded, expansions = expand_entries(
        entries,
        discoverer=_discoverer(settings, verbose=verbose, on_fetch=bridge.on_fetch),
        conn=conn,
        ttl=settings.discovery_ttl,
        refresh=refresh,
        default_max=settings.discovery_max,
        on_rule=bridge.on_rule,
    )
    urls = sum(len(expansion.urls) for expansion in expansions)
    rules = "rule" if len(expansions) == 1 else "rules"
    reporter.finish(f"{len(expansions)} {rules} → {urls} URL(s)")
    return expanded, expansions


def _execute_callbacks(reporter: progress_ui.Reporter):
    """Map ``engine.execute``'s two callbacks onto the display.

    ``"wait"`` gets its own spinner line because it is the one step that can block
    for ``SYNC_WAIT_TIMEOUT`` seconds — without it a slow ingest is indistinguishable
    from a hang.
    """

    def on_step(action, step: str) -> None:
        if step == "wait":
            reporter.busy("waiting for ingestion")
        else:
            reporter.idle()
            reporter.detail(f"{step}   {action.url or ''}")

    def on_action(action) -> None:
        reporter.idle()
        reporter.advance()

    return on_step, on_action


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

# Live progress display on stderr for `sync`, `status` and `expand`. It already
# turns itself off when stderr is not a terminal and whenever -v is used; set this
# to 0 to disable it permanently.
SYNC_PROGRESS=1

# Crawl rules (`https://site.com/*[level=2][except=blog]`) in a manifest.
# Seconds a rule's discovered URL list is reused before re-fetching the site.
SYNC_DISCOVERY_TTL=86400
# URLs one rule may contribute when it states no [max=N]. An inline [max=N]
# always wins over this, in both directions.
SYNC_DISCOVERY_MAX=100
SYNC_HTTP_TIMEOUT=30
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
    # escape(): a message quoting a crawl rule carries square brackets, which Rich
    # would otherwise read as markup and drop from the error the user has to act on.
    err_console.print(f"[red]error:[/red] {escape(str(exc))}")
    raise typer.Exit(exc.exit_code)


@app.command()
def sync(
    notebook: str = typer.Argument(None, help="Configured notebook name."),
    policy: str = typer.Option(None, "--policy", "-p", help="Override the sync policy."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan only; change nothing."),
    no_wait: bool = typer.Option(False, "--no-wait", help="Don't wait for ingestion."),
    only_stale: bool = typer.Option(
        False, "--only-stale", help="Under `override`, refresh only sources that are stale."
    ),
    refresh_discovery: bool = typer.Option(
        False, "--refresh-discovery", help="Re-resolve crawl rules instead of using the cache."
    ),
    no_progress: bool = typer.Option(
        False, "--no-progress", help="Don't show the live progress display. Implied by -v."
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Print every `notebooklm` invocation to stderr."
    ),
) -> None:
    """Sync a notebook against its source manifest."""
    try:
        settings = _load()
        config = settings.notebook(notebook) if notebook else _pick_notebook(settings)
        default_policy = SyncPolicy(policy) if policy else settings.policy_for(config)

        entries = load_manifest(config.manifest_path)
        conn = store.connect(settings.db_path)
        reporter = _reporter(settings, no_progress=no_progress, verbose=verbose)

        # Everything slow happens inside this block, and nothing is printed from it.
        # Rich restores the terminal on the way out whatever raised, so Ctrl-C, a
        # SyncError and a typer.Exit all leave a usable cursor behind.
        with reporter:
            # Crawl rules resolve here, before plan() — which keeps plan() pure and
            # keeps --dry-run running the identical code path a real sync does.
            entries, expansions = _expand(
                entries, settings, conn, refresh=refresh_discovery,
                verbose=verbose, reporter=reporter,
            )
            client = _client(settings, verbose=verbose)

            if not dry_run:
                # --test forces a real network round-trip; the local-only check
                # reports "ok" even when the session has expired.
                reporter.phase("auth")
                client.auth_check(test=True)
                reporter.finish("credentials ok")

            reporter.phase("sources")
            remote = client.list_sources(config.notebook_id)
            reporter.finish(f"{len(remote)} in notebook")

            plan_ = engine.plan(config, entries, remote, default_policy=default_policy)

            if only_stale:
                # Read-only, so it runs in dry-run too: the preview must show the
                # same refresh/skip set a real run would produce.
                _apply_stale_filter(plan_, client, reporter)

            notebook_pk = store.upsert_notebook(conn, config.name, config.notebook_id)
            run_id = store.start_run(conn, notebook_pk, default_policy.value, dry_run=dry_run)

            if not dry_run:
                reporter.phase("syncing")
                reporter.start_actions(len(plan_.actions))
                on_step, on_action = _execute_callbacks(reporter)
                engine.execute(
                    plan_,
                    client,
                    wait=not no_wait,
                    wait_timeout=settings.wait_timeout,
                    on_step=on_step,
                    on_action=on_action,
                )
                reporter.finish(f"{len(plan_.actions)} action(s)")

        # -- the live region is gone; from here on it is only local work and output --

        for action in plan_.actions:
            store.record_event(conn, run_id, action)
            if not dry_run and action.source_id and action.action is not Action.ORPHAN:
                store.upsert_source(
                    conn,
                    notebook_pk,
                    source_id=action.source_id,
                    url=action.url,
                    normalized_url=_safe_normalize(action.url),
                    title=action.title,
                    kind=action.kind,
                    status=action.outcome.value if action.outcome else None,
                    last_action=action.action.value,
                )

        summary = store.summarize(plan_.actions)

        if dry_run:
            store.finish_run(conn, run_id, summary, "dry-run")
            _render_plan(plan_, dry_run=True, expansions=expansions)
            raise typer.Exit(EXIT_OK)

        failed = summary.failed > 0
        store.finish_run(conn, run_id, summary, "failed" if failed else "ok")
        store.mark_notebook_synced(conn, notebook_pk)

        _render_plan(plan_, dry_run=False, expansions=expansions)
        raise typer.Exit(EXIT_ACTION_FAILED if failed else EXIT_OK)

    except SyncError as exc:
        _fail(exc)


def _apply_stale_filter(plan_, client: NlmClient, reporter: progress_ui.Reporter) -> None:
    """``--only-stale``, with its probes reported: one subprocess call per refresh
    candidate is easily the second-slowest thing a run does."""
    total = len(plan_.of(Action.REFRESH))
    if not total:
        engine.apply_stale_filter(plan_, client)
        return

    probed = 0

    def on_probe(action) -> None:
        nonlocal probed
        probed += 1
        reporter.busy(f"checking {probed}/{total}")
        reporter.detail(action.url or "")

    reporter.phase("staleness")
    engine.apply_stale_filter(plan_, client, on_probe=on_probe)
    reporter.finish(f"{total} probed")


@app.command()
def status(
    notebook: str = typer.Argument(None, help="Configured notebook name."),
    refresh_discovery: bool = typer.Option(
        False, "--refresh-discovery", help="Re-resolve crawl rules instead of using the cache."
    ),
    no_progress: bool = typer.Option(
        False, "--no-progress", help="Don't show the live progress display. Implied by -v."
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Print every `notebooklm` invocation to stderr."
    ),
) -> None:
    """Show drift between the manifest and the notebook. Changes nothing."""
    try:
        settings = _load()
        config = settings.notebook(notebook) if notebook else _pick_notebook(settings)
        entries = load_manifest(config.manifest_path)
        conn = store.connect(settings.db_path)
        reporter = _reporter(settings, no_progress=no_progress, verbose=verbose)

        with reporter:
            entries, expansions = _expand(
                entries, settings, conn, refresh=refresh_discovery,
                verbose=verbose, reporter=reporter,
            )
            client = _client(settings, verbose=verbose)
            reporter.phase("sources")
            remote = client.list_sources(config.notebook_id)
            reporter.finish(f"{len(remote)} in notebook")
            plan_ = engine.plan(
                config, entries, remote, default_policy=settings.policy_for(config)
            )

        _render_plan(plan_, dry_run=True, expansions=expansions)
    except SyncError as exc:
        _fail(exc)


@app.command()
def expand(
    notebook: str = typer.Argument(None, help="Configured notebook name."),
    refresh_discovery: bool = typer.Option(
        False, "--refresh-discovery", help="Re-resolve crawl rules instead of using the cache."
    ),
    no_progress: bool = typer.Option(
        False, "--no-progress", help="Don't show the live progress display. Implied by -v."
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Print every HTTP request to stderr."
    ),
) -> None:
    """Show which URLs a manifest's crawl rules resolve to.

    Reads the website and nothing else — no auth, no NotebookLM call, no changes.
    Run it before trusting a rule: sync never deletes, so a source added by a rule
    that matched too much cannot be taken back by this tool.
    """
    try:
        settings = _load()
        config = settings.notebook(notebook) if notebook else _pick_notebook(settings)
        entries = load_manifest(config.manifest_path)
        conn = store.connect(settings.db_path)
        reporter = _reporter(settings, no_progress=no_progress, verbose=verbose)

        with reporter:
            entries, expansions = _expand(
                entries, settings, conn, refresh=refresh_discovery,
                verbose=verbose, reporter=reporter,
            )

        if not expansions:
            console.print(
                f"[dim]{config.manifest_path} declares no crawl rules "
                f"({len(entries)} plain source(s)).[/dim]"
            )
            return

        for expansion in expansions:
            table = Table(title=escape(expansion.rule.raw))
            table.add_column("#", justify="right", style="dim")
            table.add_column("URL")
            for index, url in enumerate(expansion.urls, start=1):
                table.add_row(str(index), url)
            console.print(table)
        _render_expansions(expansions)
    except SyncError as exc:
        _fail(exc)


def _remote_notebook_ids(settings: Settings, *, verbose: bool) -> set[str] | None:
    """IDs upstream currently knows about, or None if it could not be asked.

    Deliberately degrades instead of exiting: this is the command you run *because*
    something is wrong, and expired cookies are the likeliest reason. Refusing to
    show the configuration at that exact moment would be the wrong failure mode.
    """
    try:
        return {
            str(row.get("id") or "")
            for row in _client(settings, verbose=verbose).list_notebooks()
            if isinstance(row, dict)
        }
    except SyncError as exc:
        err_console.print(f"[yellow]warning:[/yellow] could not reach notebooklm: {exc}")
        return None


@app.command()
def notebooks(
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Print every `notebooklm` invocation to stderr."
    ),
) -> None:
    """List configured notebooks, check them against NotebookLM, and show last sync."""
    try:
        settings = _load()
        conn = store.connect(settings.db_path)
        remote_ids = _remote_notebook_ids(settings, verbose=verbose)

        table = Table(title="Configured notebooks")
        table.add_column("Name", style="bold")
        table.add_column("Notebook ID")
        table.add_column("Remote")
        table.add_column("Manifest")
        table.add_column("Policy")
        table.add_column("Last synced")
        for name in sorted(settings.notebooks):
            config = settings.notebooks[name]
            exists = "" if Path(config.manifest_path).exists() else " [red](missing)[/red]"
            if remote_ids is None:
                remote = "[dim]?[/dim]"
            elif config.notebook_id in remote_ids:
                remote = "[green]ok[/green]"
            else:
                remote = "[red]missing[/red]"
            table.add_row(
                name,
                config.notebook_id,
                remote,
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


def _age(moment: datetime) -> str:
    """A rough human age, for the discovery cache line."""
    seconds = max(0, int((datetime.now(UTC) - moment).total_seconds()))
    if seconds < 90:
        return f"{seconds}s ago"
    if seconds < 5400:
        return f"{seconds // 60}m ago"
    if seconds < 172_800:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86_400}d ago"


def _render_expansions(expansions: list[Expansion]) -> None:
    """One line per crawl rule, plus the warning a truncated rule owes the user.

    The warning is printed on cached runs too: filtering re-runs on every
    invocation, so ``matched`` is always the real count, never a stale one.
    """
    for expansion in expansions:
        origin = expansion.source
        if expansion.cached:
            origin += f", cached {_age(expansion.fetched_at)}"
        # Rule strings are full of square brackets, which Rich would read as markup
        # and silently swallow — `…/*[except=blog]` would print as `…/*`.
        raw = escape(expansion.rule.raw)
        console.print(
            f"[dim]rule[/dim] {raw} "
            f"[dim]→[/dim] {len(expansion.urls)} URL(s) [dim]({origin})[/dim]"
        )
        if expansion.dropped:
            cap = expansion.rule.max_urls
            err_console.print(
                f"[yellow]warning:[/yellow] {raw} matched "
                f"{expansion.matched} URLs; keeping the first {cap}. "
                "Raise [bold]max=[/bold] or narrow with [bold]level=[/bold] / "
                "[bold]except=[/bold]."
            )


def _render_plan(plan_, *, dry_run: bool, expansions: list[Expansion] | None = None) -> None:
    """Print the plan (or its results) and a summary line."""
    if expansions:
        _render_expansions(expansions)

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
