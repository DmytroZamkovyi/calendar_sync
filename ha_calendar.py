"""Home Assistant side of the calendar adapter: access to a calendar entity.

This is the only place that touches Home Assistant's calendar integration. It uses
the calendar entity object the same way Home Assistant's own calendar websocket
and REST handlers do (`hass.data[DATA_COMPONENT].get_entity`), because the public
services cannot do what sync needs: `calendar.get_events` has no `uid`, and there is
no service to update or delete an event. If core changes, only this file changes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.calendar import CalendarEntityFeature
from homeassistant.components.calendar.const import DATA_COMPONENT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .adapter import AdapterError, NativeCalendarAdapter, to_payload
from .core.models import Capabilities, EventFields

_ERRORS = (HomeAssistantError, ValueError, NotImplementedError)


def _local(payload: dict[str, Any]) -> dict[str, Any]:
    """Timed events are passed in the local time zone, as the calendar services do."""
    return {
        key: dt_util.as_local(value) if isinstance(value, datetime) else value
        for key, value in payload.items()
    }


class EntityAccess:
    """`EventAccess` for one calendar entity id."""

    def __init__(self, hass: HomeAssistant, entity_id: str) -> None:
        self._hass = hass
        self._entity_id = entity_id

    def _entity(self) -> Any:
        component = self._hass.data.get(DATA_COMPONENT)
        entity = component.get_entity(self._entity_id) if component else None
        if entity is None:
            raise AdapterError(f"calendar {self._entity_id} not found")
        if not getattr(entity, "available", True):
            raise AdapterError(f"calendar {self._entity_id} is unavailable")
        return entity

    def capabilities(self) -> Capabilities:
        features = self._entity().supported_features or 0
        return Capabilities(
            create=bool(features & CalendarEntityFeature.CREATE_EVENT),
            update=bool(features & CalendarEntityFeature.UPDATE_EVENT),
            delete=bool(features & CalendarEntityFeature.DELETE_EVENT),
        )

    async def get_events(self, start: datetime, end: datetime) -> list[Any]:
        entity = self._entity()
        try:
            return list(
                await entity.async_get_events(
                    self._hass, dt_util.as_local(start), dt_util.as_local(end)
                )
            )
        except _ERRORS as err:
            raise AdapterError(f"reading {self._entity_id} failed: {err}") from err

    async def create(self, fields: EventFields) -> None:
        entity = self._entity()
        try:
            await entity.async_create_event(**_local(to_payload(fields)))
        except _ERRORS as err:
            raise AdapterError(f"creating event in {self._entity_id} failed: {err}") from err

    async def update(self, uid: str, recurrence_id: str | None, fields: EventFields) -> None:
        entity = self._entity()
        try:
            await entity.async_update_event(
                uid, _local(to_payload(fields)), recurrence_id=recurrence_id
            )
        except _ERRORS as err:
            raise AdapterError(f"updating event in {self._entity_id} failed: {err}") from err

    async def delete(self, uid: str, recurrence_id: str | None) -> None:
        entity = self._entity()
        try:
            await entity.async_delete_event(uid, recurrence_id=recurrence_id)
        except _ERRORS as err:
            raise AdapterError(f"deleting event in {self._entity_id} failed: {err}") from err


def native_adapter(hass: HomeAssistant, entity_id: str) -> NativeCalendarAdapter:
    """Adapter for a calendar entity, in Home Assistant's configured time zone."""
    return NativeCalendarAdapter(EntityAccess(hass, entity_id), dt_util.get_default_time_zone())
