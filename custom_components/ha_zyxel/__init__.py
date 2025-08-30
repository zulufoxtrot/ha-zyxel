"""The Zyxel integration."""
import asyncio
import logging
from datetime import timedelta

import async_timeout
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from custom_components.ha_zyxel.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# Block excessive nr7101 debug logging
nr7101_logger = logging.getLogger("nr7101.nr7101")
nr7101_logger.setLevel(logging.WARNING)

try:
    from nr7101 import nr7101
    NR7101_AVAILABLE = True
except ImportError:
    _LOGGER.error("Failed to import nr7101 library - check requirements in manifest.json")
    NR7101_AVAILABLE = False
    nr7101 = None

PLATFORMS = ["sensor", "button"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Zyxel integration from a config entry."""
    if not NR7101_AVAILABLE or nr7101 is None:
        raise ConfigEntryNotReady("Required nr7101 library is not available")

    host = entry.data[CONF_HOST]
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]

    try:
        router = await hass.async_add_executor_job(
            nr7101.NR7101, host, username, password
        )
    except Exception as ex:
        _LOGGER.error("Could not connect to Zyxel router: %s", ex)
        raise ConfigEntryNotReady from ex

    async def async_update_data():
        """Fetch data from the router."""
        try:
            async with async_timeout.timeout(15):
                def get_all_data():
                    # Login only if we don't have a valid session
                    if not hasattr(router, '_session_valid') or not router._session_valid:
                        login_success = router.login()
                        if not login_success:
                            raise UpdateFailed("Login failed during data update")
                        router._session_valid = True

                    data = router.get_status()
                    if not data:
                        # Session may have expired, try login once more
                        router._session_valid = False
                        login_success = router.login()
                        if not login_success:
                            raise UpdateFailed("Login failed after session timeout")
                        router._session_valid = True
                        data = router.get_status()

                    if not data:
                        raise UpdateFailed("No data received from router")

                    # Get device info if not already in data
                    if "device" not in data or not data["device"]:
                        device_info = router.get_json_object("status")
                        if device_info:
                            data["device_info"] = device_info

                    return data

                return await hass.async_add_executor_job(get_all_data)
        except asyncio.TimeoutError:
            router._session_valid = False
            raise UpdateFailed("Router data fetch timed out")
        except Exception as err:
            router._session_valid = False
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
