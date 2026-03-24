"""Support for Zyxel device buttons."""
from __future__ import annotations

import logging
from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import *
from .coordinator import ZyxelDataUpdateCoordinator
from .entity import ZyxelBaseEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Zyxel buttons."""
    coordinator: ZyxelDataUpdateCoordinator = entry.runtime_data
    async_add_entities([ZyxelRebootButton(coordinator)])


class ZyxelRebootButton(ZyxelBaseEntity, ButtonEntity):
    """Representation of a Zyxel reboot button."""

    def __init__(self, coordinator):
        """Initialize the button."""
        super().__init__(coordinator, "reboot", None)
        self._attr_icon = "mdi:restart"
        self._attr_name = "Reboot Device"

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.device_available

    async def async_press(self) -> None:
        """Handle the button press."""
        _LOGGER.info("Attempting to reboot Zyxel device")
        try:
            await self.router.reboot()
            _LOGGER.info("Zyxel device reboot command sent successfully")
        except Exception as err:
            _LOGGER.error("Failed to send reboot command: %s", err)

