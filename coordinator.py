"""Sync cycle of one pair: read both calendars, run the engine, persist state."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .adapter import AdapterError
from .const import (
    CONF_CALENDAR_1,
    CONF_CALENDAR_2,
    CONF_CONFLICT_POLICY,
    CONF_DRY_RUN,
    CONF_INTERVAL,
    DEFAULT_INTERVAL,
    DOMAIN,
)
from .core.engine import Operation, SyncResult, sync_pair
from .core.state import State
from .ha_calendar import native_adapter
from .pair import normalize_pair_data, pair_config
from .status import (
    FAILURES_BEFORE_REPAIR,
    PairStatus,
    short_list,
    status_from_failure,
    status_from_result,
)

_LOGGER = logging.getLogger(__name__)
STORAGE_VERSION = 1


class PairCoordinator(DataUpdateCoordinator[PairStatus]):
    """One sync pair (a config subentry)."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, subentry: ConfigSubentry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {subentry.title}",
            update_interval=timedelta(minutes=entry.options.get(CONF_INTERVAL, DEFAULT_INTERVAL)),
        )
        self.subentry = subentry
        self.state = State()
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{subentry.subentry_id}"
        )
        self._lock = asyncio.Lock()
        self.last_operations: list[Operation] = []
        self.data = PairStatus()

    async def async_load(self) -> None:
        stored = await self._store.async_load()
        if stored:
            self.state = State.from_json(stored)

    async def async_remove_store(self) -> None:
        await self._store.async_remove()

    @property
    def calendars(self) -> tuple[str, str]:
        return self.subentry.data[CONF_CALENDAR_1], self.subentry.data[CONF_CALENDAR_2]

    # ----------------------------------------------------------------- cycle

    async def _async_update_data(self) -> PairStatus:
        return await self.async_cycle()

    async def async_cycle(
        self,
        *,
        confirm_deletions: bool = False,
        dry_run: bool | None = None,
        conflict_policy: str | None = None,
    ) -> PairStatus:
        """Run one cycle. Never raises: problems are reported in the status."""
        async with self._lock:
            data = normalize_pair_data(self.subentry.data)
            if conflict_policy:
                data[CONF_CONFLICT_POLICY] = conflict_policy
            dry = data[CONF_DRY_RUN] if dry_run is None else dry_run
            preview = dry_run is True  # explicit preview: leave Repairs untouched
            now = dt_util.now()
            cfg = pair_config(data, dt_util.get_default_time_zone(), now)
            c1, c2 = self.calendars
            adapter1, adapter2 = native_adapter(self.hass, c1), native_adapter(self.hass, c2)
            try:
                events1 = await adapter1.read(cfg.window)
                events2 = await adapter2.read(cfg.window)
            except AdapterError as err:
                _LOGGER.warning("%s: cannot read calendars: %s", self.subentry.title, err)
                status = status_from_failure(str(err), now, self.data)
                if not preview:
                    self._update_issues(status)
                return status
            result: SyncResult = await sync_pair(
                cfg,
                self.state,
                events1,
                events2,
                adapter1,
                adapter2,
                confirm_deletions=confirm_deletions,
                dry_run=dry,
            )
            self.last_operations = result.operations
            if not dry:
                self.state = result.state
                await self._store.async_save(self.state.to_json())
            status = status_from_result(result, now, self.data, dry_run=dry)
            for error in status.errors:
                _LOGGER.warning("%s: %s", self.subentry.title, error)
            if not preview:
                self._update_issues(status)
            return status

    async def async_sync_now(self, **kwargs: Any) -> PairStatus:
        status = await self.async_cycle(**kwargs)
        self.async_set_updated_data(status)
        return status

    # ---------------------------------------------------------------- repairs

    def _issue(self, kind: str) -> str:
        return f"{kind}_{self.subentry.subentry_id}"

    def _update_issues(self, status: PairStatus) -> None:
        title = self.subentry.title
        placeholders = {"pair": title}
        data = {"entry_id": self.config_entry.entry_id, "subentry_id": self.subentry.subentry_id}

        def set_issue(kind: str, active: bool, fixable: bool, severity: ir.IssueSeverity, extra: dict[str, str]) -> None:
            if not active:
                ir.async_delete_issue(self.hass, DOMAIN, self._issue(kind))
                return
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self._issue(kind),
                is_fixable=fixable,
                severity=severity,
                translation_key=kind,
                translation_placeholders={**placeholders, **extra},
                data=data if fixable else None,
            )

        set_issue(
            "conflicts",
            bool(status.conflicts),
            False,
            ir.IssueSeverity.WARNING,
            {"count": str(len(status.conflicts)), "items": short_list(status.conflicts)},
        )
        set_issue(
            "deletions_blocked",
            bool(status.blocked_deletions),
            True,
            ir.IssueSeverity.WARNING,
            {"count": str(status.blocked_deletions)},
        )
        set_issue(
            "sync_failing",
            status.failures >= FAILURES_BEFORE_REPAIR,
            False,
            ir.IssueSeverity.ERROR,
            {"count": str(status.failures), "items": short_list(status.errors)},
        )

    def async_clear_issues(self) -> None:
        for kind in ("conflicts", "deletions_blocked", "sync_failing"):
            ir.async_delete_issue(self.hass, DOMAIN, self._issue(kind))
