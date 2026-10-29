from datetime import datetime, timedelta, timezone

import pytest

from custom_components.calendar_sync.core.models import EventFields
from custom_components.calendar_sync.core.rules import (
    Action,
    Condition,
    Rule,
    apply_rules,
    rule_from_dict,
    rule_to_dict,
    validate_rule,
)
from tests.helpers import TZ, ev


def rule(cond: Condition, act: Action) -> Rule:
    return Rule(cond, act)


def text(op, value, field="summary") -> Condition:
    return Condition("text", field, op, value)


def run(rules, fields):
    return apply_rules(rules, fields, TZ)


def test_empty_rules_copy_as_is():
    e = ev()
    assert run([], e) == e


@pytest.mark.parametrize(
    "op,value,expected",
    [
        ("contains", "stand", True),
        ("contains", "xyz", False),
        ("equals", "DAILY STANDUP", True),
        ("starts_with", "daily", True),
        ("ends_with", "up", True),
        ("regex", r"^daily\s+\w+$", True),
        ("regex", r"[unclosed", False),
    ],
)
def test_text_conditions(op, value, expected):
    skip = rule(text(op, value), Action("skip"))
    assert (run([skip], ev("Daily standup")) is None) is expected


def test_skip_stops_processing():
    rules = [rule(text("contains", "private"), Action("skip")), rule(Condition(), Action("set_summary", "X"))]
    assert run(rules, ev("My private thing")) is None
    assert run(rules, ev("Public")).summary == "X"


def test_templates_compose_in_order():
    rules = [
        rule(Condition(), Action("set_summary", "[A] {summary}")),
        rule(Condition(), Action("set_summary", "{summary} ({location})")),
    ]
    out = run(rules, ev("Lunch", location="Cafe"))
    assert out.summary == "[A] Lunch (Cafe)"


def test_template_ignores_unknown_placeholders_and_attributes():
    out = run([rule(Condition(), Action("set_summary", "{summary.__class__}{nope}"))], ev("A"))
    assert out.summary == "{summary.__class__}{nope}"


def test_clear_description_and_location():
    rules = [
        rule(Condition(), Action("clear_description")),
        rule(Condition(), Action("clear_location")),
    ]
    out = run(rules, ev(description="secret", location="home"))
    assert (out.description, out.location) == ("", "")


def test_shift_timed_and_noop_for_all_day():
    shifted = run([rule(Condition(), Action("shift", -30))], ev(hour=9))
    assert shifted.start.hour == 8 and shifted.start.minute == 30
    all_day = EventFields("D", datetime(2026, 11, 2).date(), datetime(2026, 11, 3).date())
    assert run([rule(Condition(), Action("shift", 60))], all_day) == all_day


def test_all_day_condition():
    all_day = EventFields("D", datetime(2026, 11, 2).date(), datetime(2026, 11, 3).date())
    r = [rule(Condition("all_day", value=True), Action("skip"))]
    assert run(r, all_day) is None
    assert run(r, ev()) is not None


def test_duration_condition():
    longer = [rule(Condition("duration", op="gt", value=90), Action("skip"))]
    assert run(longer, ev(minutes=120)) is None
    assert run(longer, ev(minutes=60)) is not None


def test_weekday_and_hour_conditions():
    monday_only = [rule(Condition("weekday", value=[0]), Action("skip"))]
    assert run(monday_only, ev(day=0)) is None  # 2026-11-02 is Monday
    assert run(monday_only, ev(day=1)) is not None
    night = [rule(Condition("hour", value=[22, 6]), Action("skip"))]
    assert run(night, ev(hour=23)) is None
    assert run(night, ev(hour=3)) is None
    assert run(night, ev(hour=12)) is not None


def test_hour_uses_configured_timezone():
    kyiv = timezone(timedelta(hours=2))
    start = datetime(2026, 11, 2, 7, 0, tzinfo=timezone.utc)  # 09:00 in +02:00
    e = EventFields("A", start, start + timedelta(hours=1))
    r = [rule(Condition("hour", value=[9, 10]), Action("skip"))]
    assert apply_rules(r, e, kyiv) is None
    assert apply_rules(r, e, timezone.utc) is not None


def test_validate_rejects_bad_rules():
    with pytest.raises(ValueError):
        validate_rule(rule(Condition("nope"), Action("skip")))
    with pytest.raises(ValueError):
        validate_rule(rule(text("regex", "[bad"), Action("skip")))
    with pytest.raises(ValueError):
        validate_rule(rule(Condition(), Action("explode")))
    validate_rule(rule(text("contains", "x"), Action("shift", 15)))


def test_rule_json_roundtrip():
    r = rule(text("contains", "x", "description"), Action("set_summary", "[{summary}]"))
    assert rule_from_dict(rule_to_dict(r)) == r
