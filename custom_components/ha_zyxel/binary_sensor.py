"""Connectivity sensors for Zyxel LAN clients."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.ha_zyxel.const import DOMAIN
from custom_components.ha_zyxel.helpers import lan_hosts


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create connectivity sensors for discovered LAN clients."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    tracked: set[str] = set()

    @callback
    def discover() -> None:
        new_entities = [
            ZyxelConnectivitySensor(coordinator, entry.entry_id, mac)
            for mac in lan_hosts(coordinator)
            if mac not in tracked
        ]
        tracked.update(entity.mac for entity in new_entities)
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(discover))
    discover()


class ZyxelConnectivitySensor(CoordinatorEntity, BinarySensorEntity):
    """Represent a LAN client's immediate connectivity state."""

    _attr_has_entity_name = True
    _attr_name = "Connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator, entry_id: str, mac: str) -> None:
        """Initialize a connectivity sensor."""
        super().__init__(coordinator)
        self.mac = mac
        self._attr_unique_id = f"{entry_id}_{mac}_connectivity"
        host = lan_hosts(coordinator).get(mac, {})
        friendly_name = host.get("curHostName") or host.get("HostName") or mac
        self._attr_device_info = DeviceInfo(
            connections={(CONNECTION_NETWORK_MAC, mac)},
            default_name=friendly_name,
        )

    @property
    def is_on(self) -> bool:
        """Return whether the router reports the client as active."""
        host = lan_hosts(self.coordinator).get(self.mac, {})
        return bool(host.get("Active"))
