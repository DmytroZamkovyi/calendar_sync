"""Result of one sync cycle of a pair, as shown by entities and Repairs.

No Home Assistant imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .core.engine import SyncResult

MAX_LISTED = 5
FAILURES_BEFORE_REPAIR = 3


@dataclass(slots=True)
class PairStatus:
    last_sync: datetime | None = None
    created: int = 0
    updated: int = 0
    deleted: int = 0
    adopted: int = 0
    blocked_deletions: int = 0
    conflicts: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False
    failures: int = 0  # consecutive cycles that ended with errors

    @property
    def out_of_sync(self) -> bool:
        return bool(self.conflicts or self.errors or self.blocked_deletions)


def status_from_result(
    result: SyncResult, now: datetime, previous: PairStatus | None, *, dry_run: bool
) -> PairStatus:
    failed = bool(result.errors)
    return PairStatus(
        last_sync=now,
        created=result.created,
        updated=result.updated,
        deleted=result.deleted,
        adopted=result.adopted,
        blocked_deletions=result.blocked_deletions,
        conflicts=[f"{c.summary} ({c.field})" for c in result.conflicts],
        errors=list(result.errors),
        dry_run=dry_run,
        failures=((previous.failures if previous else 0) + 1) if failed else 0,
    )


def status_from_failure(message: str, now: datetime, previous: PairStatus | None) -> PairStatus:
    """The cycle could not even read the calendars; keep the last counters."""
    base = previous or PairStatus()
    return PairStatus(
        last_sync=base.last_sync,
        created=base.created,
        updated=base.updated,
        deleted=base.deleted,
        adopted=base.adopted,
        blocked_deletions=base.blocked_deletions,
        conflicts=list(base.conflicts),
        errors=[message],
        dry_run=base.dry_run,
        failures=base.failures + 1,
    )


def short_list(items: list[str]) -> str:
    shown = items[:MAX_LISTED]
    text = "; ".join(shown)
    if len(items) > MAX_LISTED:
        text += f"; … (+{len(items) - MAX_LISTED})"
    return text
