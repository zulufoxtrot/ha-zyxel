"""The Zyxel integration."""
import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from .coordinator import ZyxelDataUpdateCoordinator
from .const import *

_LOGGER = logging.getLogger(__name__)

# Block excessive nr7101 debug logging
#nr7101_logger = logging.getLogger("nr7101.nr7101")
#nr7101_logger.setLevel(logging.WARNING)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Zyxel integration from a config entry."""

    if hass.data.get(DOMAIN) is None:
        hass.data.setdefault(DOMAIN, {})    
    
    coordinator = ZyxelDataUpdateCoordinator(hass, entry=entry)
    entry.runtime_data = coordinator

    await coordinator.async_config_entry_first_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""    
    coordinator: ZyxelDataUpdateCoordinator = entry.runtime_data
    if not coordinator:
        return True

    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        entry.runtime_data = None
    
    await coordinator.router.close()
    return unload_ok


