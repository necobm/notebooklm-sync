"""SQLite schema, audit log, and summary rollup."""

from __future__ import annotations

from notebooklm_sync import db as store
from notebooklm_sync.models import Action, Outcome, PlannedAction, SyncSummary


def test_connect_is_idempotent(db_path):
    store.connect(db_path).close()
    conn = store.connect(db_path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == store.SCHEMA_VERSION


def test_upsert_notebook_returns_stable_pk(db_path):
    conn = store.connect(db_path)
    first = store.upsert_notebook(conn, "research", "nb-1")
    second = store.upsert_notebook(conn, "research", "nb-1")
    assert first == second


def test_run_and_events_are_recorded(db_path):
    conn = store.connect(db_path)
    pk = store.upsert_notebook(conn, "research", "nb-1")
    run_id = store.start_run(conn, pk, "skip", dry_run=False)

    store.record_event(
        conn,
        run_id,
        PlannedAction(action=Action.ADD, url="https://example.com/a", outcome=Outcome.OK),
    )
    store.finish_run(conn, run_id, SyncSummary(added=1), "ok")

    rows = conn.execute("SELECT action, outcome, url FROM sync_events").fetchall()
    assert rows[0]["action"] == "add"
    assert rows[0]["outcome"] == "ok"

    run = conn.execute("SELECT * FROM sync_runs WHERE id = ?", (run_id,)).fetchone()
    assert run["status"] == "ok" and run["n_added"] == 1 and run["finished_at"]


def test_dry_run_is_flagged(db_path):
    conn = store.connect(db_path)
    pk = store.upsert_notebook(conn, "research", "nb-1")
    run_id = store.start_run(conn, pk, "skip", dry_run=True)
    row = conn.execute("SELECT dry_run FROM sync_runs WHERE id = ?", (run_id,)).fetchone()
    assert row["dry_run"] == 1


def test_upsert_source_updates_in_place(db_path):
    conn = store.connect(db_path)
    pk = store.upsert_notebook(conn, "research", "nb-1")
    for status in ("PROCESSING", "READY"):
        store.upsert_source(
            conn,
            pk,
            source_id="s1",
            url="https://example.com/a",
            normalized_url="https://example.com/a",
            title="A",
            kind="web_page",
            status=status,
            last_action="add",
        )
    rows = conn.execute("SELECT status FROM sources WHERE source_id = 's1'").fetchall()
    assert len(rows) == 1 and rows[0]["status"] == "READY"


def test_upsert_source_keeps_a_known_kind(db_path):
    # A later run that could not determine the kind must not null what we know.
    conn = store.connect(db_path)
    pk = store.upsert_notebook(conn, "research", "nb-1")
    for kind in ("web_page", None):
        store.upsert_source(
            conn,
            pk,
            source_id="s1",
            url="https://example.com/a",
            normalized_url="https://example.com/a",
            title="A",
            kind=kind,
            status="ready",
            last_action="skip",
        )
    row = conn.execute("SELECT kind FROM sources WHERE source_id = 's1'").fetchone()
    assert row["kind"] == "web_page"


def test_history_is_append_only(db_path):
    conn = store.connect(db_path)
    pk = store.upsert_notebook(conn, "research", "nb-1")
    for _ in range(3):
        store.finish_run(conn, store.start_run(conn, pk, "skip", dry_run=False), SyncSummary(), "ok")
    assert len(store.recent_runs(conn, pk)) == 3


def test_summarize_counts_by_action_and_outcome():
    actions = [
        PlannedAction(action=Action.ADD, outcome=Outcome.OK),
        PlannedAction(action=Action.ADD, outcome=Outcome.PENDING),
        PlannedAction(action=Action.ADD, outcome=Outcome.FAILED),
        PlannedAction(action=Action.REFRESH, outcome=Outcome.OK),
        PlannedAction(action=Action.SKIP, outcome=Outcome.SKIPPED),
        PlannedAction(action=Action.ORPHAN, outcome=Outcome.SKIPPED),
    ]
    summary = store.summarize(actions)
    assert (summary.added, summary.pending, summary.failed) == (1, 1, 1)
    assert (summary.refreshed, summary.skipped, summary.orphans) == (1, 1, 1)


# -- discovery cache (schema v2) ----------------------------------------------


def test_schema_version_is_two(db_path):
    conn = store.connect(db_path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2


def test_a_v1_database_is_upgraded_in_place(db_path):
    """v1 -> v2 only adds a table, so reopening is the whole migration."""
    import sqlite3

    legacy = sqlite3.connect(db_path)
    legacy.execute("PRAGMA user_version = 1")
    legacy.commit()
    legacy.close()

    conn = store.connect(db_path)

    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM discovery_cache").fetchone()[0] == 0


def test_discovery_round_trips(db_path):
    from datetime import UTC, datetime

    conn = store.connect(db_path)
    now = datetime.now(UTC)
    store.put_discovery(conn, "key", "https://site.com/*", ["https://site.com/a"], "sitemap", now)

    urls, source, fetched_at = store.get_discovery(conn, "key", ttl=3600)

    assert urls == ["https://site.com/a"]
    assert source == "sitemap"
    assert fetched_at == now.replace(microsecond=0)


def test_discovery_put_replaces_an_earlier_entry(db_path):
    from datetime import UTC, datetime

    conn = store.connect(db_path)
    now = datetime.now(UTC)
    store.put_discovery(conn, "key", "r", ["https://site.com/a"], "sitemap", now)
    store.put_discovery(conn, "key", "r", ["https://site.com/b"], "crawl", now)

    urls, source, _ = store.get_discovery(conn, "key", ttl=3600)

    assert urls == ["https://site.com/b"]
    assert source == "crawl"
    assert conn.execute("SELECT COUNT(*) FROM discovery_cache").fetchone()[0] == 1


def test_an_expired_entry_reads_as_absent_but_is_not_deleted(db_path):
    from datetime import UTC, datetime, timedelta

    conn = store.connect(db_path)
    old = datetime.now(UTC) - timedelta(days=2)
    store.put_discovery(conn, "key", "r", ["https://site.com/a"], "sitemap", old)

    assert store.get_discovery(conn, "key", ttl=3600) is None
    assert conn.execute("SELECT COUNT(*) FROM discovery_cache").fetchone()[0] == 1


def test_an_unknown_key_reads_as_none(db_path):
    conn = store.connect(db_path)
    assert store.get_discovery(conn, "nope", ttl=3600) is None
