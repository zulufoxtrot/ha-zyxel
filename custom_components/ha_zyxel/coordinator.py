"""AMC alarm integration."""
import asyncio
import logging
import async_timeout
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL, CONF_TIMEOUT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady, ConfigEntryError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.typing import ConfigType
from .const import *

from .nr7101.nr7101 import NR7101

_LOGGER = logging.getLogger(__name__)

class ZyxelDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the API."""

    router: NR7101 | None = None
    config: ConfigType | None = None
    
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize."""
        self.hass = hass
        self.entry = entry
        self.config = (entry.data or {}).copy()
        self._device_info = None  # sarà creato solo la prima volta        
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL))
        self.router = NR7101(self.config[CONF_HOST], self.config[CONF_USERNAME], self.config[CONF_PASSWORD])
    
    @property
    def device_available(self):
        return self.last_update_success

    @property
    def device_info(self) -> DeviceInfo:
        if self._device_info is None:
            if not self.data:
                return DeviceInfo(
                    identifiers={(DOMAIN, self.entry.entry_id)},
                    manufacturer="Zyxel",
                    name=f"Zyxel ({self.entry.data['host']})",
                    configuration_url=self.entry.data['host']
                )
            # Creo DeviceInfo solo la prima volta
            self._device_info = DeviceInfo(
                identifiers={(DOMAIN, self.entry.entry_id)},                
                manufacturer="Zyxel",
                name=f"Zyxel {self.data.get("device.DeviceInfo.ModelName", "")}",
                model=self.data.get("device.DeviceInfo.ModelName", ""),
                sw_version=self.data.get("device.DeviceInfo.SoftwareVersion", ""),
                hw_version=self.data.get("device.DeviceInfo.HardwareVersion", ""),
                serial_number=self.data.get("device.DeviceInfo.SerialNumber", ""),
                configuration_url=self.entry.data['host']
            )
        return self._device_info

    def get_config(self, key, default=None):
        if not key in self.config:
            return default
        value = self.config[key]
        return value

    async def _async_update_data(self):
        router = self.router
        hass = self.hass
        
        if router.sessionkey is None:
            try:
                await router.login()
            except Exception as ex:
                _LOGGER.error("Could not connect to Zyxel router: %s" % ex)
                raise ConfigEntryNotReady from ex

        """Fetch data from the router."""
        try:
            async with async_timeout.timeout(15):
                data = await router.get_status()

                if not data:
                    raise UpdateFailed("No data received from router")
                router.last_status_data = data

                # Get device info if not already in data
                if "device" not in data or not data["device"]:
                    device_info = await router.get_json_object("status")
                    if device_info:
                        data["device"] = device_info
                    else:
                        raise UpdateFailed("No device data received from router")
                
                #for get device as first
                new_data = { "device": data["device"] }
                new_data.update(data)

                flat_data = _flatten_dict(new_data)

                return flat_data
        except asyncio.TimeoutError:
            router._session_valid = False
            raise UpdateFailed("Router data fetch timed out")
        except Exception as err:
            router._session_valid = False
            raise UpdateFailed(f"Error communicating with router: {err}") from err


def _flatten_dict(d: dict, parent_key: str = "") -> dict:
    """Flatten a nested dictionary with dot notation for keys."""
    items = []
    for k, v in d.items():
        new_key = f"{parent_key}.{k}" if parent_key else k
        if isinstance(v, dict):
            items.extend(_flatten_dict(v, new_key).items())
        else:
            items.append((new_key, v))
    return dict(items)