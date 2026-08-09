"""SQLite persistence: the local mirror of notebook state plus an audit log.

``sources`` is what we believe is in the notebook. ``sync_runs`` / ``sync_events``
are append-only history — never rewritten, so ``notebooklm-sync history`` can show
what actually happened.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from .models import Action, Outcome, PlannedAction, SyncSummary

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS notebooks (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    notebook_id   TEXT NOT NULL,
    title         TEXT,
    first_seen_at TEXT NOT NULL,
    last_synced_at TEXT
);

CREATE TABLE IF NOT EXISTS sources (
    id             INTEGER PRIMARY KEY,
    notebook_id    INTEGER NOT NULL REFERENCES notebooks(id) ON DELETE CASCADE,
    source_id      TEXT NOT NULL,
    url            TEXT,
    normalized_url TEXT,
    title          TEXT,
    kind           TEXT,
    status         TEXT,
    first_added_at TEXT NOT NULL,
    last_seen_at   TEXT NOT NULL,
    last_action    TEXT,
    UNIQUE (notebook_id, source_id)
);

CREATE INDEX IF NOT EXISTS idx_sources_normalized
    ON sources (notebook_id, normalized_url);

CREATE TABLE IF NOT EXISTS sync_runs (
    id           INTEGER PRIMARY KEY,
    notebook_id  INTEGER NOT NULL REFERENCES notebooks(id) ON DELETE CASCADE,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    policy       TEXT NOT NULL,
    dry_run      INTEGER NOT NULL DEFAULT 0,
    status       TEXT,
    n_added      INTEGER NOT NULL DEFAULT 0,
    n_refreshed  INTEGER NOT NULL DEFAULT 0,
    n_skipped    INTEGER NOT NULL DEFAULT 0,
    n_pending    INTEGER NOT NULL DEFAULT 0,
    n_failed     INTEGER NOT NULL DEFAULT 0,
    n_orphans    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sync_events (
    id         INTEGER PRIMARY KEY,
    run_id     INTEGER NOT NULL REFERENCES sync_runs(id) ON DELETE CASCADE,
    url        TEXT,
    source_id  TEXT,
    action     TEXT NOT NULL,
    outcome    TEXT,
    message    TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_run ON sync_events (run_id);

-- Discovered URLs for one crawl rule (schema v2). Deliberately not tied to a
-- notebook: a rule resolves to the same pages whoever asked, so two notebooks
-- sharing a rule share one fetch.
CREATE TABLE IF NOT EXISTS discovery_cache (
    rule_key   TEXT PRIMARY KEY,
    rule       TEXT NOT NULL,
    urls       TEXT NOT NULL,
    source     TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open the database, creating parent directories and applying the schema."""
    path = Path(db_path)
    if path.parent and str(path.parent) not in ("", "."):
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create tables and stamp the schema version. Idempotent.

    v1 -> v2 added ``discovery_cache``, which needs no migration branch: every
    statement in ``SCHEMA`` is ``CREATE ... IF NOT EXISTS``, so an existing v1
    database gains the table and the stamp on its next open.
    """
    conn.executescript(SCHEMA)
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current < SCHEMA_VERSION:
        # Future migrations branch on `current` here before stamping.
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def get_discovery(
    conn: sqlite3.Connection, rule_key: str, ttl: int
) -> tuple[list[str], str, datetime] | None:
    """Return ``(urls, source, fetched_at)`` for ``rule_key`` if still fresh.

    A row older than ``ttl`` seconds is treated as absent rather than deleted — the
    next successful fetch overwrites it, and a failed one leaves the stale row where
    a human can still see what was last discovered.
    """
    row = conn.execute(
        "SELECT urls, source, fetched_at FROM discovery_cache WHERE rule_key = ?",
        (rule_key,),
    ).fetchone()
    if row is None:
        return None

    fetched_at = datetime.fromisoformat(row["fetched_at"])
    if (datetime.now(UTC) - fetched_at).total_seconds() > ttl:
        return None
    return json.loads(row["urls"]), row["source"], fetched_at


def put_discovery(
    conn: sqlite3.Connection,
    rule_key: str,
    rule: str,
    urls: list[str],
    source: str,
    fetched_at: datetime,
) -> None:
    """Store the URLs a rule discovered, replacing any earlier entry."""
    conn.execute(
        """
        INSERT INTO discovery_cache (rule_key, rule, urls, source, fetched_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(rule_key) DO UPDATE SET
            rule = excluded.rule,
            urls = excluded.urls,
            source = excluded.source,
            fetched_at = excluded.fetched_at
        """,
        (rule_key, rule, json.dumps(urls), source, fetched_at.isoformat(timespec="seconds")),
    )
    conn.commit()


def upsert_notebook(conn: sqlite3.Connection, name: str, notebook_id: str, title: str | None = None) -> int:
    """Return the local row id for a notebook, inserting it if new."""
    row = conn.execute("SELECT id FROM notebooks WHERE name = ?", (name,)).fetchone()
    if row:
        conn.execute(
            "UPDATE notebooks SET notebook_id = ?, title = COALESCE(?, title) WHERE id = ?",
            (notebook_id, title, row["id"]),
        )
        conn.commit()
        return int(row["id"])
    cursor = conn.execute(
        "INSERT INTO notebooks (name, notebook_id, title, first_seen_at) VALUES (?, ?, ?, ?)",
        (name, notebook_id, title, _now()),
    )
    conn.commit()
    return int(cursor.lastrowid)


def start_run(conn: sqlite3.Connection, notebook_pk: int, policy: str, *, dry_run: bool) -> int:
    cursor = conn.execute(
        "INSERT INTO sync_runs (notebook_id, started_at, policy, dry_run, status) "
        "VALUES (?, ?, ?, ?, 'running')",
        (notebook_pk, _now(), policy, int(dry_run)),
    )
    conn.commit()
    return int(cursor.lastrowid)


def record_event(conn: sqlite3.Connection, run_id: int, action: PlannedAction) -> None:
    conn.execute(
        "INSERT INTO sync_events (run_id, url, source_id, action, outcome, message, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            run_id,
            action.url,
            action.source_id,
            action.action.value,
            action.outcome.value if action.outcome else None,
            action.message or action.reason,
            _now(),
        ),
    )
    conn.commit()


def finish_run(conn: sqlite3.Connection, run_id: int, summary: SyncSummary, status: str) -> None:
    conn.execute(
        "UPDATE sync_runs SET finished_at = ?, status = ?, n_added = ?, n_refreshed = ?, "
        "n_skipped = ?, n_pending = ?, n_failed = ?, n_orphans = ? WHERE id = ?",
        (
            _now(),
            status,
            summary.added,
            summary.refreshed,
            summary.skipped,
            summary.pending,
            summary.failed,
            summary.orphans,
            run_id,
        ),
    )
    conn.commit()


def mark_notebook_synced(conn: sqlite3.Connection, notebook_pk: int) -> None:
    conn.execute("UPDATE notebooks SET last_synced_at = ? WHERE id = ?", (_now(), notebook_pk))
    conn.commit()


def upsert_source(
    conn: sqlite3.Connection,
    notebook_pk: int,
    *,
    source_id: str,
    url: str | None,
    normalized_url: str | None,
    title: str | None,
    kind: str | None,
    status: str | None,
    last_action: str | None,
) -> None:
    """Record what we believe is in the notebook. Never deletes rows."""
    now = _now()
    conn.execute(
        """
        INSERT INTO sources (notebook_id, source_id, url, normalized_url, title, kind,
                             status, first_added_at, last_seen_at, last_action)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (notebook_id, source_id) DO UPDATE SET
            url = excluded.url,
            normalized_url = excluded.normalized_url,
            title = excluded.title,
            -- `kind` is an immutable property we sometimes fail to learn, so a run
            -- that knows less must not null what an earlier one recorded. `title`
            -- and `status` are current state and keep overwriting.
            kind = COALESCE(excluded.kind, kind),
            status = excluded.status,
            last_seen_at = excluded.last_seen_at,
            last_action = excluded.last_action
        """,
        (
            notebook_pk,
            source_id,
            url,
            normalized_url,
            title,
            kind,
            status,
            now,
            now,
            last_action,
        ),
    )
    conn.commit()


def recent_runs(conn: sqlite3.Connection, notebook_pk: int | None = None, limit: int = 20) -> list[sqlite3.Row]:
    sql = (
        "SELECT r.*, n.name AS notebook_name FROM sync_runs r "
        "JOIN notebooks n ON n.id = r.notebook_id "
    )
    params: tuple = ()
    if notebook_pk is not None:
        sql += "WHERE r.notebook_id = ? "
        params = (notebook_pk,)
    sql += "ORDER BY r.id DESC LIMIT ?"
    return list(conn.execute(sql, (*params, limit)).fetchall())


def last_synced_at(conn: sqlite3.Connection, name: str) -> str | None:
    row = conn.execute("SELECT last_synced_at FROM notebooks WHERE name = ?", (name,)).fetchone()
    return row["last_synced_at"] if row else None


def summarize(actions: list[PlannedAction]) -> SyncSummary:
    """Roll planned/executed actions up into counts for ``sync_runs``."""
    summary = SyncSummary()
    for action in actions:
        if action.action is Action.ORPHAN:
            summary.orphans += 1
        elif action.outcome is Outcome.FAILED:
            summary.failed += 1
        elif action.outcome is Outcome.PENDING:
            summary.pending += 1
        elif action.action is Action.ADD:
            summary.added += 1
        elif action.action is Action.REFRESH:
            summary.refreshed += 1
        elif action.action is Action.SKIP:
            summary.skipped += 1
    return summary
