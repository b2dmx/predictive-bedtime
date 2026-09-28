"""Predictive Bedtime: learns when each person goes to sleep from their work calendars."""
from __future__ import annotations

import voluptuous as vol

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_ASLEEP,
    CONF_ASLEEP_STATES,
    CONF_BED_SENSOR,
    CONF_IN_BED,
    DOMAIN,
    TITLE_SUFFIX,
)
from .coordinator import BedtimeConfigEntry, BedtimeCoordinator
from .model import DEFAULT_ASLEEP_VALUES

PLATFORMS = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SENSOR, Platform.SWITCH]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

FORGET_SCHEMA = vol.Schema({vol.Required("config_entry_id"): cv.string})


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async def forget_last_night(call: ServiceCall) -> None:
        entry = hass.config_entries.async_get_entry(call.data["config_entry_id"])
        if entry is None or entry.domain != DOMAIN or not hasattr(entry, "runtime_data"):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="entry_not_loaded"
            )
        entry.runtime_data.async_forget_night()

    hass.services.async_register(DOMAIN, "forget_last_night", forget_last_night, FORGET_SCHEMA)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: BedtimeConfigEntry) -> bool:
    coordinator = BedtimeCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    coordinator.async_start_tracking()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: BedtimeConfigEntry) -> bool:
    if entry.version == 1:
        # One bed sensor became lists of in-bed signals and sleep trackers.
        options = dict(entry.options)
        bed = options.pop(CONF_BED_SENSOR, None)
        options[CONF_IN_BED] = [bed] if bed else []
        options.setdefault(CONF_ASLEEP, [])
        options.setdefault(CONF_ASLEEP_STATES, list(DEFAULT_ASLEEP_VALUES))
        hass.config_entries.async_update_entry(entry, options=options, version=2)
    if entry.version in (2, 3):
        # Titles became "<first name> Predictive Bedtime". A title the user chose is kept.
        title = entry.title.strip()
        if entry.version == 2 or title.endswith(" Predictive Sleep"):
            title = f"{title.split()[0]} {TITLE_SUFFIX}" if title else title
        hass.config_entries.async_update_entry(entry, title=title, version=4)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BedtimeConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload(hass: HomeAssistant, entry: BedtimeConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
