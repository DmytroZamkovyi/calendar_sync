"""Adapter between the sync core and a Home Assistant calendar entity.

This module has no Home Assistant imports: all access to the entity goes through
the small `EventAccess` protocol (implemented in `ha_calendar.py`), so the logic
is testable without Home Assistant.

Why event ids come from `uid` + `recurrence_id` and why the entity is used
directly: the `calendar.get_events` service does not return `uid`, and updating
or deleting an event is only possible through the entity methods
(`async_update_event`, `async_delete_event`), which need the `uid`.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from datetime import tzinfo
from typing import Any, Protocol

from .core.models import Capabilities, EventFields, Window, to_datetime

ID_SEP = "\x1f"
HASH_PREFIX = "h:"


class AdapterError(Exception):
    """A calendar could not be read or written (entity missing, rejected, ...)."""


class EventAccess(Protocol):
    """What the adapter needs from a calendar entity."""

    def capabilities(self) -> Capabilities: ...

    async def get_events(self, start: Any, end: Any) -> list[Any]:
        """Events overlapping [start, end] as objects with the attributes of
        Home Assistant's `CalendarEvent` (uid, recurrence_id, summary, start, end,
        description, location)."""

    async def create(self, fields: EventFields) -> None: ...

    async def update(self, uid: str, recurrence_id: str | None, fields: EventFields) -> None: ...

    async def delete(self, uid: str, recurrence_id: str | None) -> None: ...


def to_payload(fields: EventFields) -> dict[str, Any]:
    """Arguments of the entity create/update methods (rfc5545 field names)."""
    return {
        "dtstart": fields.start,
        "dtend": fields.end,
        "summary": fields.summary,
        "description": fields.description,
        "location": fields.location,
    }


def fields_from_event(event: Any) -> EventFields:
    return EventFields(
        summary=event.summary or "",
        start=event.start,
        end=event.end,
        description=event.description or "",
        location=event.location or "",
    )


def make_event_id(event: Any) -> str:
    """Stable id of one event occurrence inside a calendar.

    Events without a uid (read-only calendars) get a content hash so they can still
    act as a source; such ids cannot be used for writing.
    """
    uid = getattr(event, "uid", None)
    recurrence_id = getattr(event, "recurrence_id", None)
    if uid:
        return f"{uid}{ID_SEP}{recurrence_id}" if recurrence_id else uid
    raw = f"{event.summary}|{event.start.isoformat()}|{event.end.isoformat()}"
    return HASH_PREFIX + hashlib.sha1(raw.encode()).hexdigest()  # noqa: S324 - not security


class NativeCalendarAdapter:
    """`CalendarAdapter` for any calendar entity, plus reading for the caller."""

    def __init__(
        self,
        access: EventAccess,
        tz: tzinfo,
        *,
        find_attempts: int = 3,
        find_delay: float = 1.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._access = access
        self._tz = tz
        self._attempts = find_attempts
        self._delay = find_delay
        self._sleep = sleep
        self._index: dict[str, tuple[str | None, str | None]] = {}

    @property
    def capabilities(self) -> Capabilities:
        return self._access.capabilities()

    async def read(self, window: Window) -> dict[str, EventFields]:
        """Events of the window keyed by event id; remembers uids for later writes."""
        events = await self._access.get_events(window.start, window.end)
        index: dict[str, tuple[str | None, str | None]] = {}
        result: dict[str, EventFields] = {}
        for event in events:
            event_id = make_event_id(event)
            result[event_id] = fields_from_event(event)
            index[event_id] = (getattr(event, "uid", None), getattr(event, "recurrence_id", None))
        self._index = index
        return result

    def _lookup(self, event_id: str) -> tuple[str, str | None]:
        uid, recurrence_id = self._index.get(event_id, (None, None))
        if not uid:
            raise AdapterError(f"event {event_id!r} has no uid or was not read in this cycle")
        return uid, recurrence_id

    async def create(self, fields: EventFields) -> tuple[str, EventFields]:
        """Create an event and return its id and stored fields.

        The entity API returns nothing, so the new event is found by reading the
        event's own time span and picking the matching event that was not known.
        If it cannot be found the error is reported; the next cycle then adopts the
        existing event by matching summary and time instead of creating a duplicate.
        """
        known = set(self._index)
        await self._access.create(fields)
        start = to_datetime(fields.start, self._tz)
        end = to_datetime(fields.end, self._tz)
        for attempt in range(self._attempts):
            if attempt:
                await self._sleep(self._delay)
            for event in await self._access.get_events(start, end):
                event_id = make_event_id(event)
                if event_id in known:
                    continue
                stored = fields_from_event(event)
                if stored.summary == fields.summary and stored.get("when") == fields.get("when"):
                    self._index[event_id] = (
                        getattr(event, "uid", None),
                        getattr(event, "recurrence_id", None),
                    )
                    return event_id, stored
        raise AdapterError(f"created event {fields.summary!r} not found after creation")

    async def update(self, event_id: str, fields: EventFields) -> EventFields | None:
        uid, recurrence_id = self._lookup(event_id)
        await self._access.update(uid, recurrence_id, fields)
        return None

    async def delete(self, event_id: str) -> None:
        uid, recurrence_id = self._lookup(event_id)
        await self._access.delete(uid, recurrence_id)
        self._index.pop(event_id, None)
