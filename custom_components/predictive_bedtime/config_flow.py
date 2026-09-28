"""Config and options flows.

Setup is four short steps: who, their work calendars, how to tell when they sleep, and a
few starting habits. The habits
are only a starting point; once nights have been learned, actual behaviour takes over.
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    TimeSelector,
)

from .const import (
    CONF_ASLEEP,
    CONF_ASLEEP_STATES,
    CONF_CALENDARS,
    CONF_FREE_BEDTIME,
    CONF_IN_BED,
    CONF_KEYWORDS,
    CONF_MIN_SLEEP,
    CONF_NOTIFY,
    CONF_PAUSE_ENTITIES,
    CONF_PAUSE_STATES,
    CONF_PERSON,
    CONF_PREP,
    CONF_RETENTION,
    CONF_SETTLE,
    CONF_SLEEP_DEBT,
    CONF_TARGET_SLEEP,
    CONF_UNWIND,
    CONF_WAKE_GAP,
    CONF_WIND_DOWN,
    DEFAULT_OPTIONS,
    DEFAULT_PAUSE_STATES,
    DOMAIN,
    HOUSEHOLD_TITLE,
    KIND_HOUSEHOLD,
    TITLE_SUFFIX,
)
from .model import DEFAULT_ASLEEP_VALUES

PERSON = EntitySelector(EntitySelectorConfig(domain="person"))
CALENDARS = EntitySelector(EntitySelectorConfig(domain="calendar", multiple=True))
SIGNALS = EntitySelector(
    EntitySelectorConfig(domain=["binary_sensor", "input_boolean", "sensor"], multiple=True)
)
ASLEEP_STATES = SelectSelector(
    SelectSelectorConfig(options=list(DEFAULT_ASLEEP_VALUES), multiple=True, custom_value=True)
)


def _number(low: float, high: float, step: float, unit: str) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=low, max=high, step=step, unit_of_measurement=unit, mode=NumberSelectorMode.BOX
        )
    )


KEYWORDS = SelectSelector(
    SelectSelectorConfig(options=["work", "shift"], multiple=True, custom_value=True)
)

CALENDAR_CONTENT = "calendar_content"
ONLY_SHIFTS = "only_shifts"
MIXED = "mixed"

SOURCES_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CALENDARS): CALENDARS,
        vol.Required(CALENDAR_CONTENT, default=ONLY_SHIFTS): SelectSelector(
            SelectSelectorConfig(options=[ONLY_SHIFTS, MIXED], translation_key=CALENDAR_CONTENT)
        ),
    }
)

KEYWORDS_SCHEMA = vol.Schema({vol.Required(CONF_KEYWORDS): KEYWORDS})

SIGNALS_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_IN_BED, default=[]): SIGNALS,
        vol.Optional(CONF_ASLEEP, default=[]): SIGNALS,
        vol.Required(CONF_ASLEEP_STATES): ASLEEP_STATES,
    }
)

PAUSE_ENTITIES = EntitySelector(EntitySelectorConfig(multiple=True))
PAUSE_STATES = SelectSelector(
    SelectSelectorConfig(options=DEFAULT_PAUSE_STATES, multiple=True, custom_value=True)
)


def _learning_schema(notify_services: list[str]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(CONF_PAUSE_ENTITIES, default=[]): PAUSE_ENTITIES,
            vol.Optional(CONF_PAUSE_STATES, default=DEFAULT_PAUSE_STATES): PAUSE_STATES,
            vol.Optional(CONF_NOTIFY): SelectSelector(
                SelectSelectorConfig(options=notify_services, custom_value=True)
            ),
            vol.Required(CONF_SLEEP_DEBT, default=False): BooleanSelector(),
        }
    )


HABITS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_FREE_BEDTIME): TimeSelector(),
        vol.Required(CONF_TARGET_SLEEP): _number(4, 12, 0.25, "h"),
        vol.Required(CONF_PREP): _number(0, 240, 5, "min"),
        vol.Required(CONF_WIND_DOWN): _number(0, 240, 5, "min"),
    }
)

OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CALENDARS): CALENDARS,
        vol.Optional(CONF_KEYWORDS, default=[]): KEYWORDS,
    }
).extend(SIGNALS_SCHEMA.schema).extend(HABITS_SCHEMA.schema).extend(
    {
        vol.Required(CONF_UNWIND): _number(0, 240, 5, "min"),
        vol.Required(CONF_SETTLE): _number(5, 90, 5, "min"),
        vol.Required(CONF_WAKE_GAP): _number(5, 120, 5, "min"),
        vol.Required(CONF_MIN_SLEEP): _number(1, 8, 0.5, "h"),
        vol.Required(CONF_RETENTION): _number(30, 1095, 1, "d"),
    }
)


def _notify_services(hass) -> list[str]:
    """Phones and other targets that can show a notification with buttons."""
    return sorted(
        f"notify.{name}"
        for name in hass.services.async_services_for_domain("notify")
        if name not in ("send_message", "persistent_notification")
    )


def _check_signals(user_input: dict[str, Any]) -> dict[str, str]:
    if not user_input.get(CONF_IN_BED) and not user_input.get(CONF_ASLEEP):
        return {"base": "no_signals"}
    return {}


class PredictiveBedtimeConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 5

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="user", menu_options=["person", KIND_HOUSEHOLD])

    async def async_step_household(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(KIND_HOUSEHOLD)
        self._abort_if_unique_id_configured(error="household_exists")
        if user_input is not None:
            return self.async_create_entry(title=HOUSEHOLD_TITLE, data={"kind": KIND_HOUSEHOLD})
        return self.async_show_form(step_id=KIND_HOUSEHOLD, data_schema=vol.Schema({}))

    async def async_step_person(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_PERSON])
            self._abort_if_unique_id_configured()
            self._data.update(user_input)
            return await self.async_step_sources()

        return self.async_show_form(
            step_id="person", data_schema=vol.Schema({vol.Required(CONF_PERSON): PERSON})
        )

    async def async_step_sources(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input[CONF_CALENDARS]:
                errors[CONF_CALENDARS] = "no_calendars"
            else:
                self._data[CONF_CALENDARS] = user_input[CONF_CALENDARS]
                if user_input[CALENDAR_CONTENT] == MIXED:
                    return await self.async_step_keywords()
                self._data[CONF_KEYWORDS] = []
                return await self.async_step_signals()

        return self.async_show_form(
            step_id="sources",
            data_schema=self.add_suggested_values_to_schema(
                SOURCES_SCHEMA, user_input or {CONF_CALENDARS: self._suggest_calendars()}
            ),
            errors=errors,
            description_placeholders={"name": self._first_name()},
        )

    async def async_step_keywords(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            words = [w.strip() for w in user_input[CONF_KEYWORDS] if w.strip()]
            if not words:
                errors[CONF_KEYWORDS] = "no_keywords"
            else:
                self._data[CONF_KEYWORDS] = words
                return await self.async_step_signals()

        return self.async_show_form(
            step_id="keywords",
            data_schema=self.add_suggested_values_to_schema(
                KEYWORDS_SCHEMA, user_input or {CONF_KEYWORDS: ["work"]}
            ),
            errors=errors,
            description_placeholders={"name": self._first_name()},
        )

    async def async_step_signals(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _check_signals(user_input)
            if not errors:
                self._data.update(user_input)
                return await self.async_step_learning()

        return self.async_show_form(
            step_id="signals",
            data_schema=self.add_suggested_values_to_schema(
                SIGNALS_SCHEMA,
                user_input or {CONF_ASLEEP_STATES: list(DEFAULT_ASLEEP_VALUES)},
            ),
            errors=errors,
            description_placeholders={"name": self._first_name()},
        )

    async def async_step_learning(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_habits()

        return self.async_show_form(
            step_id="learning",
            data_schema=_learning_schema(_notify_services(self.hass)),
            description_placeholders={"name": self._first_name()},
        )

    async def async_step_habits(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title=f"{self._first_name()} {TITLE_SUFFIX}",
                data={CONF_PERSON: self._data[CONF_PERSON]},
                options={
                    **DEFAULT_OPTIONS,
                    CONF_CALENDARS: self._data[CONF_CALENDARS],
                    CONF_KEYWORDS: self._data.get(CONF_KEYWORDS, []),
                    CONF_IN_BED: self._data.get(CONF_IN_BED, []),
                    CONF_ASLEEP: self._data.get(CONF_ASLEEP, []),
                    CONF_ASLEEP_STATES: self._data[CONF_ASLEEP_STATES],
                    CONF_PAUSE_ENTITIES: self._data.get(CONF_PAUSE_ENTITIES, []),
                    CONF_PAUSE_STATES: self._data.get(CONF_PAUSE_STATES, DEFAULT_PAUSE_STATES),
                    CONF_SLEEP_DEBT: self._data.get(CONF_SLEEP_DEBT, False),
                    **(
                        {CONF_NOTIFY: self._data[CONF_NOTIFY]}
                        if self._data.get(CONF_NOTIFY)
                        else {}
                    ),
                    **user_input,
                },
            )

        return self.async_show_form(
            step_id="habits",
            data_schema=self.add_suggested_values_to_schema(HABITS_SCHEMA, DEFAULT_OPTIONS),
            description_placeholders={"name": self._first_name()},
        )

    def _first_name(self) -> str:
        person = self.hass.states.get(self._data.get(CONF_PERSON, ""))
        return person.name.split()[0] if person and person.name else "this person"

    def _suggest_calendars(self) -> list[str]:
        """Calendars whose name mentions the person, e.g. "Work (Sam)"."""
        name = self._first_name().lower()
        return [
            state.entity_id
            for state in self.hass.states.async_all("calendar")
            if name in (state.name or "").lower()
        ]

    @classmethod
    @callback
    def async_supports_options_flow(cls, config_entry: ConfigEntry) -> bool:
        return config_entry.data.get("kind") != KIND_HOUSEHOLD

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        return PredictiveBedtimeOptionsFlow()


class PredictiveBedtimeOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _check_signals(user_input)
            if not errors:
                return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                OPTIONS_SCHEMA.extend(_learning_schema(_notify_services(self.hass)).schema),
                user_input
                or {
                    **DEFAULT_OPTIONS,
                    CONF_ASLEEP_STATES: list(DEFAULT_ASLEEP_VALUES),
                    **self.config_entry.options,
                },
            ),
            errors=errors,
        )
