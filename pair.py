"""Settings of one sync pair: defaults, title and conversion to `PairConfig`.

No Home Assistant imports.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, time, timedelta, tzinfo
from typing import Any

from .const import (
    CONF_CLOSED_MODE,
    CONF_CONFLICT_POLICY,
    CONF_DAYS_BACK,
    CONF_DAYS_FORWARD,
    CONF_DIRECTION_1TO2,
    CONF_DIRECTION_2TO1,
    CONF_DRY_RUN,
    CONF_PROPAGATE_DELETES,
    CONF_RULES_1TO2,
    CONF_RULES_2TO1,
    DEFAULT_DAYS_BACK,
    DEFAULT_DAYS_FORWARD,
)
from .core.models import (
    CLOSED_REVERT,
    CONFLICT_CALENDAR1,
    PairConfig,
    Window,
)
from .core.rules import rule_from_dict

PAIR_DEFAULTS: dict[str, Any] = {
    CONF_DIRECTION_1TO2: True,
    CONF_DIRECTION_2TO1: True,
    CONF_CLOSED_MODE: CLOSED_REVERT,
    CONF_PROPAGATE_DELETES: True,
    CONF_CONFLICT_POLICY: CONFLICT_CALENDAR1,
    CONF_DAYS_BACK: DEFAULT_DAYS_BACK,
    CONF_DAYS_FORWARD: DEFAULT_DAYS_FORWARD,
    CONF_DRY_RUN: False,
    CONF_RULES_1TO2: [],
    CONF_RULES_2TO1: [],
}


def normalize_pair_data(data: Mapping[str, Any]) -> dict[str, Any]:
    """Pair data with all defaults filled in (stored data may predate new options)."""
    merged = {**PAIR_DEFAULTS, **data}
    merged[CONF_RULES_1TO2] = list(merged.get(CONF_RULES_1TO2) or [])
    merged[CONF_RULES_2TO1] = list(merged.get(CONF_RULES_2TO1) or [])
    merged[CONF_DAYS_BACK] = int(merged[CONF_DAYS_BACK])
    merged[CONF_DAYS_FORWARD] = int(merged[CONF_DAYS_FORWARD])
    return merged


def pair_title(name1: str, name2: str, direction_1to2: bool, direction_2to1: bool) -> str:
    arrow = "⇄" if direction_1to2 and direction_2to1 else "→" if direction_1to2 else "←"
    return f"{name1} {arrow} {name2}"


def window_for(now: datetime, tz: tzinfo, days_back: int, days_forward: int) -> Window:
    """Whole local days: from `days_back` days ago to the end of day `days_forward` ahead."""
    today = now.astimezone(tz).date()
    start = datetime.combine(today - timedelta(days=days_back), time.min, tzinfo=tz)
    end = datetime.combine(today + timedelta(days=days_forward + 1), time.min, tzinfo=tz)
    return Window(start, end)


def pair_config(data: Mapping[str, Any], tz: tzinfo, now: datetime) -> PairConfig:
    d = normalize_pair_data(data)
    return PairConfig(
        tz=tz,
        window=window_for(now, tz, d[CONF_DAYS_BACK], d[CONF_DAYS_FORWARD]),
        direction_1to2=d[CONF_DIRECTION_1TO2],
        direction_2to1=d[CONF_DIRECTION_2TO1],
        closed_mode=d[CONF_CLOSED_MODE],
        propagate_deletes=d[CONF_PROPAGATE_DELETES],
        conflict_policy=d[CONF_CONFLICT_POLICY],
        rules_1to2=[rule_from_dict(r) for r in d[CONF_RULES_1TO2]],
        rules_2to1=[rule_from_dict(r) for r in d[CONF_RULES_2TO1]],
    )
