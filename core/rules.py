"""Fixed rule set: one condition + one action per rule. No Home Assistant imports."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from typing import Any

from .models import EventFields

TEXT_FIELDS = ("summary", "description", "location")
TEXT_OPS = ("contains", "equals", "starts_with", "ends_with", "regex")
NUMBER_OPS = ("gt", "lt")
CONDITION_KINDS = ("always", "text", "all_day", "duration", "weekday", "hour")
ACTION_KINDS = (
    "skip",
    "set_summary",
    "set_description",
    "set_location",
    "clear_description",
    "clear_location",
    "shift",
)
TEMPLATE_FIELDS = ("summary", "description", "location", "start", "end")
_TEMPLATE_RE = re.compile(r"\{(" + "|".join(TEMPLATE_FIELDS) + r")\}")


@dataclass(frozen=True, slots=True)
class Condition:
    """kind=always | text(field, op, value) | all_day(value: bool) |
    duration(op gt/lt, value: minutes) | weekday(value: list of 0..6, Monday=0) |
    hour(value: [from_hour, to_hour), wraps over midnight when from > to)."""

    kind: str = "always"
    field: str = ""
    op: str = ""
    value: Any = None


@dataclass(frozen=True, slots=True)
class Action:
    """kind=skip | set_summary/set_description/set_location(value: template) |
    clear_description | clear_location | shift(value: minutes, timed events only)."""

    kind: str
    value: Any = None


@dataclass(frozen=True, slots=True)
class Rule:
    condition: Condition
    action: Action


def validate_rule(rule: Rule) -> None:
    cond, act = rule.condition, rule.action
    if cond.kind not in CONDITION_KINDS:
        raise ValueError(f"unknown condition: {cond.kind}")
    if act.kind not in ACTION_KINDS:
        raise ValueError(f"unknown action: {act.kind}")
    if cond.kind == "text":
        if cond.field not in TEXT_FIELDS or cond.op not in TEXT_OPS:
            raise ValueError("invalid text condition")
        if cond.op == "regex":
            try:
                re.compile(str(cond.value))
            except re.error as err:
                raise ValueError(f"invalid regular expression: {err}") from err
    if cond.kind == "duration" and cond.op not in NUMBER_OPS:
        raise ValueError("invalid duration condition")
    if cond.kind == "hour" and not (isinstance(cond.value, (list, tuple)) and len(cond.value) == 2):
        raise ValueError("hour condition needs [from, to]")
    if act.kind == "shift":
        int(act.value)


def _local(value: datetime, tz: tzinfo) -> datetime:
    return value.astimezone(tz) if value.tzinfo else value


def duration_minutes(fields: EventFields) -> float:
    if fields.all_day:
        return (fields.end - fields.start).days * 1440
    return (fields.end - fields.start).total_seconds() / 60


def matches(cond: Condition, fields: EventFields, tz: tzinfo) -> bool:
    kind = cond.kind
    if kind == "always":
        return True
    if kind == "text":
        text = str(getattr(fields, cond.field) or "")
        needle = str(cond.value or "")
        if cond.op == "regex":
            try:
                return re.search(needle, text, re.IGNORECASE) is not None
            except re.error:
                return False
        text, needle = text.casefold(), needle.casefold()
        return {
            "contains": needle in text,
            "equals": text == needle,
            "starts_with": text.startswith(needle),
            "ends_with": text.endswith(needle),
        }[cond.op]
    if kind == "all_day":
        return fields.all_day == bool(cond.value)
    if kind == "duration":
        minutes = duration_minutes(fields)
        return minutes > float(cond.value) if cond.op == "gt" else minutes < float(cond.value)
    if kind == "weekday":
        start = fields.start if fields.all_day else _local(fields.start, tz)
        return start.weekday() in set(cond.value or [])
    if kind == "hour":
        if fields.all_day:
            return False
        hour = _local(fields.start, tz).hour
        lo, hi = int(cond.value[0]), int(cond.value[1])
        return lo <= hour < hi if lo <= hi else (hour >= lo or hour < hi)
    return False


def render(template: str, fields: EventFields) -> str:
    """Substitute {summary}, {description}, {location}, {start}, {end} only."""

    def sub(match: re.Match[str]) -> str:
        value = getattr(fields, match.group(1))
        return value if isinstance(value, str) else value.isoformat()

    return _TEMPLATE_RE.sub(sub, template or "")


def _apply(action: Action, fields: EventFields) -> EventFields:
    kind = action.kind
    if kind == "set_summary":
        return fields.replace({"summary": render(action.value, fields)})
    if kind == "set_description":
        return fields.replace({"description": render(action.value, fields)})
    if kind == "set_location":
        return fields.replace({"location": render(action.value, fields)})
    if kind == "clear_description":
        return fields.replace({"description": ""})
    if kind == "clear_location":
        return fields.replace({"location": ""})
    if kind == "shift" and not fields.all_day:
        delta = timedelta(minutes=int(action.value))
        return fields.replace({"when": (fields.start + delta, fields.end + delta)})
    return fields


def apply_rules(rules: list[Rule], fields: EventFields, tz: tzinfo) -> EventFields | None:
    """Run rules in order on the current (already transformed) event.

    Returns None when a matching `skip` rule says the event must not be copied.
    """
    current = fields
    for rule in rules:
        if not matches(rule.condition, current, tz):
            continue
        if rule.action.kind == "skip":
            return None
        current = _apply(rule.action, current)
    return current


def rule_to_dict(rule: Rule) -> dict[str, Any]:
    c, a = rule.condition, rule.action
    return {
        "condition": {"kind": c.kind, "field": c.field, "op": c.op, "value": c.value},
        "action": {"kind": a.kind, "value": a.value},
    }


def rule_from_dict(data: dict[str, Any]) -> Rule:
    c, a = data["condition"], data["action"]
    return Rule(
        Condition(c.get("kind", "always"), c.get("field", ""), c.get("op", ""), c.get("value")),
        Action(a["kind"], a.get("value")),
    )
