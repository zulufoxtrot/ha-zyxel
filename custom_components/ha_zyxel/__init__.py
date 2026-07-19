"""The Zyxel integration."""
import asyncio
import logging
from datetime import timedelta

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

from nr7101 import nr7101

PLATFORMS = ["sensor", "button", "device_tracker", "binary_sensor"]

# Bound every HTTP call. nr7101 sets no request timeouts, so a single hung
# request would otherwise pile up worker threads that share the router's one
# session and desync its AES key (shows up as "decrypt" errors that never heal).
REQUEST_TIMEOUT = 15


def _new_router(host, username, password):
    """Create an nr7101 client with a per-request timeout applied to every call."""
    return nr7101.NR7101(host, username, password, {"timeout": REQUEST_TIMEOUT})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Zyxel integration from a config entry."""
    host = entry.data[CONF_HOST]
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]

    try:
        router = await hass.async_add_executor_job(_new_router, host, username, password)
    except Exception as ex:
        _LOGGER.error("Could not connect to Zyxel router: %s", ex)
        raise ConfigEntryNotReady from ex

    state = {"router": router}
    scan_interval = entry.options.get("scan_interval", DEFAULT_SCAN_INTERVAL)

    def _fetch():
        """Fetch router data; on failure recreate the session once and retry (self-heal)."""
        client = state["router"]
        try:
            data = client.get_status()
        except Exception as err:  # noqa: BLE001 - desync/timeout/etc.
            _LOGGER.debug("Zyxel fetch failed, recreating session: %s", err)
            data = None
        if not data:
            client = _new_router(host, username, password)
            state["router"] = client
            data = client.get_status()
        if not data:
            raise UpdateFailed("No data received from router")
        if "device" not in data or not data["device"]:
            try:
                device_info = client.get_json_object("status")
                if device_info:
                    data["device_info"] = device_info
            except Exception:  # noqa: BLE001 - optional extra info
                pass
        return data

    async def async_update_data():
        # No asyncio timeout wrapper: each HTTP call is bounded by REQUEST_TIMEOUT
        # and DataUpdateCoordinator serialises refreshes, so the executor job always
        # finishes before the next one starts -> no overlapping threads / session desync.
        try:
            return await hass.async_add_executor_job(_fetch)
        except UpdateFailed:
            raise
        except Exception as err:
            raise UpdateFailed(f"Error communicating with router: {err}") from err

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
        "router": router,
        "state": state,
    }

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options change (scan interval, etc.)."""
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
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
