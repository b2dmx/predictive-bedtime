"""Predictive Bedtime: learns when each person goes to sleep from their work calendars."""
from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import (
    CONF_ASLEEP,
    CONF_ASLEEP_STATES,
    CONF_BED_SENSOR,
    CONF_IN_BED,
    TITLE_SUFFIX,
)
from .coordinator import BedtimeConfigEntry, BedtimeCoordinator
from .model import DEFAULT_ASLEEP_VALUES

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]


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
    if entry.version == 2:
        # Titles became "<first name> Predictive Sleep".
        first = entry.title.split()[0] if entry.title.strip() else entry.title
        hass.config_entries.async_update_entry(
            entry, title=f"{first} {TITLE_SUFFIX}", version=3
        )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BedtimeConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload(hass: HomeAssistant, entry: BedtimeConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
