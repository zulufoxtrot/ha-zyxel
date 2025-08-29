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

# Completely block nr7101 debug and info logging
class NR7101LogFilter:
    def filter(self, record):
        return record.levelno >= logging.WARNING

nr7101_logger = logging.getLogger("nr7101.nr7101")
nr7101_logger.setLevel(logging.WARNING)
nr7101_logger.addFilter(NR7101LogFilter())

try:
    from nr7101 import nr7101
    NR7101_AVAILABLE = True
    _LOGGER.debug("Successfully imported nr7101 library in __init__.py")
except ImportError as e:
    _LOGGER.error(f"Failed to import nr7101 library in __init__.py: {e}")
    NR7101_AVAILABLE = False
    nr7101 = None
except Exception as e:
    _LOGGER.error(f"Unexpected error importing nr7101 library in __init__.py: {e}")
    NR7101_AVAILABLE = False
    nr7101 = None

PLATFORMS = ["sensor", "button"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Zyxel integration from a config entry."""
    # Check if nr7101 library is available
    if not NR7101_AVAILABLE or nr7101 is None:
        _LOGGER.error("nr7101 library is not available in __init__.py - check requirements in manifest.json")
        raise ConfigEntryNotReady("Required nr7101 library is not installed or failed to import")

    host = entry.data[CONF_HOST]
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]

    try:
        # Create router instance (login will be done by first coordinator update)
        router = await hass.async_add_executor_job(
            nr7101.NR7101,
            host,
            username,
            password)

        # Quick validation that we can at least connect to the router
        # (actual data fetching will be done by the coordinator)
        _LOGGER.info("Router instance created successfully")

    except Exception as ex:
        _LOGGER.error("Could not connect to Zyxel router: %s", ex)
        raise ConfigEntryNotReady from ex

    async def async_update_data():
        """Fetch data from the router."""
        try:
            async with async_timeout.timeout(15):
                # Put all blocking operations in a single executor job
                def get_all_data():
                    try:
                        # Login first to ensure valid session
                        login_success = router.login()
                        if not login_success:
                            _LOGGER.error("Login failed during data update")
                            raise UpdateFailed("Login failed during data update")

                        data = router.get_status()
                        if not data:
                            _LOGGER.error("No data received from router")
                            raise UpdateFailed("No data received from router")

                        # Use device info from get_status() if available, otherwise fetch it separately
                        if "device" not in data or not data["device"]:
                            data["device_info"] = router.get_json_object("status")
                        else:
                            data["device_info"] = data["device"]

                        # Keep session active - don't logout on every update
                        return data

                    except Exception as e:
                        _LOGGER.error(f"Error updating router data: {e}")
                        raise

                return await hass.async_add_executor_job(get_all_data)
        except asyncio.TimeoutError:
            _LOGGER.warning("Router data fetch timed out after 15 seconds")
            raise UpdateFailed("Router data fetch timed out")
        except Exception as err:
            _LOGGER.error(f"Error communicating with router: {err}")
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
