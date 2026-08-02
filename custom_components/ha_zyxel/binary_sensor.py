"""Connectivity sensors for Zyxel LAN clients."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.ha_zyxel.const import (
    CONF_CLIENT_DIAGNOSTICS,
    DEFAULT_CLIENT_DIAGNOSTICS,
    DOMAIN,
)
from custom_components.ha_zyxel.helpers import lan_hosts, select_unique_fields

ROUTER_BINARY_SENSORS = (
    ("INTF_Status", "Cellular connection", lambda value: value == "Up"),
    ("USIM_Status", "SIM ready", lambda value: value == "DEVST_SIM_RDY"),
    (
        "CELL_Roaming_Enable",
        "Roaming",
        lambda value: value in (True, 1, "1", "true", "True"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create connectivity sensors for discovered LAN clients."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    fields = select_unique_fields(
        coordinator.data or {},
        {spec[0] for spec in ROUTER_BINARY_SENSORS},
    )
    router_entities = [
        ZyxelRouterBinarySensor(
            coordinator,
            entry,
            field_name,
            fields[field_name][0],
            name,
            is_on,
        )
        for field_name, name, is_on in ROUTER_BINARY_SENSORS
        if field_name in fields
    ]
    async_add_entities(router_entities)

    if not entry.options.get(
        CONF_CLIENT_DIAGNOSTICS, DEFAULT_CLIENT_DIAGNOSTICS
    ):
        intended_unique_ids = {
            entity.unique_id
            for entity in router_entities
            if entity.unique_id is not None
        }
        registry = er.async_get(hass)
        for registry_entry in er.async_entries_for_config_entry(
            registry, entry.entry_id
        ):
            if (
                registry_entry.entity_id.startswith("binary_sensor.")
                and registry_entry.unique_id not in intended_unique_ids
            ):
                registry.async_remove(registry_entry.entity_id)
        return

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


class ZyxelRouterBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Represent one router health state."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator,
        entry: ConfigEntry,
        field_name: str,
        path: str,
        name: str,
        is_on,
    ) -> None:
        """Initialize a router health sensor."""
        super().__init__(coordinator)
        self._path = path
        self._is_on = is_on
        self._attr_unique_id = f"{entry.entry_id}_{field_name.lower()}"
        self._attr_name = name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"Zyxel ({entry.data['host']})",
            manufacturer="Zyxel",
        )

    def _value(self):
        value = self.coordinator.data
        for key in self._path.split("."):
            value = value[key]
        return value

    @property
    def available(self) -> bool:
        """Return whether the router health field is available."""
        if not self.coordinator.last_update_success:
            return False
        try:
            self._value()
        except (KeyError, TypeError):
            return False
        return True

    @property
    def is_on(self) -> bool:
        """Return the mapped router state."""
        try:
            return self._is_on(self._value())
        except (KeyError, TypeError):
            return False


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
