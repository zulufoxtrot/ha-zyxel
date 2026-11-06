"""Support for Zyxel device buttons."""
from __future__ import annotations

import logging
from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .api import reboot_router
from .const import DOMAIN
from .device_info import build_device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Zyxel buttons."""
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [ZyxelRebootButton(entry, data["router"], data["coordinator"].data)]
    )


class ZyxelRebootButton(ButtonEntity):
    """Representation of a Zyxel reboot button."""

    def __init__(self, entry: ConfigEntry, router, data=None) -> None:
        """Initialize the button."""
        self._router = router
        self._attr_unique_id = f"{entry.entry_id}_reboot"
        self._attr_device_info = build_device_info(entry, data)
        self._attr_icon = "mdi:restart"
        self._attr_has_entity_name = True
        self._attr_name = "Reboot Device"

    def _reboot(self) -> None:
        reboot_router(self._router)

    async def async_press(self) -> None:
        """Handle the button press."""
        _LOGGER.info("Attempting to reboot Zyxel device")
        try:
            await self.hass.async_add_executor_job(self._reboot)
            _LOGGER.info("Zyxel device reboot command sent successfully")
        except Exception as err:
            _LOGGER.error("Failed to send reboot command: %s", err)
