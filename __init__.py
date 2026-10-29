"""Calendar Sync: two-way sync between any two Home Assistant calendars."""

from __future__ import annotations

from dataclasses import dataclass, field

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, SUBENTRY_PAIR
from .coordinator import PairCoordinator
from .services import async_register_services

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON]


@dataclass
class CalendarSyncData:
    pairs: dict[str, PairCoordinator] = field(default_factory=dict)  # subentry_id -> pair


type CalendarSyncConfigEntry = ConfigEntry[CalendarSyncData]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: CalendarSyncConfigEntry) -> bool:
    data = CalendarSyncData()
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type != SUBENTRY_PAIR:
            continue
        coordinator = PairCoordinator(hass, entry, subentry)
        await coordinator.async_load()
        data.pairs[subentry_id] = coordinator
    entry.runtime_data = data

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))

    # First cycle in the background: calendars of other integrations may not be ready yet.
    for coordinator in data.pairs.values():
        entry.async_create_background_task(
            hass, coordinator.async_refresh(), f"{DOMAIN} first sync {coordinator.name}"
        )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CalendarSyncConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload(hass: HomeAssistant, entry: CalendarSyncConfigEntry) -> None:
    """Pairs are subentries: any change (add, edit, delete, options) reloads the entry."""
    for subentry_id, coordinator in list(entry.runtime_data.pairs.items()):
        if subentry_id not in entry.subentries:  # pair was deleted: forget its link table
            coordinator.async_clear_issues()
            await coordinator.async_remove_store()
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_entry(hass: HomeAssistant, entry: CalendarSyncConfigEntry) -> None:
    for subentry_id in entry.subentries:
        await Store(hass, 1, f"{DOMAIN}.{subentry_id}").async_remove()
