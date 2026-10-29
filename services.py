"""Services: sync_now, preview, resolve_conflict. Target = any entity of a pair."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .coordinator import PairCoordinator
from .core.models import CONFLICT_CALENDAR1, CONFLICT_CALENDAR2

SERVICE_SYNC_NOW = "sync_now"
SERVICE_PREVIEW = "preview"
SERVICE_RESOLVE_CONFLICT = "resolve_conflict"


def _pairs(hass: HomeAssistant, call: ServiceCall) -> list[PairCoordinator]:
    raw = call.data.get(ATTR_ENTITY_ID)
    ids = [raw] if isinstance(raw, str) else list(raw or [])
    if not ids:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_target")
    registry = er.async_get(hass)
    found: dict[str, PairCoordinator] = {}
    for entity_id in ids:
        entry = registry.async_get(entity_id)
        coordinator = None
        if entry and entry.platform == DOMAIN and entry.config_subentry_id:
            config_entry = hass.config_entries.async_get_entry(entry.config_entry_id)
            if config_entry and config_entry.state is ConfigEntryState.LOADED:
                coordinator = config_entry.runtime_data.pairs.get(entry.config_subentry_id)
        if coordinator is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="not_a_pair_entity",
                translation_placeholders={"entity_id": str(entity_id)},
            )
        found[coordinator.subentry.subentry_id] = coordinator
    return list(found.values())


def async_register_services(hass: HomeAssistant) -> None:
    async def sync_now(call: ServiceCall) -> None:
        for pair in _pairs(hass, call):
            await pair.async_sync_now(confirm_deletions=bool(call.data.get("confirm_deletions")))

    async def preview(call: ServiceCall) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for pair in _pairs(hass, call):
            status = await pair.async_cycle(dry_run=True)
            c1, c2 = pair.calendars
            out[pair.subentry.title] = {
                "created": status.created,
                "updated": status.updated,
                "deleted": status.deleted,
                "adopted": status.adopted,
                "blocked_deletions": status.blocked_deletions,
                "conflicts": status.conflicts,
                "errors": status.errors,
                "operations": [
                    {
                        "operation": op.kind,
                        "calendar": c1 if op.side == 1 else c2,
                        "event": op.summary,
                        "fields": list(op.fields),
                    }
                    for op in pair.last_operations
                ],
            }
        return out

    async def resolve_conflict(call: ServiceCall) -> None:
        winner = call.data.get("winner")
        if winner not in (CONFLICT_CALENDAR1, CONFLICT_CALENDAR2):
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="bad_winner")
        for pair in _pairs(hass, call):
            await pair.async_sync_now(conflict_policy=winner)

    hass.services.async_register(DOMAIN, SERVICE_SYNC_NOW, sync_now)
    hass.services.async_register(
        DOMAIN, SERVICE_PREVIEW, preview, supports_response=SupportsResponse.ONLY
    )
    hass.services.async_register(DOMAIN, SERVICE_RESOLVE_CONFLICT, resolve_conflict)
