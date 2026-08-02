"""The Zyxel integration."""
import asyncio
import logging
from datetime import timedelta

from nr7101 import nr7101

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from custom_components.ha_zyxel.const import (
    CONF_SCAN_INTERVAL,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    DEFAULT_REQUEST_TIMEOUT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    ERROR_BACKOFF_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

# Block excessive nr7101 debug logging
nr7101_logger = logging.getLogger("nr7101.nr7101")
nr7101_logger.setLevel(logging.WARNING)

PLATFORMS = ["sensor", "button", "device_tracker", "binary_sensor"]


def _create_router(host: str, username: str, password: str):
    """Create a router client with bounded HTTP requests."""
    return nr7101.NR7101(
        host,
        username,
        password,
        {"timeout": DEFAULT_REQUEST_TIMEOUT},
    )


def _logout_router(router) -> None:
    """Close the router session when one was established."""
    if router.sessionkey is None:
        return
    try:
        router.logout()
    except Exception as err:
        _LOGGER.debug("Could not close Zyxel router session: %s", err)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Zyxel integration from a config entry."""
    host = entry.data[CONF_HOST]
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]

    try:
        router = await hass.async_add_executor_job(
            _create_router,
            host,
            username,
            password,
        )
    except Exception as ex:
        _LOGGER.error("Could not connect to Zyxel router: %s", ex)
        raise ConfigEntryNotReady from ex

    router_holder = {"router": router}
    router_lock = asyncio.Lock()
    scan_interval = entry.options.get(
        CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
    )

    def set_update_interval(seconds: int) -> None:
        coordinator.update_interval = timedelta(seconds=seconds)

    async def async_update_data():
        """Fetch data from the router."""
        try:
            def get_all_data():
                current_router = router_holder["router"]
                try:
                    data = current_router.get_status()
                except Exception as err:
                    _LOGGER.debug(
                        "Router fetch failed; recreating session: %s", err
                    )
                    data = None

                if not data:
                    _logout_router(current_router)
                    current_router = _create_router(host, username, password)
                    router_holder["router"] = current_router
                    data = current_router.get_status()

                if not data:
                    raise UpdateFailed("No data received from router")

                # Get device info if not already in data
                if "device" not in data or not data["device"]:
                    try:
                        device_info = current_router.get_json_object("status")
                        if device_info:
                            data["device_info"] = device_info
                    except Exception:
                        pass

                return data

            async with router_lock:
                data = await hass.async_add_executor_job(get_all_data)
            set_update_interval(scan_interval)
            return data
        except Exception as err:
            set_update_interval(max(scan_interval, ERROR_BACKOFF_INTERVAL))
            raise UpdateFailed(
                f"Error communicating with router: {err}"
            ) from err

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=DOMAIN,
        update_method=async_update_data,
        update_interval=timedelta(seconds=scan_interval),
    )

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "router_holder": router_holder,
        "router_lock": router_lock,
    }

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def _async_update_listener(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)


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
        entry_data = hass.data[DOMAIN][entry.entry_id]
        router_holder = entry_data["router_holder"]
        async with entry_data["router_lock"]:
            await hass.async_add_executor_job(
                _logout_router, router_holder["router"]
            )
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
