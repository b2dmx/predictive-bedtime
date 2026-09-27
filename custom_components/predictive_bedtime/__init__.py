"""Predictive Bedtime: learns when each person goes to sleep from their work calendars."""
from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import BedtimeConfigEntry, BedtimeCoordinator

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: BedtimeConfigEntry) -> bool:
    # Entries are titled with the first name only.
    if " " in entry.title.strip():
        hass.config_entries.async_update_entry(entry, title=entry.title.split()[0])
    coordinator = BedtimeCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    coordinator.async_start_tracking()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BedtimeConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload(hass: HomeAssistant, entry: BedtimeConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
