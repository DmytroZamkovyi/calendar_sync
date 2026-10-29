"""In-memory calendars for engine tests."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from custom_components.calendar_sync.core.engine import sync_pair
from custom_components.calendar_sync.core.models import (
    Capabilities,
    EventFields,
    PairConfig,
    Window,
)
from custom_components.calendar_sync.core.state import State

TZ = timezone.utc
T0 = datetime(2026, 11, 2, 9, 0, tzinfo=TZ)  # Monday
WINDOW = Window(T0 - timedelta(days=7), T0 + timedelta(days=30))


def ev(summary="Meeting", day=0, hour=9, minutes=60, **kw) -> EventFields:
    start = datetime(2026, 11, 2, hour, 0, tzinfo=TZ) + timedelta(days=day)
    return EventFields(summary, start, start + timedelta(minutes=minutes), **kw)


class FakeCalendar:
    def __init__(self, caps: Capabilities | None = None, prefix: str = "e") -> None:
        self.capabilities = caps or Capabilities()
        self.events: dict[str, EventFields] = {}
        self.calls: list[tuple] = []
        self.fail: set[str] = set()
        self._n = 0
        self._prefix = prefix

    def add(self, fields: EventFields) -> str:
        self._n += 1
        event_id = f"{self._prefix}{self._n}"
        self.events[event_id] = fields
        return event_id

    async def create(self, fields):
        if "create" in self.fail:
            raise RuntimeError("boom")
        self.calls.append(("create", fields.summary))
        return self.add(fields), fields

    async def update(self, event_id, fields):
        if "update" in self.fail:
            raise RuntimeError("boom")
        self.calls.append(("update", event_id))
        self.events[event_id] = fields
        return fields

    async def delete(self, event_id):
        self.calls.append(("delete", event_id))
        del self.events[event_id]


def cfg(**kw) -> PairConfig:
    return PairConfig(tz=TZ, window=WINDOW, **kw)


def cycle(config, state, cal1, cal2, **kw):
    """Run one cycle reading the fake calendars; returns (result, new_state)."""
    result = asyncio.run(
        sync_pair(config, state, dict(cal1.events), dict(cal2.events), cal1, cal2, **kw)
    )
    return result, result.state


def fresh() -> State:
    return State()
