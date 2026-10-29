"""Diagnostics without event contents: settings, counters and link counts."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import CalendarSyncConfigEntry
from .const import CONF_CALENDAR_1, CONF_CALENDAR_2, CONF_RULES_1TO2, CONF_RULES_2TO1
from .pair import normalize_pair_data

REDACT = {CONF_CALENDAR_1, CONF_CALENDAR_2}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: CalendarSyncConfigEntry
) -> dict[str, Any]:
    pairs = []
    for subentry_id, coordinator in entry.runtime_data.pairs.items():
        settings = normalize_pair_data(coordinator.subentry.data)
        rules = {
            "rules_1to2": [r["action"]["kind"] for r in settings.pop(CONF_RULES_1TO2)],
            "rules_2to1": [r["action"]["kind"] for r in settings.pop(CONF_RULES_2TO1)],
        }
        status = coordinator.data
        pairs.append(
            {
                "settings": async_redact_data(settings, REDACT),
                "rule_actions": rules,
                "links": len(coordinator.state.links),
                "detached_links": sum(1 for l in coordinator.state.links.values() if l.detached),
                "last_sync": status.last_sync.isoformat() if status.last_sync else None,
                "created": status.created,
                "updated": status.updated,
                "deleted": status.deleted,
                "adopted": status.adopted,
                "blocked_deletions": status.blocked_deletions,
                "conflicts": len(status.conflicts),
                "errors": len(status.errors),
                "failures": status.failures,
            }
        )
    return {"options": dict(entry.options), "pairs": pairs}
