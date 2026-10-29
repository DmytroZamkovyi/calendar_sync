"""Problem indicator of a pair."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CalendarSyncConfigEntry
from .coordinator import PairCoordinator
from .entity import PairEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CalendarSyncConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    for subentry_id, coordinator in entry.runtime_data.pairs.items():
        async_add_entities([OutOfSync(coordinator)], config_subentry_id=subentry_id)


class OutOfSync(PairEntity, BinarySensorEntity):
    """On when the last cycle had conflicts, errors or blocked deletions."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: PairCoordinator) -> None:
        super().__init__(coordinator, "out_of_sync")

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.out_of_sync

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        status = self.coordinator.data
        return {
            "conflicts": len(status.conflicts),
            "errors": len(status.errors),
            "blocked_deletions": status.blocked_deletions,
            "dry_run": status.dry_run,
        }
