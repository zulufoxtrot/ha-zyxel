"""The Zyxel integration."""
import asyncio
import logging
from datetime import timedelta

import async_timeout
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from nr7101 import nr7101

from .const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "button"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Zyxel integration from a config entry."""
    host = entry.data[CONF_HOST]
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]

    try:
        # Create router instance
        router = nr7101.NR7101(host, username, password)

        # Test that we can get data
        data = await hass.async_add_executor_job(router.get_status)
        if not data:
            raise Exception("No data received from router")

    except Exception as ex:
        _LOGGER.error("Could not connect to Zyxel router: %s", ex)
        raise ConfigEntryNotReady from ex

    async def async_update_data():
        """Fetch data from the router."""
        try:
            async with async_timeout.timeout(30):
                # Note: This uses a synchronous library in an async context
                # We use async_add_executor_job for blocking calls
                data = await hass.async_add_executor_job(router.get_status)
                if not data:
                    raise UpdateFailed("No data received from router")
                return data
        except Exception as err:
            raise UpdateFailed(f"Error communicating with router: {err}") from err

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=DOMAIN,
        update_method=async_update_data,
        update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
    )

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "router": router,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = all(
        await asyncio.gather(
            *[
                hass.config_entries.async_forward_entry_unload(entry, platform)
                for platform in PLATFORMS
            ]
        )
    )
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok