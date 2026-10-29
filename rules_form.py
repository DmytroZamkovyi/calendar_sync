"""Convert between rule editor form input and `Rule` objects.

No Home Assistant imports. Builders return `(object, errors)`: `errors` maps a form
field to a translation key of the config flow's `error` section.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .core.rules import Action, Condition, Rule

NO_DETAILS_CONDITIONS = ("always",)
NO_DETAILS_ACTIONS = ("skip", "clear_description", "clear_location")
TEMPLATE_ACTIONS = ("set_summary", "set_description", "set_location")


def _int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def build_condition(kind: str, data: Mapping[str, Any]) -> tuple[Condition | None, dict[str, str]]:
    errors: dict[str, str] = {}
    if kind == "always":
        return Condition("always"), errors
    if kind == "text":
        value = str(data.get("value", "")).strip()
        if not value:
            errors["value"] = "value_required"
        elif data.get("op") == "regex":
            try:
                re.compile(value)
            except re.error:
                errors["value"] = "bad_regex"
        if errors:
            return None, errors
        return Condition("text", data["field"], data["op"], value), errors
    if kind == "all_day":
        return Condition("all_day", value=bool(data.get("value", True))), errors
    if kind == "duration":
        minutes = _int(data.get("value"))
        if minutes is None or minutes <= 0:
            return None, {"value": "positive_number"}
        return Condition("duration", op=data["op"], value=minutes), errors
    if kind == "weekday":
        days = sorted({d for d in (_int(x) for x in data.get("value") or []) if d is not None})
        if not days:
            return None, {"value": "select_days"}
        return Condition("weekday", value=days), errors
    if kind == "hour":
        lo, hi = _int(data.get("from_hour")), _int(data.get("to_hour"))
        if lo is None or hi is None or not (0 <= lo <= 24 and 0 <= hi <= 24) or lo == hi:
            return None, {"to_hour": "bad_hours"}
        return Condition("hour", value=[lo, hi]), errors
    return None, {"base": "unknown_condition"}


def build_action(kind: str, data: Mapping[str, Any]) -> tuple[Action | None, dict[str, str]]:
    if kind in NO_DETAILS_ACTIONS:
        return Action(kind), {}
    if kind in TEMPLATE_ACTIONS:
        template = str(data.get("template", ""))
        if not template.strip():
            return None, {"template": "value_required"}
        return Action(kind, template), {}
    if kind == "shift":
        minutes = _int(data.get("minutes"))
        if minutes is None or minutes == 0 or abs(minutes) > 1440:
            return None, {"minutes": "bad_shift"}
        return Action("shift", minutes), {}
    return None, {"base": "unknown_action"}


def condition_to_form(cond: Condition) -> dict[str, Any]:
    """Prefill values for the details step when editing an existing rule."""
    if cond.kind == "text":
        return {"field": cond.field, "op": cond.op, "value": cond.value}
    if cond.kind == "all_day":
        return {"value": bool(cond.value)}
    if cond.kind == "duration":
        return {"op": cond.op, "value": cond.value}
    if cond.kind == "weekday":
        return {"value": [str(d) for d in cond.value or []]}
    if cond.kind == "hour":
        return {"from_hour": cond.value[0], "to_hour": cond.value[1]}
    return {}


def action_to_form(action: Action) -> dict[str, Any]:
    if action.kind in TEMPLATE_ACTIONS:
        return {"template": action.value}
    if action.kind == "shift":
        return {"minutes": action.value}
    return {}


def describe_rule(rule: Rule) -> str:
    """Short language-neutral summary used in the rule list."""
    c, a = rule.condition, rule.action
    if c.kind == "always":
        cond = "always"
    elif c.kind == "text":
        cond = f'{c.field} {c.op} "{c.value}"'
    elif c.kind == "all_day":
        cond = "all-day" if c.value else "timed"
    elif c.kind == "duration":
        cond = f"duration {'>' if c.op == 'gt' else '<'} {c.value} min"
    elif c.kind == "weekday":
        cond = "weekday " + ",".join(str(d) for d in c.value)
    elif c.kind == "hour":
        cond = f"hour {c.value[0]}-{c.value[1]}"
    else:
        cond = c.kind
    if a.kind in TEMPLATE_ACTIONS:
        act = f'{a.kind} "{a.value}"'
    elif a.kind == "shift":
        act = f"shift {a.value:+d} min"
    else:
        act = a.kind
    return f"{cond} → {act}"


def move(items: list[Any], index: int, delta: int) -> int:
    """Move an item up (-1) or down (+1); returns its new index."""
    target = index + delta
    if 0 <= target < len(items):
        items[index], items[target] = items[target], items[index]
        return target
    return index
