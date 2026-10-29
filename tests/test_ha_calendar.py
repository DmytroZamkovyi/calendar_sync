"""Test the Home Assistant wrapper against stubs of the few HA names it uses.

Stub values mirror Home Assistant core (calendar/const.py): CREATE_EVENT = 1,
DELETE_EVENT = 2, UPDATE_EVENT = 4, DATA_COMPONENT keyed by the domain.
"""

import asyncio
import importlib
import sys
import types
from datetime import datetime, timedelta, timezone
from enum import IntFlag
from types import SimpleNamespace

import pytest

from custom_components.calendar_sync.adapter import AdapterError
from tests.helpers import T0, ev

LOCAL = timezone(timedelta(hours=2))


class Feature(IntFlag):
    CREATE_EVENT = 1
    DELETE_EVENT = 2
    UPDATE_EVENT = 4


class HAError(Exception):
    pass


class FakeEntity:
    def __init__(self, features, available=True):
        self.supported_features = features
        self.available = available
        self.calls = []
        self.fail = None
        self.events = []

    def _maybe_fail(self):
        if self.fail:
            raise self.fail

    async def async_get_events(self, hass, start, end):
        self.calls.append(("get", start, end))
        self._maybe_fail()
        return self.events

    async def async_create_event(self, **kwargs):
        self.calls.append(("create", kwargs))
        self._maybe_fail()

    async def async_update_event(self, uid, event, recurrence_id=None, recurrence_range=None):
        self.calls.append(("update", uid, event, recurrence_id))
        self._maybe_fail()

    async def async_delete_event(self, uid, recurrence_id=None, recurrence_range=None):
        self.calls.append(("delete", uid, recurrence_id))
        self._maybe_fail()


class FakeComponent:
    def __init__(self, entities):
        self._entities = entities

    def get_entity(self, entity_id):
        return self._entities.get(entity_id)


@pytest.fixture
def ha(monkeypatch):
    def module(name, **attrs):
        mod = types.ModuleType(name)
        mod.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, mod)
        return mod

    module("homeassistant")
    module("homeassistant.components")
    module("homeassistant.components.calendar", CalendarEntityFeature=Feature)
    module("homeassistant.components.calendar.const", DATA_COMPONENT="calendar")
    module("homeassistant.core", HomeAssistant=object)
    module("homeassistant.exceptions", HomeAssistantError=HAError)
    util = module("homeassistant.util")
    dt_mod = module(
        "homeassistant.util.dt",
        as_local=lambda value: value.astimezone(LOCAL),
        get_default_time_zone=lambda: LOCAL,
    )
    util.dt = dt_mod
    monkeypatch.delitem(sys.modules, "custom_components.calendar_sync.ha_calendar", raising=False)
    return importlib.import_module("custom_components.calendar_sync.ha_calendar")


def make(ha, features=7, available=True):
    entity = FakeEntity(Feature(features), available)
    hass = SimpleNamespace(data={"calendar": FakeComponent({"calendar.test": entity})})
    return ha.EntityAccess(hass, "calendar.test"), entity


def run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize(
    "features,expected",
    [
        (7, (True, True, True)),  # local calendar
        (3, (True, False, True)),  # google: create + delete
        (0, (False, False, False)),  # read-only
    ],
)
def test_capabilities_follow_supported_features(ha, features, expected):
    access, _ = make(ha, features)
    caps = access.capabilities()
    assert (caps.create, caps.update, caps.delete) == expected


def test_create_passes_rfc5545_field_names_in_local_time(ha):
    access, entity = make(ha)
    run(access.create(ev("A", description="d", location="l")))
    (_, kwargs) = entity.calls[0]
    assert set(kwargs) == {"dtstart", "dtend", "summary", "description", "location"}
    assert kwargs["dtstart"] == T0 and kwargs["dtstart"].utcoffset() == timedelta(hours=2)


def test_all_day_dates_are_passed_unchanged(ha):
    from custom_components.calendar_sync.core.models import EventFields

    access, entity = make(ha)
    day = datetime(2026, 11, 3).date()
    run(access.create(EventFields("D", day, day + timedelta(days=1))))
    assert entity.calls[0][1]["dtstart"] == day


def test_update_and_delete_call_entity_methods(ha):
    access, entity = make(ha)
    run(access.update("uid1", "rid1", ev("B")))
    run(access.delete("uid1", None))
    assert entity.calls[0][:2] == ("update", "uid1") and entity.calls[0][3] == "rid1"
    assert entity.calls[0][2]["summary"] == "B"
    assert entity.calls[1] == ("delete", "uid1", None)


def test_get_events_uses_local_window(ha):
    access, entity = make(ha)
    entity.events = [SimpleNamespace(uid="u")]
    result = run(access.get_events(T0, T0 + timedelta(days=1)))
    assert result == entity.events and entity.calls[0][1].utcoffset() == timedelta(hours=2)


def test_missing_or_unavailable_entity_is_an_adapter_error(ha):
    hass = SimpleNamespace(data={"calendar": FakeComponent({})})
    with pytest.raises(AdapterError):
        ha.EntityAccess(hass, "calendar.nope").capabilities()
    hass = SimpleNamespace(data={})
    with pytest.raises(AdapterError):
        run(ha.EntityAccess(hass, "calendar.nope").get_events(T0, T0))
    access, _ = make(ha, available=False)
    with pytest.raises(AdapterError):
        access.capabilities()


@pytest.mark.parametrize("error", [HAError("x"), ValueError("x"), NotImplementedError()])
def test_core_errors_become_adapter_errors(ha, error):
    access, entity = make(ha)
    entity.fail = error
    for call in (
        access.get_events(T0, T0),
        access.create(ev("A")),
        access.update("u", None, ev("A")),
        access.delete("u", None),
    ):
        with pytest.raises(AdapterError):
            run(call)


def test_native_adapter_factory_uses_default_time_zone(ha):
    hass = SimpleNamespace(data={"calendar": FakeComponent({"calendar.test": FakeEntity(Feature(7))})})
    adapter = ha.native_adapter(hass, "calendar.test")
    assert adapter.capabilities.update
