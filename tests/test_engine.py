import asyncio
from datetime import timedelta

import pytest

from custom_components.calendar_sync.core.engine import sync_pair
from custom_components.calendar_sync.core.models import Capabilities
from custom_components.calendar_sync.core.rules import Action, Condition, Rule
from custom_components.calendar_sync.core.state import State
from tests.helpers import T0, TZ, FakeCalendar, cfg, cycle, ev, fresh

MIRROR = dict(direction_2to1=False)  # calendar 1 -> calendar 2


def mirror_setup(**kw):
    c1, c2 = FakeCalendar(prefix="a"), FakeCalendar(prefix="b")
    return cfg(**{**MIRROR, **kw}), c1, c2


def only(result, kind):
    return [o for o in result.operations if o.kind == kind]


# -- one-way (mirror) ---------------------------------------------------------


def test_mirror_creates_and_is_idempotent():
    config, c1, c2 = mirror_setup()
    c1.add(ev("A"))
    c1.add(ev("B", day=1))
    r, state = cycle(config, fresh(), c1, c2)
    assert r.created == 2 and len(c2.events) == 2
    r, state = cycle(config, state, c1, c2)
    assert r.operations == [] and not r.errors


def test_mirror_propagates_only_changed_fields():
    config, c1, c2 = mirror_setup(closed_mode="ignore")
    id1 = c1.add(ev("A", description="d"))
    _, state = cycle(config, fresh(), c1, c2)
    id2 = next(iter(c2.events))
    c2.events[id2] = c2.events[id2].replace({"location": "set on 2"})  # ignored side edit
    c1.events[id1] = c1.events[id1].replace({"summary": "A2"})
    r, state = cycle(config, state, c1, c2)
    assert c2.events[id2].summary == "A2"
    assert c2.events[id2].location == "set on 2"  # untouched in ignore mode
    assert only(r, "update")[0].fields == ("summary",)


def test_mirror_revert_restores_target_edits():
    config, c1, c2 = mirror_setup(closed_mode="revert")
    c1.add(ev("A"))
    _, state = cycle(config, fresh(), c1, c2)
    id2 = next(iter(c2.events))
    c2.events[id2] = c2.events[id2].replace({"summary": "hacked", "location": "x"})
    r, state = cycle(config, state, c1, c2)
    assert c2.events[id2].summary == "A" and c2.events[id2].location == ""
    _, state = cycle(config, state, c1, c2)
    assert c2.calls[-1][0] == "update"  # no further writes after revert
    assert len(c2.calls) == 2


def test_mirror_revert_recreates_deleted_target_event():
    config, c1, c2 = mirror_setup(closed_mode="revert")
    c1.add(ev("A"))
    _, state = cycle(config, fresh(), c1, c2)
    c2.events.clear()
    r, state = cycle(config, state, c1, c2)
    assert r.created == 1 and [f.summary for f in c2.events.values()] == ["A"]
    r, _ = cycle(config, state, c1, c2)
    assert r.operations == []


def test_mirror_ignore_does_not_recreate_deleted_target_event():
    config, c1, c2 = mirror_setup(closed_mode="ignore")
    id1 = c1.add(ev("A"))
    _, state = cycle(config, fresh(), c1, c2)
    c2.events.clear()
    r, state = cycle(config, state, c1, c2)
    assert r.operations == [] and c2.events == {}
    (link,) = state.links.values()
    assert link.detached
    del c1.events[id1]  # once the source is gone the link is dropped
    _, state = cycle(config, state, c1, c2)
    assert state.links == {}


def test_source_deletion_propagates_when_enabled_only():
    config, c1, c2 = mirror_setup()
    id1 = c1.add(ev("A"))
    _, state = cycle(config, fresh(), c1, c2)
    del c1.events[id1]
    r, state = cycle(config, state, c1, c2)
    assert r.deleted == 1 and c2.events == {} and state.links == {}

    config, c1, c2 = mirror_setup(propagate_deletes=False, closed_mode="ignore")
    id1 = c1.add(ev("A"))
    _, state = cycle(config, fresh(), c1, c2)
    del c1.events[id1]
    r, _ = cycle(config, state, c1, c2)
    assert r.deleted == 0 and len(c2.events) == 1


def test_events_rolled_out_of_window_are_not_deleted():
    config, c1, c2 = mirror_setup()
    c1.add(ev("old", day=-3))
    _, state = cycle(config, fresh(), c1, c2)
    far = T0 + timedelta(days=60)
    config.window = type(config.window)(far, far + timedelta(days=30))
    c1.events.clear()
    r, state = cycle(config, state, c1, c2)
    assert r.deleted == 0 and len(c2.events) == 1


def test_skip_rule_keeps_event_out():
    rules = [Rule(Condition("text", "summary", "contains", "private"), Action("skip"))]
    config, c1, c2 = mirror_setup(rules_1to2=rules)
    c1.add(ev("private dentist"))
    c1.add(ev("public"))
    cycle(config, fresh(), c1, c2)
    assert [f.summary for f in c2.events.values()] == ["public"]


def test_rule_transform_applies_on_create_and_update():
    rules = [Rule(Condition(), Action("set_summary", "[1] {summary}"))]
    config, c1, c2 = mirror_setup(rules_1to2=rules)
    id1 = c1.add(ev("A"))
    _, state = cycle(config, fresh(), c1, c2)
    id2 = next(iter(c2.events))
    assert c2.events[id2].summary == "[1] A"
    c1.events[id1] = c1.events[id1].replace({"summary": "B"})
    cycle(config, state, c1, c2)
    assert c2.events[id2].summary == "[1] B"


def test_adopts_identical_existing_event_instead_of_duplicating():
    config, c1, c2 = mirror_setup()
    c1.add(ev("A"))
    c2.add(ev("A"))
    r, _ = cycle(config, fresh(), c1, c2)
    assert r.adopted == 1 and r.created == 0 and len(c2.events) == 1


def test_foreign_events_in_target_are_never_touched():
    config, c1, c2 = mirror_setup(closed_mode="revert")
    foreign = c2.add(ev("mine, not synced", day=3))
    c1.add(ev("A"))
    _, state = cycle(config, fresh(), c1, c2)
    cycle(config, state, c1, c2)
    assert c2.events[foreign].summary == "mine, not synced"
    assert all(call[0] == "create" for call in c2.calls)


# -- two-way ------------------------------------------------------------------


def two_way(**kw):
    return cfg(**kw), FakeCalendar(prefix="a"), FakeCalendar(prefix="b")


def test_two_way_creates_both_ways_without_ping_pong():
    config, c1, c2 = two_way()
    c1.add(ev("from 1"))
    c2.add(ev("from 2", day=1))
    r, state = cycle(config, fresh(), c1, c2)
    assert r.created == 2
    assert sorted(f.summary for f in c1.events.values()) == ["from 1", "from 2"]
    assert sorted(f.summary for f in c2.events.values()) == ["from 1", "from 2"]
    r, _ = cycle(config, state, c1, c2)
    assert r.operations == []


def test_two_way_merges_changes_to_different_fields():
    config, c1, c2 = two_way()
    id1 = c1.add(ev("A"))
    _, state = cycle(config, fresh(), c1, c2)
    id2 = next(iter(c2.events))
    c1.events[id1] = c1.events[id1].replace({"summary": "A-new"})
    c2.events[id2] = c2.events[id2].replace({"location": "room 5"})
    r, state = cycle(config, state, c1, c2)
    for cal, event_id in ((c1, id1), (c2, id2)):
        assert (cal.events[event_id].summary, cal.events[event_id].location) == ("A-new", "room 5")
    r, _ = cycle(config, state, c1, c2)
    assert r.operations == []


@pytest.mark.parametrize("policy,expected", [("calendar1", "one"), ("calendar2", "two")])
def test_two_way_conflict_policies(policy, expected):
    config, c1, c2 = two_way(conflict_policy=policy)
    id1 = c1.add(ev("A"))
    _, state = cycle(config, fresh(), c1, c2)
    id2 = next(iter(c2.events))
    c1.events[id1] = c1.events[id1].replace({"summary": "one"})
    c2.events[id2] = c2.events[id2].replace({"summary": "two"})
    r, state = cycle(config, state, c1, c2)
    assert c1.events[id1].summary == c2.events[id2].summary == expected
    assert r.conflicts == []


def test_two_way_ask_leaves_conflict_open_until_resolved():
    config, c1, c2 = two_way(conflict_policy="ask")
    id1 = c1.add(ev("A"))
    _, state = cycle(config, fresh(), c1, c2)
    id2 = next(iter(c2.events))
    c1.events[id1] = c1.events[id1].replace({"summary": "one"})
    c2.events[id2] = c2.events[id2].replace({"summary": "two"})
    r, state = cycle(config, state, c1, c2)
    assert [c.field for c in r.conflicts] == ["summary"]
    assert (c1.events[id1].summary, c2.events[id2].summary) == ("one", "two")
    r, state = cycle(config, state, c1, c2)  # still reported
    assert [c.field for c in r.conflicts] == ["summary"]
    c2.events[id2] = c2.events[id2].replace({"summary": "one"})  # user resolves
    r, _ = cycle(config, state, c1, c2)
    assert r.conflicts == [] and r.operations == []


def test_two_way_time_change_moves_start_and_end_together():
    config, c1, c2 = two_way()
    id1 = c1.add(ev("A", hour=9))
    _, state = cycle(config, fresh(), c1, c2)
    c1.events[id1] = ev("A", hour=14, minutes=30)
    _, _ = cycle(config, state, c1, c2)
    moved = next(iter(c2.events.values()))
    assert moved.start.hour == 14 and moved.end - moved.start == timedelta(minutes=30)


def test_two_way_deletion_propagates_either_direction():
    config, c1, c2 = two_way()
    c1.add(ev("A"))
    _, state = cycle(config, fresh(), c1, c2)
    c2.events.clear()
    r, state = cycle(config, state, c1, c2)
    assert r.deleted == 1 and c1.events == {} and state.links == {}


def test_two_way_without_delete_propagation_detaches():
    config, c1, c2 = two_way(propagate_deletes=False)
    c1.add(ev("A"))
    _, state = cycle(config, fresh(), c1, c2)
    c2.events.clear()
    r, state = cycle(config, state, c1, c2)
    assert r.deleted == 0 and r.created == 0 and len(c1.events) == 1
    assert next(iter(state.links.values())).detached


# -- capabilities, guard, errors, dry run, state -------------------------------


def test_adapter_without_update_replaces_event_and_relinks():
    c1 = FakeCalendar(prefix="a")
    c2 = FakeCalendar(Capabilities(update=False), prefix="b")
    config = cfg(**MIRROR)
    id1 = c1.add(ev("A"))
    _, state = cycle(config, fresh(), c1, c2)
    old_id = next(iter(c2.events))
    c1.events[id1] = c1.events[id1].replace({"summary": "B"})
    r, state = cycle(config, state, c1, c2)
    assert only(r, "replace") and old_id not in c2.events
    assert [f.summary for f in c2.events.values()] == ["B"]
    r, _ = cycle(config, state, c1, c2)
    assert r.operations == []


def test_read_only_target_cannot_be_written():
    c1 = FakeCalendar(prefix="a")
    c2 = FakeCalendar(Capabilities(create=False, update=False, delete=False), prefix="b")
    config = cfg()  # both directions requested, but 1 -> 2 is impossible
    c1.add(ev("A"))
    c2.add(ev("B", day=1))
    r, _ = cycle(config, fresh(), c1, c2)
    assert c2.calls == [] and [f.summary for f in c1.events.values()] == ["A", "B"]


def test_mass_deletion_is_blocked_unless_confirmed():
    config, c1, c2 = mirror_setup()
    ids = [c1.add(ev(f"E{i}", day=i)) for i in range(8)]
    _, state = cycle(config, fresh(), c1, c2)
    for event_id in ids:
        del c1.events[event_id]
    r, state2 = cycle(config, state, c1, c2)
    assert r.deleted == 0 and r.blocked_deletions == 8 and len(c2.events) == 8
    assert len(state2.links) == 8
    r, _ = cycle(config, state, c1, c2, confirm_deletions=True)
    assert r.deleted == 8 and c2.events == {}


def test_adapter_error_keeps_link_and_retries():
    config, c1, c2 = mirror_setup()
    id1 = c1.add(ev("A"))
    _, state = cycle(config, fresh(), c1, c2)
    c1.events[id1] = c1.events[id1].replace({"summary": "B"})
    c2.fail.add("update")
    r, state = cycle(config, state, c1, c2)
    assert r.errors and r.updated == 0
    c2.fail.clear()
    r, _ = cycle(config, state, c1, c2)
    assert r.updated == 1 and next(iter(c2.events.values())).summary == "B"


def test_create_error_is_reported_and_retried_next_cycle():
    config, c1, c2 = mirror_setup()
    c1.add(ev("A"))
    c2.fail.add("create")
    r, state = cycle(config, fresh(), c1, c2)
    assert r.errors and state.links == {}
    c2.fail.clear()
    r, _ = cycle(config, state, c1, c2)
    assert r.created == 1


def test_dry_run_writes_nothing_and_leaves_state_empty_of_effects():
    config, c1, c2 = mirror_setup()
    c1.add(ev("A"))
    r = asyncio.run(
        sync_pair(config, fresh(), dict(c1.events), dict(c2.events), c1, c2, dry_run=True)
    )
    assert r.created == 1 and c2.events == {} and c2.calls == []


def test_state_json_roundtrip_keeps_sync_stable():
    config, c1, c2 = two_way()
    c1.add(ev("A", description="d", location="l"))
    c1.add(ev("All day"))
    _, state = cycle(config, fresh(), c1, c2)
    restored = State.from_json(state.to_json())
    assert restored.to_json() == state.to_json()
    r, _ = cycle(config, restored, c1, c2)
    assert r.operations == []


def test_config_requires_a_direction():
    with pytest.raises(ValueError):
        cfg(direction_1to2=False, direction_2to1=False)


def test_two_way_replace_does_not_resurrect_old_event_from_stale_snapshot():
    c1 = FakeCalendar(Capabilities(update=False), prefix="a")
    c2 = FakeCalendar(prefix="b")
    config = cfg()
    id2 = c2.add(ev("A"))
    _, state = cycle(config, fresh(), c1, c2)
    c2.events[id2] = c2.events[id2].replace({"summary": "B"})
    r, state = cycle(config, state, c1, c2)
    assert only(r, "replace") and [f.summary for f in c1.events.values()] == ["B"]
    assert len(c2.events) == 1
    r, _ = cycle(config, state, c1, c2)
    assert r.operations == []
