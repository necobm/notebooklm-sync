"""Core dataclasses and enums shared across the package."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class SyncPolicy(str, Enum):
    """What to do when a manifest URL already exists in the notebook."""

    SKIP = "skip"
    OVERRIDE = "override"
    CREATE = "create"


class Action(str, Enum):
    """A planned operation for one manifest entry (or one orphaned source)."""

    ADD = "add"
    REFRESH = "refresh"
    SKIP = "skip"
    ORPHAN = "orphan"


class Outcome(str, Enum):
    """The result of executing a planned action.

    ``PENDING`` is deliberately distinct from ``FAILED``: ``source wait`` exits 2 on
    timeout, meaning ingestion is merely slow. The next run reconciles it, and it does
    not make the run exit non-zero.
    """

    OK = "ok"
    PENDING = "pending"
    FAILED = "failed"
    SKIPPED = "skipped"


class WaitStatus(str, Enum):
    """Normalized result of ``notebooklm source wait`` (exit 0/1/2)."""

    READY = "ready"
    FAILED = "failed"
    TIMEOUT = "timeout"


#: Source kinds that ``notebooklm source refresh`` can act on. Refreshing anything
#: else (pasted text, uploaded files) is invalid, so ``override`` degrades to a skip.
REFRESHABLE_KINDS = frozenset(
    {
        "web_page",
        "youtube",
        "google_docs",
        "google_slides",
        "google_spreadsheet",
        "google_drive_audio",
        "google_drive_video",
    }
)


@dataclass(frozen=True)
class ManifestEntry:
    """One desired source, as declared in a notebook's YAML manifest."""

    url: str
    title: str | None = None
    type: str | None = None
    policy: SyncPolicy | None = None

    #: Comparison key only — never sent to ``source add``, which always gets ``url``.
    normalized_url: str = ""


@dataclass(frozen=True)
class RemoteSource:
    """A source as it currently exists in the notebook, per ``source list --json``."""

    id: str
    url: str | None = None
    title: str | None = None
    kind: str | None = None
    status: str | None = None
    created_at: datetime | None = None
    normalized_url: str | None = None

    @property
    def is_refreshable(self) -> bool:
        return (self.kind or "") in REFRESHABLE_KINDS


@dataclass(frozen=True)
class NotebookConfig:
    """A notebook declared in ``.env``."""

    name: str
    notebook_id: str
    manifest_path: str
    policy: SyncPolicy | None = None


@dataclass
class PlannedAction:
    """One decision produced by ``engine.plan()``."""

    action: Action
    url: str | None = None
    title: str | None = None
    type: str | None = None
    policy: SyncPolicy | None = None
    source_id: str | None = None
    reason: str = ""

    #: The source's kind, when known: copied from the matched ``RemoteSource``, or
    #: learned from the ``source add`` response. Recorded in the local mirror.
    kind: str | None = None

    outcome: Outcome | None = None
    message: str = ""


@dataclass
class SyncPlan:
    """The full set of decisions for one notebook, before any side effects."""

    notebook: NotebookConfig
    actions: list[PlannedAction] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)

    def of(self, action: Action) -> list[PlannedAction]:
        return [a for a in self.actions if a.action is action]


@dataclass
class SyncSummary:
    """Counts reported at the end of a run and persisted to ``sync_runs``."""

    added: int = 0
    refreshed: int = 0
    skipped: int = 0
    pending: int = 0
    failed: int = 0
    orphans: int = 0
