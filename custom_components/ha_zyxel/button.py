"""Support for Zyxel device buttons."""
from __future__ import annotations

import logging
from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Zyxel buttons."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            ZyxelRebootButton(
                entry,
                entry_data["router_holder"],
                entry_data["router_lock"],
            )
        ]
    )


class ZyxelRebootButton(ButtonEntity):
    """Representation of a Zyxel reboot button."""

    def __init__(self, entry: ConfigEntry, router_holder, router_lock) -> None:
        """Initialize the button."""
        self._router_holder = router_holder
        self._router_lock = router_lock
        self._attr_unique_id = f"{entry.entry_id}_reboot"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"Zyxel ({entry.data['host']})",
            manufacturer="Zyxel",
            model="",
        )
        self._attr_icon = "mdi:restart"
        self._attr_name = "Zyxel Reboot Device"

    async def async_press(self) -> None:
        """Handle the button press."""
        _LOGGER.info("Attempting to reboot Zyxel device")
        try:
            async with self._router_lock:
                await self.hass.async_add_executor_job(
                    self._router_holder["router"].reboot
                )
            _LOGGER.info("Zyxel device reboot command sent successfully")
        except Exception as err:
            _LOGGER.error("Failed to send reboot command: %s", err)
