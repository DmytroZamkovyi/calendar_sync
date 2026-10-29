"""Config flow: one service entry, each sync pair is a subentry with a rules editor.

Only native Home Assistant forms/selectors are used.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from .adapter import AdapterError
from .const import (
    CONF_CALENDAR_1,
    CONF_CALENDAR_2,
    CONF_CLOSED_MODE,
    CONF_CONFLICT_POLICY,
    CONF_DAYS_BACK,
    CONF_DAYS_FORWARD,
    CONF_DIRECTION_1TO2,
    CONF_DIRECTION_2TO1,
    CONF_DRY_RUN,
    CONF_INTERVAL,
    CONF_PROPAGATE_DELETES,
    CONF_RULES_1TO2,
    CONF_RULES_2TO1,
    DEFAULT_INTERVAL,
    DOMAIN,
    MAX_DAYS,
    SUBENTRY_PAIR,
)
from .core.models import (
    CLOSED_IGNORE,
    CLOSED_REVERT,
    CONFLICT_ASK,
    CONFLICT_CALENDAR1,
    CONFLICT_CALENDAR2,
)
from .core.rules import (
    ACTION_KINDS,
    CONDITION_KINDS,
    Rule,
    NUMBER_OPS,
    TEXT_FIELDS,
    TEXT_OPS,
    rule_from_dict,
    rule_to_dict,
)
from .ha_calendar import EntityAccess
from .pair import normalize_pair_data, pair_title
from .rules_form import (
    NO_DETAILS_ACTIONS,
    TEMPLATE_ACTIONS,
    action_to_form,
    build_action,
    build_condition,
    condition_to_form,
    describe_rule,
    move,
)

ADD = "__add"
BACK = "__back"

WEEKDAYS = ["0", "1", "2", "3", "4", "5", "6"]  # Monday = 0


def _select(
    options: list[str] | dict[str, str],
    *,
    key: str | None = None,
    multiple: bool = False,
    mode: SelectSelectorMode = SelectSelectorMode.DROPDOWN,
) -> SelectSelector:
    """Dropdown. With `key`, labels come from `selector.<key>.options` translations."""
    if isinstance(options, dict):
        opts: Any = [SelectOptionDict(value=k, label=v) for k, v in options.items()]
    else:
        opts = options
    return SelectSelector(
        SelectSelectorConfig(
            options=opts,
            mode=mode,
            multiple=multiple,
            translation_key=key,
        )
    )


def _number(minimum: float, maximum: float, unit: str | None = None) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=1,
            mode=NumberSelectorMode.BOX,
            unit_of_measurement=unit,
        )
    )


class CalendarSyncConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="Calendar Sync", data={})
        return self.async_show_form(step_id="user", data_schema=vol.Schema({}))

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_PAIR: PairFlow}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return IntervalOptionsFlow()


class IntervalOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data={CONF_INTERVAL: int(user_input[CONF_INTERVAL])})
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_INTERVAL,
                    default=self.config_entry.options.get(CONF_INTERVAL, DEFAULT_INTERVAL),
                ): _number(1, 1440, "min")
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)


class PairFlow(ConfigSubentryFlow):
    """Create/reconfigure a pair: settings -> rules menu -> rule editor."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._loaded = False
        self._dir = "1to2"
        self._index: int | None = None  # rule being edited (None = new)
        self._cond_kind = "always"
        self._cond: Any = None
        self._action_kind = "skip"

    # ---------------------------------------------------------------- helpers

    @property
    def _reconfigure(self) -> bool:
        return self.source == SOURCE_RECONFIGURE

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if self._reconfigure:
            self._data = normalize_pair_data(self._get_reconfigure_subentry().data)
        else:
            self._data = normalize_pair_data({})

    def _rules_key(self) -> str:
        return CONF_RULES_1TO2 if self._dir == "1to2" else CONF_RULES_2TO1

    def _rules(self) -> list[dict[str, Any]]:
        return self._data[self._rules_key()]

    def _name(self, entity_id: str) -> str:
        state = self.hass.states.get(entity_id)
        return state.name if state else entity_id

    def _placeholders(self) -> dict[str, str]:
        c1, c2 = self._data.get(CONF_CALENDAR_1, ""), self._data.get(CONF_CALENDAR_2, "")
        a, b = (self._name(c1), self._name(c2)) if self._dir == "1to2" else (
            self._name(c2),
            self._name(c1),
        )
        return {"source": a, "target": b}

    def _duplicate(self, c1: str, c2: str) -> bool:
        own = self._get_reconfigure_subentry().subentry_id if self._reconfigure else None
        for sub in self._get_entry().subentries.values():
            if sub.subentry_id == own or sub.subentry_type != SUBENTRY_PAIR:
                continue
            if {sub.data[CONF_CALENDAR_1], sub.data[CONF_CALENDAR_2]} == {c1, c2}:
                return True
        return False

    def _validate(self, data: dict[str, Any]) -> dict[str, str]:
        c1, c2 = data[CONF_CALENDAR_1], data[CONF_CALENDAR_2]
        d12, d21 = data[CONF_DIRECTION_1TO2], data[CONF_DIRECTION_2TO1]
        if c1 == c2:
            return {CONF_CALENDAR_2: "same_calendar"}
        if not (d12 or d21):
            return {CONF_DIRECTION_1TO2: "no_direction"}
        if self._duplicate(c1, c2):
            return {"base": "pair_exists"}
        caps = {}
        for conf, eid in ((CONF_CALENDAR_1, c1), (CONF_CALENDAR_2, c2)):
            try:
                caps[conf] = EntityAccess(self.hass, eid).capabilities()
            except AdapterError:
                return {conf: "calendar_unavailable"}
        if d12 and not caps[CONF_CALENDAR_2].create:
            return {CONF_CALENDAR_2: "read_only"}
        if d21 and not caps[CONF_CALENDAR_1].create:
            return {CONF_CALENDAR_1: "read_only"}
        return {}

    # --------------------------------------------------------------- settings

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._settings("user", user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._settings("reconfigure", user_input)

    async def _settings(self, step_id: str, user_input: dict[str, Any] | None) -> SubentryFlowResult:
        self._load()
        errors: dict[str, str] = {}
        if user_input is not None:
            candidate = {**self._data, **user_input}
            candidate[CONF_DAYS_BACK] = int(candidate[CONF_DAYS_BACK])
            candidate[CONF_DAYS_FORWARD] = int(candidate[CONF_DAYS_FORWARD])
            errors = self._validate(candidate)
            if not errors:
                self._data = candidate
                return await self.async_step_rules()
            self._data = candidate

        cal = EntitySelector(EntitySelectorConfig(domain="calendar"))
        schema = vol.Schema(
            {
                vol.Required(CONF_CALENDAR_1): cal,
                vol.Required(CONF_CALENDAR_2): cal,
                vol.Required(CONF_DIRECTION_1TO2): BooleanSelector(),
                vol.Required(CONF_DIRECTION_2TO1): BooleanSelector(),
                vol.Required(CONF_CLOSED_MODE): _select(
                    [CLOSED_REVERT, CLOSED_IGNORE], key="closed_mode"
                ),
                vol.Required(CONF_PROPAGATE_DELETES): BooleanSelector(),
                vol.Required(CONF_CONFLICT_POLICY): _select(
                    [CONFLICT_CALENDAR1, CONFLICT_CALENDAR2, CONFLICT_ASK],
                    key="conflict_policy",
                ),
                vol.Required(CONF_DAYS_BACK): _number(0, MAX_DAYS, "d"),
                vol.Required(CONF_DAYS_FORWARD): _number(1, MAX_DAYS, "d"),
                vol.Required(CONF_DRY_RUN): BooleanSelector(),
            }
        )
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(schema, self._data),
            errors=errors,
        )

    # -------------------------------------------------------------- rules menu

    async def async_step_rules(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        options = []
        if self._data[CONF_DIRECTION_1TO2]:
            options.append("rules_1to2")
        if self._data[CONF_DIRECTION_2TO1]:
            options.append("rules_2to1")
        options.append("save")
        return self.async_show_menu(step_id="rules", menu_options=options)

    async def async_step_rules_1to2(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        self._dir = "1to2"
        return await self.async_step_list()

    async def async_step_rules_2to1(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        self._dir = "2to1"
        return await self.async_step_list()

    async def async_step_save(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        data = dict(self._data)
        title = pair_title(
            self._name(data[CONF_CALENDAR_1]),
            self._name(data[CONF_CALENDAR_2]),
            data[CONF_DIRECTION_1TO2],
            data[CONF_DIRECTION_2TO1],
        )
        if self._reconfigure:
            return self.async_update_and_abort(
                self._get_entry(),
                self._get_reconfigure_subentry(),
                title=title,
                data=data,
            )
        return self.async_create_entry(
            title=title,
            data=data,
            unique_id="|".join(sorted((data[CONF_CALENDAR_1], data[CONF_CALENDAR_2]))),
        )

    # -------------------------------------------------------------- rule list

    async def async_step_list(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        rules = self._rules()
        if user_input is not None:
            choice = user_input["choice"]
            if choice == BACK:
                return await self.async_step_rules()
            if choice == ADD:
                self._index = None
                return await self.async_step_cond_kind()
            self._index = int(choice)
            return await self.async_step_rule_menu()
        options = {str(i): f"{i + 1}. {describe_rule(rule_from_dict(r))}" for i, r in enumerate(rules)}
        options[ADD] = "+"
        options[BACK] = "←"
        return self.async_show_form(
            step_id="list",
            data_schema=vol.Schema(
                {vol.Required("choice", default=BACK): _select(options, mode=SelectSelectorMode.LIST)}
            ),
            description_placeholders={**self._placeholders(), "count": str(len(rules))},
        )

    async def async_step_rule_menu(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return self.async_show_menu(
            step_id="rule_menu",
            menu_options=["rule_edit", "rule_up", "rule_down", "rule_delete", "rule_back"],
            description_placeholders={
                "rule": describe_rule(rule_from_dict(self._rules()[self._index or 0]))
            },
        )

    async def async_step_rule_back(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self.async_step_list()

    async def async_step_rule_up(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        move(self._rules(), self._index or 0, -1)
        return await self.async_step_list()

    async def async_step_rule_down(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        move(self._rules(), self._index or 0, 1)
        return await self.async_step_list()

    async def async_step_rule_delete(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        del self._rules()[self._index or 0]
        return await self.async_step_list()

    async def async_step_rule_edit(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self.async_step_cond_kind()

    # ------------------------------------------------------------- rule editor

    def _current(self) -> Any:
        if self._index is None:
            return None
        return rule_from_dict(self._rules()[self._index])

    async def async_step_cond_kind(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        current = self._current()
        if user_input is not None:
            self._cond_kind = user_input["kind"]
            return await getattr(self, f"async_step_cond_{self._cond_kind}")()
        schema = vol.Schema(
            {
                vol.Required("kind", default=current.condition.kind if current else "always"): _select(
                    list(CONDITION_KINDS), key="condition_kind", mode=SelectSelectorMode.LIST
                )
            }
        )
        return self.async_show_form(
            step_id="cond_kind", data_schema=schema, description_placeholders=self._placeholders()
        )

    async def _cond_step(
        self, step_id: str, schema: vol.Schema, user_input: dict[str, Any] | None
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        suggested: dict[str, Any] = {}
        current = self._current()
        if current and current.condition.kind == self._cond_kind:
            suggested = condition_to_form(current.condition)
        if user_input is not None:
            cond, errors = build_condition(self._cond_kind, user_input)
            if not errors:
                self._cond = cond
                return await self.async_step_action_kind()
            suggested = user_input
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            errors=errors,
            description_placeholders=self._placeholders(),
        )

    async def async_step_cond_always(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        self._cond, _ = build_condition("always", {})
        return await self.async_step_action_kind()

    async def async_step_cond_text(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        schema = vol.Schema(
            {
                vol.Required("field", default="summary"): _select(list(TEXT_FIELDS), key="text_field"),
                vol.Required("op", default="contains"): _select(list(TEXT_OPS), key="text_op"),
                vol.Required("value"): TextSelector(),
            }
        )
        return await self._cond_step("cond_text", schema, user_input)

    async def async_step_cond_all_day(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        schema = vol.Schema({vol.Required("value", default=True): BooleanSelector()})
        return await self._cond_step("cond_all_day", schema, user_input)

    async def async_step_cond_duration(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        schema = vol.Schema(
            {
                vol.Required("op", default="gt"): _select(list(NUMBER_OPS), key="number_op"),
                vol.Required("value", default=60): _number(1, 100000, "min"),
            }
        )
        return await self._cond_step("cond_duration", schema, user_input)

    async def async_step_cond_weekday(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        schema = vol.Schema(
            {
                vol.Required("value"): _select(
                    WEEKDAYS, key="weekday", multiple=True, mode=SelectSelectorMode.LIST
                )
            }
        )
        return await self._cond_step("cond_weekday", schema, user_input)

    async def async_step_cond_hour(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        schema = vol.Schema(
            {
                vol.Required("from_hour", default=9): _number(0, 24, "h"),
                vol.Required("to_hour", default=18): _number(0, 24, "h"),
            }
        )
        return await self._cond_step("cond_hour", schema, user_input)

    async def async_step_action_kind(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        current = self._current()
        if user_input is not None:
            self._action_kind = user_input["kind"]
            if self._action_kind in NO_DETAILS_ACTIONS:
                return await self._commit(build_action(self._action_kind, {})[0])
            if self._action_kind == "shift":
                return await self.async_step_action_shift()
            return await self.async_step_action_template()
        schema = vol.Schema(
            {
                vol.Required("kind", default=current.action.kind if current else "skip"): _select(
                    list(ACTION_KINDS), key="action_kind", mode=SelectSelectorMode.LIST
                )
            }
        )
        return self.async_show_form(
            step_id="action_kind", data_schema=schema, description_placeholders=self._placeholders()
        )

    async def _action_step(
        self, step_id: str, schema: vol.Schema, user_input: dict[str, Any] | None
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        suggested: dict[str, Any] = {}
        current = self._current()
        if current and current.action.kind == self._action_kind:
            suggested = action_to_form(current.action)
        if user_input is not None:
            action, errors = build_action(self._action_kind, user_input)
            if not errors:
                return await self._commit(action)
            suggested = user_input
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            errors=errors,
            description_placeholders={
                **self._placeholders(),
                "placeholders": "{summary}, {description}, {location}, {start}, {end}",
            },
        )

    async def async_step_action_template(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        assert self._action_kind in TEMPLATE_ACTIONS
        schema = vol.Schema({vol.Required("template"): TextSelector()})
        return await self._action_step("action_template", schema, user_input)

    async def async_step_action_shift(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        schema = vol.Schema({vol.Required("minutes", default=60): _number(-1440, 1440, "min")})
        return await self._action_step("action_shift", schema, user_input)

    async def _commit(self, action: Any) -> SubentryFlowResult:
        rule = rule_to_dict(Rule(self._cond, action))
        rules = self._rules()
        if self._index is None:
            rules.append(rule)
        else:
            rules[self._index] = rule
        return await self.async_step_list()
