"""Pure parts of the integration layer: pair settings and rule form conversion."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from custom_components.calendar_sync import pair, rules_form
from custom_components.calendar_sync.core.rules import Action, Condition, Rule, rule_to_dict

TZ = timezone.utc
ROOT = Path(__file__).resolve().parent.parent


def test_window_covers_whole_days():
    w = pair.window_for(datetime(2026, 11, 2, 15, 30, tzinfo=TZ), TZ, 7, 14)
    assert w.start == datetime(2026, 10, 26, tzinfo=TZ)
    assert w.end == datetime(2026, 11, 17, tzinfo=TZ)


def test_pair_config_defaults_and_rules():
    rule = Rule(Condition("text", "summary", "contains", "x"), Action("skip"))
    cfg = pair.pair_config(
        {"rules_1to2": [rule_to_dict(rule)]}, TZ, datetime(2026, 11, 2, tzinfo=TZ)
    )
    assert cfg.direction_1to2 and cfg.direction_2to1
    assert len(cfg.rules_1to2) == 1 and cfg.rules_2to1 == []


def test_pair_title_arrows():
    assert pair.pair_title("A", "B", True, True) == "A ⇄ B"
    assert pair.pair_title("A", "B", True, False) == "A → B"
    assert pair.pair_title("A", "B", False, True) == "A ← B"


@pytest.mark.parametrize(
    "kind,data,ok",
    [
        ("always", {}, True),
        ("text", {"field": "summary", "op": "contains", "value": "  "}, False),
        ("text", {"field": "summary", "op": "regex", "value": "("}, False),
        ("text", {"field": "summary", "op": "regex", "value": "^a"}, True),
        ("duration", {"op": "gt", "value": 0}, False),
        ("duration", {"op": "gt", "value": 30.0}, True),
        ("weekday", {"value": []}, False),
        ("weekday", {"value": ["0", "4"]}, True),
        ("hour", {"from_hour": 9, "to_hour": 9}, False),
        ("hour", {"from_hour": 9, "to_hour": 18}, True),
    ],
)
def test_build_condition(kind, data, ok):
    cond, errors = rules_form.build_condition(kind, data)
    assert (cond is not None) == ok and bool(errors) != ok


@pytest.mark.parametrize(
    "kind,data,ok",
    [
        ("skip", {}, True),
        ("set_summary", {"template": ""}, False),
        ("set_summary", {"template": "{summary}!"}, True),
        ("shift", {"minutes": 0}, False),
        ("shift", {"minutes": 2000}, False),
        ("shift", {"minutes": -30}, True),
    ],
)
def test_build_action(kind, data, ok):
    action, errors = rules_form.build_action(kind, data)
    assert (action is not None) == ok and bool(errors) != ok


def test_form_roundtrip_prefill():
    cond, _ = rules_form.build_condition("hour", {"from_hour": 9, "to_hour": 18})
    assert rules_form.condition_to_form(cond) == {"from_hour": 9, "to_hour": 18}
    action, _ = rules_form.build_action("shift", {"minutes": 15})
    assert rules_form.action_to_form(action) == {"minutes": 15}


def test_move_and_describe():
    items = [1, 2, 3]
    assert rules_form.move(items, 2, -1) == 1 and items == [1, 3, 2]
    assert rules_form.move(items, 0, -1) == 0
    text = rules_form.describe_rule(Rule(Condition("always"), Action("shift", -30)))
    assert "-30" in text


def test_translations_have_same_structure_and_manifest_is_valid():
    def keys(d, prefix=""):
        out = set()
        for k, v in d.items():
            out.add(prefix + k)
            if isinstance(v, dict):
                out |= keys(v, prefix + k + ".")
        return out

    en = json.loads((ROOT / "translations/en.json").read_text("utf-8"))
    uk = json.loads((ROOT / "translations/uk.json").read_text("utf-8"))
    assert keys(en) == keys(uk)
    manifest = json.loads((ROOT / "manifest.json").read_text("utf-8"))
    assert manifest["domain"] == "calendar_sync" and manifest["config_flow"] is True
