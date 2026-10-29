"""Sensors of a pair: last sync and counters of the last cycle."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CalendarSyncConfigEntry
from .coordinator import PairCoordinator
from .entity import PairEntity
from .status import PairStatus


@dataclass(frozen=True, kw_only=True)
class PairSensorDescription(SensorEntityDescription):
    value_fn: Callable[[PairStatus], datetime | int | None]
    attrs_fn: Callable[[PairStatus], dict[str, Any]] | None = None


def _counter(key: str, attr: str, **kw: Any) -> PairSensorDescription:
    return PairSensorDescription(
        key=key,
        translation_key=key,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: getattr(s, attr),
        **kw,
    )


SENSORS: tuple[PairSensorDescription, ...] = (
    PairSensorDescription(
        key="last_sync",
        translation_key="last_sync",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.last_sync,
    ),
    _counter("created", "created"),
    _counter("updated", "updated"),
    _counter("deleted", "deleted"),
    PairSensorDescription(
        key="conflicts",
        translation_key="conflicts",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: len(s.conflicts),
        attrs_fn=lambda s: {"items": s.conflicts},
    ),
    PairSensorDescription(
        key="errors",
        translation_key="errors",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: len(s.errors),
        attrs_fn=lambda s: {"items": s.errors},
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CalendarSyncConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    for subentry_id, coordinator in entry.runtime_data.pairs.items():
        async_add_entities(
            [PairSensor(coordinator, d) for d in SENSORS], config_subentry_id=subentry_id
        )


class PairSensor(PairEntity, SensorEntity):
    entity_description: PairSensorDescription

    def __init__(self, coordinator: PairCoordinator, description: PairSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> datetime | int | None:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attrs_fn
        return fn(self.coordinator.data) if fn else None
