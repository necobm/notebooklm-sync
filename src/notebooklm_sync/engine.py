"""Reconciliation: decide what to do (``plan``), then do it (``execute``).

``--dry-run`` runs the *same* ``plan()`` and stops before ``execute()``. There is
deliberately no dry-run-only code path — a second path drifts from the real one and
then misreports what a real run would do.

Invariant: **sync never deletes.** Sources present in the notebook but absent from
the manifest are reported as orphans and left alone.
"""

from __future__ import annotations

from collections.abc import Callable

from .errors import NlmError
from .matching import index_sources, normalize_entry
from .models import (
    Action,
    ManifestEntry,
    NotebookConfig,
    Outcome,
    PlannedAction,
    RemoteSource,
    SyncPlan,
    SyncPolicy,
    WaitStatus,
)
from .nlm import NlmClient


def resolve_policy(
    entry: ManifestEntry,
    notebook: NotebookConfig,
    default: SyncPolicy,
) -> SyncPolicy:
    """First match wins: entry -> notebook -> global default."""
    return entry.policy or notebook.policy or default


def plan(
    notebook: NotebookConfig,
    entries: list[ManifestEntry],
    remote_sources: list[RemoteSource],
    *,
    default_policy: SyncPolicy = SyncPolicy.SKIP,
) -> SyncPlan:
    """Decide an action per manifest entry, plus orphans. Pure — no side effects."""
    index, duplicates = index_sources(remote_sources)
    result = SyncPlan(notebook=notebook, duplicates=duplicates)
    matched_ids: set[str] = set()

    for raw_entry in entries:
        entry = normalize_entry(raw_entry)
        policy = resolve_policy(entry, notebook, default_policy)
        match = index.get(entry.normalized_url)

        if match is None:
            result.actions.append(
                PlannedAction(
                    action=Action.ADD,
                    url=entry.url,
                    title=entry.title,
                    type=entry.type,
                    policy=policy,
                    reason="not in notebook",
                )
            )
            continue

        matched_ids.add(match.id)

        if policy is SyncPolicy.SKIP:
            result.actions.append(
                PlannedAction(
                    action=Action.SKIP,
                    url=entry.url,
                    title=entry.title or match.title,
                    policy=policy,
                    source_id=match.id,
                    kind=match.kind,
                    reason="already present",
                )
            )
        elif policy is SyncPolicy.OVERRIDE:
            if match.is_refreshable:
                result.actions.append(
                    PlannedAction(
                        action=Action.REFRESH,
                        url=entry.url,
                        title=entry.title or match.title,
                        policy=policy,
                        source_id=match.id,
                        kind=match.kind,
                        reason="override policy",
                    )
                )
            else:
                # refresh is only valid for URL/Drive-backed sources; degrade rather
                # than fail, so one odd source doesn't block the whole notebook.
                result.actions.append(
                    PlannedAction(
                        action=Action.SKIP,
                        url=entry.url,
                        title=entry.title or match.title,
                        policy=policy,
                        source_id=match.id,
                        kind=match.kind,
                        reason=f"kind {match.kind!r} cannot be refreshed",
                    )
                )
        else:  # SyncPolicy.CREATE
            result.actions.append(
                PlannedAction(
                    action=Action.ADD,
                    url=entry.url,
                    title=entry.title,
                    type=entry.type,
                    policy=policy,
                    reason="create policy (duplicate intended)",
                )
            )

    for source in remote_sources:
        if source.id not in matched_ids:
            result.actions.append(
                PlannedAction(
                    action=Action.ORPHAN,
                    url=source.url,
                    title=source.title,
                    source_id=source.id,
                    kind=source.kind,
                    reason="in notebook, not in manifest",
                )
            )

    return result


def apply_stale_filter(
    plan_: SyncPlan,
    client: NlmClient,
    *,
    on_probe: Callable[[PlannedAction], None] | None = None,
) -> None:
    """Narrow ``REFRESH`` actions to the sources upstream reports as stale.

    Read-only: it probes ``source stale`` and rewrites decisions, so it is safe to
    run under ``--dry-run`` — which is the point. Running it in both modes keeps the
    preview equal to the real run instead of introducing a second code path.

    It lives here rather than inside ``plan()`` because ``plan()`` is pure and holds
    no client. ``on_probe`` is announced *before* each probe; like ``NlmClient.on_call``
    it renders nothing, so this module stays free of any display.

    **Fails open:** an unusable answer (no ``stale`` field) or a failed probe leaves
    the refresh in place. Refreshing needlessly wastes a call; skipping wrongly
    leaves the notebook serving stale content, which is the thing we exist to stop.
    """
    notebook_id = plan_.notebook.notebook_id

    for action in plan_.actions:
        if action.action is not Action.REFRESH or not action.source_id:
            continue
        if on_probe is not None:
            on_probe(action)
        try:
            # The JSON field, never the exit code: `source stale --exit-on-stale`
            # inverts it (0 = stale).
            stale = client.is_stale(notebook_id, action.source_id)
        except NlmError as exc:
            action.message = f"staleness unknown ({exc}); refreshing anyway"
            continue
        if stale is False:
            action.action = Action.SKIP
            action.reason = "not stale"


def execute(
    plan_: SyncPlan,
    client: NlmClient,
    *,
    wait: bool = True,
    wait_timeout: int = 120,
    on_step: Callable[[PlannedAction, str], None] | None = None,
    on_action=None,
) -> list[PlannedAction]:
    """Perform the planned actions, annotating each with its outcome.

    A per-source failure never aborts the run: it is recorded and the next action
    proceeds.

    Two callbacks, both optional and both purely informational:

    * ``on_step(action, step)`` fires **before** each unit of work, with ``step`` one
      of ``"add"``, ``"wait"`` or ``"refresh"``. It has to fire first, because
      ``"wait"`` can block for ``wait_timeout`` seconds and is the longest thing this
      tool ever does — reporting it afterwards would report it once it no longer
      mattered.
    * ``on_action(action)`` fires after the whole action completes.

    Neither renders anything; that is ``cli.py``'s job, via ``progress.py``.
    """
    notebook_id = plan_.notebook.notebook_id

    def step(action: PlannedAction, name: str) -> None:
        if on_step is not None:
            on_step(action, name)

    for action in plan_.actions:
        try:
            if action.action is Action.SKIP:
                action.outcome = Outcome.SKIPPED

            elif action.action is Action.ORPHAN:
                # Reported only. Never deleted — see the never-delete invariant.
                action.outcome = Outcome.SKIPPED

            elif action.action is Action.ADD:
                step(action, "add")
                created = client.add_source(
                    notebook_id, action.url or "", title=action.title, type_=action.type
                )
                if created is not None:
                    action.source_id = created.id
                    # Upstream's own classification wins; the manifest's `type:` is
                    # only what we asked for.
                    action.kind = created.kind or action.type
                action.outcome = Outcome.OK

                if wait and action.source_id:
                    step(action, "wait")
                    result = client.wait_source(
                        notebook_id, action.source_id, timeout=wait_timeout
                    )
                    if result.status is WaitStatus.READY:
                        action.outcome = Outcome.OK
                    elif result.status is WaitStatus.TIMEOUT:
                        # Still processing — not a failure. Next run reconciles it.
                        action.outcome = Outcome.PENDING
                        action.message = result.message
                    else:
                        action.outcome = Outcome.FAILED
                        action.message = result.message

            elif action.action is Action.REFRESH:
                step(action, "refresh")
                client.refresh_source(notebook_id, action.source_id or "")
                action.outcome = Outcome.OK

        except NlmError as exc:
            action.outcome = Outcome.FAILED
            action.message = str(exc)

        if on_action is not None:
            on_action(action)

    return plan_.actions
