"""Adapter logic and engine + adapter together, on fake calendar entities."""

import asyncio
from types import SimpleNamespace

import pytest

from custom_components.calendar_sync.adapter import (
    ID_SEP,
    AdapterError,
    NativeCalendarAdapter,
    make_event_id,
)
from custom_components.calendar_sync.core.engine import sync_pair
from custom_components.calendar_sync.core.models import Capabilities, to_datetime
from custom_components.calendar_sync.core.state import State
from tests.helpers import TZ, WINDOW, cfg, ev

FULL = Capabilities(create=True, update=True, delete=True)
GOOGLE_LIKE = Capabilities(create=True, update=False, delete=True)
READ_ONLY = Capabilities(create=False, update=False, delete=False)


class FakeAccess:
    """Behaves like a calendar entity: created events get a uid, `lag` extra reads
    pass before a new event becomes visible (like a cache that refreshes later)."""

    def __init__(self, caps=FULL, prefix="u", lag=0):
        self._caps = caps
        self.events: list[SimpleNamespace] = []
        self.calls: list[tuple] = []
        self._n = 0
        self._prefix = prefix
        self._lag = lag
        self._pending: list[tuple[int, SimpleNamespace]] = []
        self.reads = 0

    def seed(self, fields, uid=True, recurrence_id=None, description=None, location=None):
        self._n += 1
        event = SimpleNamespace(
            uid=f"{self._prefix}{self._n}" if uid else None,
            recurrence_id=recurrence_id,
            summary=fields.summary,
            start=fields.start,
            end=fields.end,
            description=description if description is not None else (fields.description or None),
            location=location if location is not None else (fields.location or None),
        )
        self.events.append(event)
        return event

    def capabilities(self):
        return self._caps

    async def get_events(self, start, end):
        self.reads += 1
        ready = [e for due, e in self._pending if self.reads > due]
        self._pending = [(d, e) for d, e in self._pending if self.reads <= d]
        self.events.extend(ready)
        return [
            e
            for e in self.events
            if to_datetime(e.start, TZ) < end and to_datetime(e.end, TZ) >= start
        ]

    async def create(self, fields):
        self.calls.append(("create", fields.summary))
        self._n += 1
        event = SimpleNamespace(
            uid=f"{self._prefix}{self._n}",
            recurrence_id=None,
            summary=fields.summary,
            start=fields.start,
            end=fields.end,
            description=fields.description or None,
            location=fields.location or None,
        )
        if self._lag:
            self._pending.append((self.reads + self._lag, event))
        else:
            self.events.append(event)

    async def update(self, uid, recurrence_id, fields):
        self.calls.append(("update", uid, recurrence_id))
        for e in self.events:
            if e.uid == uid and e.recurrence_id == recurrence_id:
                e.summary, e.start, e.end = fields.summary, fields.start, fields.end
                e.description, e.location = fields.description or None, fields.location or None

    async def delete(self, uid, recurrence_id):
        self.calls.append(("delete", uid, recurrence_id))
        self.events = [
            e for e in self.events if not (e.uid == uid and e.recurrence_id == recurrence_id)
        ]


async def no_sleep(_):
    return None


def adapter(access, **kw):
    return NativeCalendarAdapter(access, TZ, sleep=no_sleep, **kw)


def run(coro):
    return asyncio.run(coro)


# -- reading and ids ---------------------------------------------------------


def test_read_converts_events_and_none_to_empty_strings():
    access = FakeAccess()
    access.seed(ev("A"))
    result = run(adapter(access).read(WINDOW))
    (fields,) = result.values()
    assert (fields.summary, fields.description, fields.location) == ("A", "", "")


def test_recurring_instances_get_distinct_ids():
    access = FakeAccess()
    access.seed(ev("Weekly"), recurrence_id="20261102")
    access.seed(ev("Weekly", day=7), recurrence_id="20261109")
    first, second = access.events
    first.uid = second.uid = "same-uid"
    result = run(adapter(access).read(WINDOW))
    assert len(result) == 2
    assert all(ID_SEP in event_id for event_id in result)


def test_event_without_uid_gets_hash_id_and_cannot_be_written():
    access = FakeAccess()
    access.seed(ev("Holiday"), uid=False)
    ad = adapter(access)
    (event_id,) = run(ad.read(WINDOW))
    assert event_id.startswith("h:")
    with pytest.raises(AdapterError):
        run(ad.delete(event_id))
    assert access.calls == []


def test_write_before_read_is_rejected():
    with pytest.raises(AdapterError):
        run(adapter(FakeAccess()).update("nope", ev("A")))


# -- create: finding the new event -------------------------------------------


def test_create_returns_new_id_and_stored_fields():
    access = FakeAccess()
    ad = adapter(access)
    run(ad.read(WINDOW))
    event_id, stored = run(ad.create(ev("New", description="d")))
    assert event_id == access.events[0].uid and stored.description == "d"


def test_create_ignores_identical_event_that_existed_before():
    access = FakeAccess()
    existing = access.seed(ev("Same"))
    ad = adapter(access)
    run(ad.read(WINDOW))
    event_id, _ = run(ad.create(ev("Same")))
    assert event_id != existing.uid


def test_create_waits_for_event_to_appear():
    access = FakeAccess(lag=2)
    ad = adapter(access)
    run(ad.read(WINDOW))
    event_id, _ = run(ad.create(ev("Slow")))
    assert event_id.startswith("u")


def test_create_reports_event_that_never_appears():
    access = FakeAccess(lag=99)
    ad = adapter(access, find_attempts=2)
    run(ad.read(WINDOW))
    with pytest.raises(AdapterError):
        run(ad.create(ev("Ghost")))


def test_update_and_delete_use_uid_and_recurrence_id():
    access = FakeAccess()
    seeded = access.seed(ev("A"), recurrence_id="r1")
    ad = adapter(access)
    (event_id,) = run(ad.read(WINDOW))
    run(ad.update(event_id, ev("B")))
    assert access.calls[-1] == ("update", seeded.uid, "r1") and seeded.summary == "B"
    run(ad.delete(event_id))
    assert access.calls[-1] == ("delete", seeded.uid, "r1") and access.events == []


# -- engine + adapters --------------------------------------------------------


def sync(config, state, access1, access2, **kw):
    ad1, ad2 = adapter(access1), adapter(access2)
    events1 = run(ad1.read(config.window))
    events2 = run(ad2.read(config.window))
    result = run(sync_pair(config, state, events1, events2, ad1, ad2, **kw))
    return result, result.state


def names(access):
    return sorted(e.summary for e in access.events)


def test_mirror_into_local_like_calendar_is_stable():
    source, target = FakeAccess(prefix="s"), FakeAccess(prefix="t")
    source.seed(ev("A"))
    source.seed(ev("B", day=1))
    config = cfg(direction_2to1=False)
    r, state = sync(config, State(), source, target)
    assert r.created == 2 and names(target) == ["A", "B"] and not r.errors
    source.events[0].summary = "A2"
    r, state = sync(config, state, source, target)
    assert [o.kind for o in r.operations] == ["update"] and names(target) == ["A2", "B"]
    r, _ = sync(config, state, source, target)
    assert r.operations == []


def test_mirror_into_google_like_calendar_replaces_and_relinks():
    source, target = FakeAccess(prefix="s"), FakeAccess(GOOGLE_LIKE, prefix="g")
    source.seed(ev("A"))
    config = cfg(direction_2to1=False)
    _, state = sync(config, State(), source, target)
    old_uid = target.events[0].uid
    source.events[0].summary = "A2"
    r, state = sync(config, state, source, target)
    assert [o.kind for o in r.operations] == ["replace"]
    assert names(target) == ["A2"] and target.events[0].uid != old_uid
    r, _ = sync(config, state, source, target)
    assert r.operations == []


def test_read_only_source_feeds_a_writable_calendar():
    holidays, mine = FakeAccess(READ_ONLY, prefix="h"), FakeAccess(prefix="m")
    holidays.seed(ev("Holiday"), uid=False)
    config = cfg()  # both directions requested, 2->1 impossible into read-only
    r, state = sync(config, State(), holidays, mine)
    assert names(mine) == ["Holiday"] and holidays.calls == []
    r, _ = sync(config, state, holidays, mine)
    assert r.operations == []


def test_two_way_between_two_local_like_calendars():
    one, two = FakeAccess(prefix="a"), FakeAccess(prefix="b")
    one.seed(ev("From one"))
    two.seed(ev("From two", day=1))
    config = cfg()
    r, state = sync(config, State(), one, two)
    assert names(one) == names(two) == ["From one", "From two"]
    two.events[0].location = "room 4"
    r, state = sync(config, state, one, two)
    assert {e.location for e in one.events if e.summary == two.events[0].summary} == {"room 4"}
    r, _ = sync(config, state, one, two)
    assert r.operations == []


def test_unreadable_new_event_is_adopted_next_cycle_without_duplicate():
    source, target = FakeAccess(prefix="s"), FakeAccess(prefix="t", lag=99)
    source.seed(ev("A"))
    config = cfg(direction_2to1=False)
    ad1, ad2 = adapter(source), NativeCalendarAdapter(target, TZ, sleep=no_sleep, find_attempts=1)
    result = run(sync_pair(config, State(), run(ad1.read(config.window)), run(ad2.read(config.window)), ad1, ad2))
    assert result.errors and result.state.links == {}
    # the entity caught up: the event created earlier is now visible
    target.events.extend(event for _, event in target._pending)
    target._pending.clear()
    r, state = sync(config, result.state, source, target)
    assert r.adopted == 1 and r.created == 0 and names(target) == ["A"]
