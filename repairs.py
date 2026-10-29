"""Fixable Repair: confirm a blocked mass deletion."""

from __future__ import annotations

from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir

from .coordinator import PairCoordinator


class ConfirmDeletionsFlow(RepairsFlow):
    def __init__(self, coordinator: PairCoordinator) -> None:
        self._coordinator = coordinator

    async def async_step_init(self, user_input: dict[str, str] | None = None) -> FlowResult:
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, str] | None = None) -> FlowResult:
        if user_input is not None:
            await self._coordinator.async_sync_now(confirm_deletions=True)
            return self.async_create_entry(data={})
        issue = ir.async_get(self.hass).async_get_issue(self.handler, self.issue_id)
        return self.async_show_form(
            step_id="confirm",
            description_placeholders=issue.translation_placeholders if issue else None,
        )


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, str | int | float | None] | None
) -> RepairsFlow:
    entry = hass.config_entries.async_get_entry(str((data or {}).get("entry_id")))
    coordinator = None
    if entry is not None and hasattr(entry, "runtime_data"):
        coordinator = entry.runtime_data.pairs.get(str((data or {}).get("subentry_id")))
    if coordinator is None:
        raise HomeAssistantError("The sync pair of this issue is no longer set up")
    return ConfirmDeletionsFlow(coordinator)
