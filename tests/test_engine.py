"""The policy decision table and the never-delete invariant."""

from __future__ import annotations

from notebooklm_sync import engine
from notebooklm_sync.models import (
    Action,
    ManifestEntry,
    NotebookConfig,
    Outcome,
    RemoteSource,
    SyncPolicy,
    WaitStatus,
)

NOTEBOOK = NotebookConfig(name="research", notebook_id="nb-1", manifest_path="x.yaml")


def existing(url="https://example.com/a", kind="web_page", source_id="s1"):
    return RemoteSource(id=source_id, url=url, kind=kind, status="READY")


def test_unmatched_url_is_added():
    plan = engine.plan(NOTEBOOK, [ManifestEntry(url="https://example.com/new")], [])
    assert [a.action for a in plan.actions] == [Action.ADD]


def test_match_with_skip_policy():
    plan = engine.plan(
        NOTEBOOK,
        [ManifestEntry(url="https://example.com/a")],
        [existing()],
        default_policy=SyncPolicy.SKIP,
    )
    assert [a.action for a in plan.actions] == [Action.SKIP]
    assert plan.actions[0].source_id == "s1"


def test_match_with_override_policy_refreshes_in_place():
    plan = engine.plan(
        NOTEBOOK,
        [ManifestEntry(url="https://example.com/a")],
        [existing()],
        default_policy=SyncPolicy.OVERRIDE,
    )
    assert plan.actions[0].action is Action.REFRESH
    assert plan.actions[0].source_id == "s1"


def test_override_degrades_to_skip_for_unrefreshable_kind():
    plan = engine.plan(
        NOTEBOOK,
        [ManifestEntry(url="https://example.com/a")],
        [existing(kind="pasted_text")],
        default_policy=SyncPolicy.OVERRIDE,
    )
    assert plan.actions[0].action is Action.SKIP
    assert "cannot be refreshed" in plan.actions[0].reason


def test_match_with_create_policy_adds_duplicate():
    plan = engine.plan(
        NOTEBOOK,
        [ManifestEntry(url="https://example.com/a")],
        [existing()],
        default_policy=SyncPolicy.CREATE,
    )
    assert plan.actions[0].action is Action.ADD


def test_matching_is_by_normalized_url():
    plan = engine.plan(
        NOTEBOOK,
        [ManifestEntry(url="https://www.example.com/a/?utm_source=news")],
        [existing(url="https://example.com/a")],
        default_policy=SyncPolicy.SKIP,
    )
    assert plan.actions[0].action is Action.SKIP


def test_orphans_are_reported_never_deleted():
    plan = engine.plan(NOTEBOOK, [], [existing(url="https://example.com/old")])
    orphans = plan.of(Action.ORPHAN)
    assert len(orphans) == 1
    assert orphans[0].source_id == "s1"


def test_per_source_policy_beats_notebook_and_global():
    notebook = NotebookConfig(
        name="research", notebook_id="nb-1", manifest_path="x.yaml", policy=SyncPolicy.SKIP
    )
    plan = engine.plan(
        notebook,
        [ManifestEntry(url="https://example.com/a", policy=SyncPolicy.OVERRIDE)],
        [existing()],
        default_policy=SyncPolicy.CREATE,
    )
    assert plan.actions[0].action is Action.REFRESH


def test_notebook_policy_beats_global():
    notebook = NotebookConfig(
        name="research", notebook_id="nb-1", manifest_path="x.yaml", policy=SyncPolicy.OVERRIDE
    )
    plan = engine.plan(
        notebook,
        [ManifestEntry(url="https://example.com/a")],
        [existing()],
        default_policy=SyncPolicy.SKIP,
    )
    assert plan.actions[0].action is Action.REFRESH


# -- execute ------------------------------------------------------------------


class StubClient:
    """Records calls; lets a test dictate what `wait_source` returns."""

    def __init__(self, wait_status=WaitStatus.READY, wait_message=""):
        self.added: list[str] = []
        self.refreshed: list[str] = []
        self.deleted: list[str] = []
        self._wait = (wait_status, wait_message)

    def add_source(self, notebook_id, url, *, title=None, type_=None):
        self.added.append(url)
        return RemoteSource(id="new-1", url=url)

    def refresh_source(self, notebook_id, source_id):
        self.refreshed.append(source_id)
        return {}

    def wait_source(self, notebook_id, source_id, *, timeout=120):
        from notebooklm_sync.nlm import WaitResult

        return WaitResult(self._wait[0], self._wait[1])


def test_execute_add_then_wait_ready():
    plan = engine.plan(NOTEBOOK, [ManifestEntry(url="https://example.com/new")], [])
    client = StubClient(WaitStatus.READY)
    engine.execute(plan, client)
    assert client.added == ["https://example.com/new"]
    assert plan.actions[0].outcome is Outcome.OK


def test_wait_timeout_is_pending_not_failed():
    # `source wait` exit 2 means still processing; the next run reconciles it.
    plan = engine.plan(NOTEBOOK, [ManifestEntry(url="https://example.com/new")], [])
    engine.execute(plan, StubClient(WaitStatus.TIMEOUT, "still processing"))
    assert plan.actions[0].outcome is Outcome.PENDING


def test_wait_failure_is_failed():
    plan = engine.plan(NOTEBOOK, [ManifestEntry(url="https://example.com/new")], [])
    engine.execute(plan, StubClient(WaitStatus.FAILED, "bad url"))
    assert plan.actions[0].outcome is Outcome.FAILED


def test_execute_never_deletes_orphans():
    plan = engine.plan(NOTEBOOK, [], [existing(url="https://example.com/old")])
    client = StubClient()
    engine.execute(plan, client)
    assert client.deleted == []
    assert not hasattr(client, "delete_source") or client.deleted == []
    assert plan.actions[0].outcome is Outcome.SKIPPED


def test_one_failure_does_not_abort_the_rest():
    from notebooklm_sync.errors import NlmError

    class FlakyClient(StubClient):
        def add_source(self, notebook_id, url, *, title=None, type_=None):
            if "bad" in url:
                raise NlmError("BAD", "could not fetch")
            return super().add_source(notebook_id, url, title=title, type_=type_)

    plan = engine.plan(
        NOTEBOOK,
        [
            ManifestEntry(url="https://example.com/bad"),
            ManifestEntry(url="https://example.com/good"),
        ],
        [],
    )
    client = FlakyClient()
    engine.execute(plan, client)
    assert plan.actions[0].outcome is Outcome.FAILED
    assert plan.actions[1].outcome is Outcome.OK
    assert client.added == ["https://example.com/good"]


def test_plan_carries_the_matched_kind():
    # The mirror can only record a kind if plan() copies the one it matched against.
    plan = engine.plan(
        NOTEBOOK,
        [ManifestEntry(url="https://example.com/a")],
        [existing(kind="youtube")],
        default_policy=SyncPolicy.SKIP,
    )
    assert plan.actions[0].kind == "youtube"


def test_execute_learns_the_kind_from_the_add_response():
    class TypedClient(StubClient):
        def add_source(self, notebook_id, url, *, title=None, type_=None):
            self.added.append(url)
            return RemoteSource(id="new-1", url=url, kind="web_page")

    plan = engine.plan(NOTEBOOK, [ManifestEntry(url="https://example.com/new")], [])
    engine.execute(plan, TypedClient())
    assert plan.actions[0].kind == "web_page"


def test_execute_falls_back_to_the_manifest_type():
    plan = engine.plan(
        NOTEBOOK, [ManifestEntry(url="https://youtu.be/abc", type="youtube")], []
    )
    engine.execute(plan, StubClient())  # its add_source returns no kind
    assert plan.actions[0].kind == "youtube"


def test_add_receives_original_url_not_normalized():
    raw = "https://www.example.com/a/?utm_source=news"
    plan = engine.plan(NOTEBOOK, [ManifestEntry(url=raw)], [])
    client = StubClient()
    engine.execute(plan, client)
    assert client.added == [raw]


# -- apply_stale_filter -------------------------------------------------------


class StaleClient(StubClient):
    """Answers `is_stale` from a dict, or raises when told to."""

    def __init__(self, answers: dict, error: Exception | None = None):
        super().__init__()
        self.answers = answers
        self.error = error
        self.probed: list[str] = []

    def is_stale(self, notebook_id, source_id):
        self.probed.append(source_id)
        if self.error is not None:
            raise self.error
        return self.answers.get(source_id)


def _override_plan(*sources):
    return engine.plan(
        NOTEBOOK,
        [ManifestEntry(url=s.url) for s in sources],
        list(sources),
        default_policy=SyncPolicy.OVERRIDE,
    )


def test_stale_filter_skips_a_fresh_source():
    plan = _override_plan(existing())
    client = StaleClient({"s1": False})
    engine.apply_stale_filter(plan, client)
    assert plan.actions[0].action is Action.SKIP
    assert plan.actions[0].reason == "not stale"

    engine.execute(plan, client)
    assert client.refreshed == []


def test_stale_filter_keeps_a_stale_source():
    plan = _override_plan(existing())
    client = StaleClient({"s1": True})
    engine.apply_stale_filter(plan, client)
    assert plan.actions[0].action is Action.REFRESH

    engine.execute(plan, client)
    assert client.refreshed == ["s1"]


def test_stale_filter_fails_open_on_unusable_answer():
    # No `stale` field in the payload — refresh anyway rather than silently skip.
    plan = _override_plan(existing())
    engine.apply_stale_filter(plan, StaleClient({"s1": None}))
    assert plan.actions[0].action is Action.REFRESH


def test_stale_filter_fails_open_on_probe_error():
    from notebooklm_sync.errors import NlmError

    plan = _override_plan(existing())
    engine.apply_stale_filter(plan, StaleClient({}, error=NlmError("BOOM", "probe died")))
    assert plan.actions[0].action is Action.REFRESH
    assert "refreshing anyway" in plan.actions[0].message


def test_stale_filter_ignores_non_refresh_actions():
    # Under `skip` there are no REFRESH actions, so nothing is probed at all.
    plan = engine.plan(
        NOTEBOOK,
        [ManifestEntry(url="https://example.com/a"), ManifestEntry(url="https://example.com/new")],
        [existing()],
        default_policy=SyncPolicy.SKIP,
    )
    client = StaleClient({"s1": False})
    engine.apply_stale_filter(plan, client)
    assert client.probed == []
    assert [a.action for a in plan.actions] == [Action.SKIP, Action.ADD]
