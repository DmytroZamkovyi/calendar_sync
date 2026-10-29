"""«Sync now» button of a pair."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
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
        async_add_entities([SyncNowButton(coordinator)], config_subentry_id=subentry_id)


class SyncNowButton(PairEntity, ButtonEntity):
    def __init__(self, coordinator: PairCoordinator) -> None:
        super().__init__(coordinator, "sync_now")

    async def async_press(self) -> None:
        await self.coordinator.async_sync_now()
