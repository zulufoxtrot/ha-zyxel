# custom_components/zyxel_nr7101/__init__.py
"""The Zyxel NR7101 integration."""
import asyncio
import logging
from datetime import timedelta

import async_timeout
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from nr7101 import nr7101  # Import the correct module/class

from .const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Zyxel NR7101 from a config entry."""
    host = entry.data[CONF_HOST]
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]

    try:
        # Create router instance
        # Note: Adjusting based on actual library usage
        router = nr7101.NR7101(host, username, password)

        # Test connection
        await hass.async_add_executor_job(router.login)
    except Exception as ex:
        _LOGGER.error("Could not connect to Zyxel NR7101 router: %s", ex)
        raise ConfigEntryNotReady from ex

    async def async_update_data():
        """Fetch data from the router."""
        try:
            async with async_timeout.timeout(30):
                # Note: This uses a synchronous library in an async context
                # We use async_add_executor_job for blocking calls
                data = {}

                # Login to get session
                await hass.async_add_executor_job(router.login)

                try:
                    # Get signal status (including RSSI)
                    signal_status = await hass.async_add_executor_job(router.get_signal_status)
                    if signal_status:
                        data["rssi"] = signal_status.get("rssi")
                        data["signal_strength"] = signal_status.get("signalStrength")
                        data["cell_id"] = signal_status.get("cellId")
                        data["connection_status"] = signal_status.get("status")
                        data["network_type"] = signal_status.get("networkType")

                    # Get device information
                    device_info = await hass.async_add_executor_job(router.get_device_info)
                    if device_info:
                        data["firmware_version"] = device_info.get("firmwareVersion")
                        data["model"] = device_info.get("modelName")
                finally:
                    # Always logout to clean up session
                    await hass.async_add_executor_job(router.logout)

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

    for platform in PLATFORMS:
        hass.async_create_task(
            hass.config_entries.async_forward_entry_setup(entry, platform)
        )

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
        router = hass.data[DOMAIN][entry.entry_id]["router"]
        await hass.async_add_executor_job(router.logout)
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok