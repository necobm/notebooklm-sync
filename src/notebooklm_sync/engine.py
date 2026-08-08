"""Reconciliation: decide what to do (``plan``), then do it (``execute``).

``--dry-run`` runs the *same* ``plan()`` and stops before ``execute()``. There is
deliberately no dry-run-only code path — a second path drifts from the real one and
then misreports what a real run would do.

Invariant: **sync never deletes.** Sources present in the notebook but absent from
the manifest are reported as orphans and left alone.
"""

from __future__ import annotations

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
                    reason="in notebook, not in manifest",
                )
            )

    return result


def execute(
    plan_: SyncPlan,
    client: NlmClient,
    *,
    wait: bool = True,
    wait_timeout: int = 120,
    on_action=None,
) -> list[PlannedAction]:
    """Perform the planned actions, annotating each with its outcome.

    A per-source failure never aborts the run: it is recorded and the next action
    proceeds. ``on_action``, if given, is called after each action completes.
    """
    notebook_id = plan_.notebook.notebook_id

    for action in plan_.actions:
        try:
            if action.action is Action.SKIP:
                action.outcome = Outcome.SKIPPED

            elif action.action is Action.ORPHAN:
                # Reported only. Never deleted — see the never-delete invariant.
                action.outcome = Outcome.SKIPPED

            elif action.action is Action.ADD:
                created = client.add_source(
                    notebook_id, action.url or "", title=action.title, type_=action.type
                )
                if created is not None:
                    action.source_id = created.id
                action.outcome = Outcome.OK

                if wait and action.source_id:
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
                client.refresh_source(notebook_id, action.source_id or "")
                action.outcome = Outcome.OK

        except NlmError as exc:
            action.outcome = Outcome.FAILED
            action.message = str(exc)

        if on_action is not None:
            on_action(action)

    return plan_.actions
