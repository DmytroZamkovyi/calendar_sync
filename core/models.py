"""Data model of the sync core. No Home Assistant imports."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, tzinfo
from typing import Any, Protocol

# Logical fields compared and merged independently. "when" groups start and end,
# so a time change is always applied (and conflicts are resolved) as one unit.
FIELD_NAMES = ("summary", "when", "description", "location")

CONFLICT_CALENDAR1 = "calendar1"
CONFLICT_CALENDAR2 = "calendar2"
CONFLICT_ASK = "ask"
CONFLICT_POLICIES = (CONFLICT_CALENDAR1, CONFLICT_CALENDAR2, CONFLICT_ASK)

CLOSED_IGNORE = "ignore"
CLOSED_REVERT = "revert"
CLOSED_MODES = (CLOSED_IGNORE, CLOSED_REVERT)


def _minute(value: Any) -> Any:
    """Times are compared to the minute: calendar backends (Google) drop seconds and
    fractions, so an exact comparison would never match what they store back."""
    if isinstance(value, datetime):
        return value.replace(second=0, microsecond=0)
    return value


@dataclass(frozen=True, slots=True)
class EventFields:
    """The synchronised content of one event."""

    summary: str
    start: date | datetime
    end: date | datetime
    description: str = ""
    location: str = ""

    @property
    def all_day(self) -> bool:
        return not isinstance(self.start, datetime)

    def get(self, name: str) -> Any:
        if name == "when":
            return (_minute(self.start), _minute(self.end))
        return getattr(self, name)

    def replace(self, changes: dict[str, Any]) -> EventFields:
        data: dict[str, Any] = {
            "summary": self.summary,
            "start": self.start,
            "end": self.end,
            "description": self.description,
            "location": self.location,
        }
        for name, value in changes.items():
            if name == "when":
                data["start"], data["end"] = value
            else:
                data[name] = value
        return EventFields(**data)

    def diff(self, other: EventFields) -> set[str]:
        """Names of logical fields whose values differ."""
        return {name for name in FIELD_NAMES if self.get(name) != other.get(name)}


@dataclass(frozen=True, slots=True)
class Capabilities:
    """What a calendar can do. Sides without `create` are read-only sources."""

    create: bool = True
    update: bool = True
    delete: bool = True


class CalendarAdapter(Protocol):
    """Write side of a calendar. Reading is done by the caller.

    `create` returns the id of the new event and the fields as actually stored.
    If the backend does not return an id, the adapter must resolve it itself.
    `update` receives the complete new fields and may return the stored fields.
    """

    capabilities: Capabilities

    async def create(self, fields: EventFields) -> tuple[str, EventFields]: ...

    async def update(self, event_id: str, fields: EventFields) -> EventFields | None: ...

    async def delete(self, event_id: str) -> None: ...


@dataclass(frozen=True, slots=True)
class Window:
    """Time window events are read for; events outside it are never touched."""

    start: datetime
    end: datetime


def to_datetime(value: date | datetime, tz: tzinfo) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=tz)
    return datetime.combine(value, time.min, tzinfo=tz)


def overlaps(fields: EventFields, window: Window, tz: tzinfo) -> bool:
    start = to_datetime(fields.start, tz)
    end = to_datetime(fields.end, tz)
    return start < window.end and end >= window.start


@dataclass(slots=True)
class PairConfig:
    """Settings of one synchronisation pair."""

    tz: tzinfo
    window: Window
    direction_1to2: bool = True
    direction_2to1: bool = True
    closed_mode: str = CLOSED_REVERT
    propagate_deletes: bool = True
    conflict_policy: str = CONFLICT_CALENDAR1
    rules_1to2: list = field(default_factory=list)
    rules_2to1: list = field(default_factory=list)
    delete_guard_min: int = 3
    delete_guard_ratio: float = 0.5

    def __post_init__(self) -> None:
        if not (self.direction_1to2 or self.direction_2to1):
            raise ValueError("at least one direction must be enabled")
        if self.closed_mode not in CLOSED_MODES:
            raise ValueError(f"unknown closed_mode: {self.closed_mode}")
        if self.conflict_policy not in CONFLICT_POLICIES:
            raise ValueError(f"unknown conflict_policy: {self.conflict_policy}")
