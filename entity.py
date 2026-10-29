"""Base entity of a pair: all entities of a pair share one device."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PairCoordinator


class PairEntity(CoordinatorEntity[PairCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: PairCoordinator, key: str) -> None:
        super().__init__(coordinator)
        subentry_id = coordinator.subentry.subentry_id
        self._attr_unique_id = f"{subentry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, subentry_id)},
            name=coordinator.subentry.title,
            entry_type=DeviceEntryType.SERVICE,
        )
