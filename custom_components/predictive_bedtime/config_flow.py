"""Config and options flows.

Setup is three short steps: who, which data sources, and a few starting habits. The habits
are only a starting point; once nights have been learned, actual behaviour takes over.
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TimeSelector,
)

from .const import (
    CONF_BED_SENSOR,
    CONF_CALENDARS,
    CONF_FREE_BEDTIME,
    CONF_MIN_SLEEP,
    CONF_PERSON,
    CONF_PREP,
    CONF_RETENTION,
    CONF_SETTLE,
    CONF_TARGET_SLEEP,
    CONF_UNWIND,
    CONF_WAKE_GAP,
    CONF_WIND_DOWN,
    DEFAULT_OPTIONS,
    DOMAIN,
)

PERSON = EntitySelector(EntitySelectorConfig(domain="person"))
CALENDARS = EntitySelector(EntitySelectorConfig(domain="calendar", multiple=True))
BED = EntitySelector(EntitySelectorConfig(domain="binary_sensor"))


def _number(low: float, high: float, step: float, unit: str) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=low, max=high, step=step, unit_of_measurement=unit, mode=NumberSelectorMode.BOX
        )
    )


SOURCES_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CALENDARS): CALENDARS,
        vol.Required(CONF_BED_SENSOR): BED,
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

OPTIONS_SCHEMA = SOURCES_SCHEMA.extend(HABITS_SCHEMA.schema).extend(
    {
        vol.Required(CONF_UNWIND): _number(0, 240, 5, "min"),
        vol.Required(CONF_SETTLE): _number(5, 90, 5, "min"),
        vol.Required(CONF_WAKE_GAP): _number(5, 120, 5, "min"),
        vol.Required(CONF_MIN_SLEEP): _number(1, 8, 0.5, "h"),
        vol.Required(CONF_RETENTION): _number(30, 1095, 1, "d"),
    }
)


class PredictiveBedtimeConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_PERSON])
            self._abort_if_unique_id_configured()
            self._data.update(user_input)
            return await self.async_step_sources()

        return self.async_show_form(
            step_id="user", data_schema=vol.Schema({vol.Required(CONF_PERSON): PERSON})
        )

    async def async_step_sources(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input[CONF_CALENDARS]:
                errors[CONF_CALENDARS] = "no_calendars"
            elif self.hass.states.get(user_input[CONF_BED_SENSOR]) is None:
                errors[CONF_BED_SENSOR] = "missing_entity"
            else:
                self._data.update(user_input)
                return await self.async_step_habits()

        return self.async_show_form(
            step_id="sources",
            data_schema=self.add_suggested_values_to_schema(
                SOURCES_SCHEMA, user_input or {CONF_CALENDARS: self._suggest_calendars()}
            ),
            errors=errors,
            description_placeholders={"name": self._first_name()},
        )

    async def async_step_habits(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title=self._first_name(),
                data={CONF_PERSON: self._data[CONF_PERSON]},
                options={
                    **DEFAULT_OPTIONS,
                    CONF_CALENDARS: self._data[CONF_CALENDARS],
                    CONF_BED_SENSOR: self._data[CONF_BED_SENSOR],
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

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        return PredictiveBedtimeOptionsFlow()


class PredictiveBedtimeOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                OPTIONS_SCHEMA, {**DEFAULT_OPTIONS, **self.config_entry.options}
            ),
        )
