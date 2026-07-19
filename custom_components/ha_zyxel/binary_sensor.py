"""Connectivity binary sensor for Zyxel LAN hosts (instant online/offline).

Unlike the device_tracker (which applies a consider-home grace period), this
reports the router's raw association state immediately - handy for automations
that want to react the moment a device drops off or joins the network.
"""
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
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Create a connectivity sensor per LAN host, picking up new ones over time."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    tracked: set[str] = set()

    @callback
    def _discover() -> None:
        new = [
            ZyxelConnectivitySensor(coordinator, mac)
            for mac in lan_hosts(coordinator)
            if mac not in tracked
        ]
        for ent in new:
            tracked.add(ent.mac)
        if new:
            async_add_entities(new)

    entry.async_on_unload(coordinator.async_add_listener(_discover))
    _discover()


class ZyxelConnectivitySensor(CoordinatorEntity, BinarySensorEntity):
    """Instant online/offline for one LAN host (no consider-home debounce)."""

    _attr_has_entity_name = True
    _attr_name = "Connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    # Secondary/opt-in entity so it doesn't double the entity count by default.
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator, mac: str) -> None:
        """Initialise the connectivity sensor for one MAC address."""
        super().__init__(coordinator)
        self.mac = mac
        self._attr_unique_id = f"{mac}_connectivity"
        host = lan_hosts(coordinator).get(mac, {})
        friendly = host.get("curHostName") or host.get("HostName") or mac
        self._attr_device_info = DeviceInfo(
            connections={(CONNECTION_NETWORK_MAC, mac)},
            default_name=friendly,
        )

    @property
    def is_on(self) -> bool:
        """True while the router reports the device as currently active."""
        return bool(lan_hosts(self.coordinator).get(self.mac, {}).get("Active"))
