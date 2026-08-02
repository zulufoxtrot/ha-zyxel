"""Support for Zyxel device sensors."""
from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
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
    CONF_EXPOSE_ALL_ROUTER_SENSORS,
    DEFAULT_CLIENT_DIAGNOSTICS,
    DEFAULT_EXPOSE_ALL_ROUTER_SENSORS,
    DOMAIN,
)
from custom_components.ha_zyxel.helpers import (
    flattened_scalars,
    lan_hosts,
    select_unique_fields,
)

_LOGGER = logging.getLogger(__name__)

# Define some known sensor types for proper configuration
KNOWN_SENSORS = {
    "INTF_RSSI": {
        "name": "Cellular RSSI",
        "unit": "dBm",
        "icon": "mdi:signal",
        "device_class": SensorDeviceClass.SIGNAL_STRENGTH,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    "INTF_PhyCell_ID": {
        "name": "Physical Cell ID",
        "unit": None,
        "icon": "mdi:antenna",
        "device_class": None,
        "state_class": None,
    },
    "INTF_RSRP": {
        "name": "Cellular Reference Signal Received Power",
        "unit": "dBm",
        "icon": "mdi:signal",
        "device_class": SensorDeviceClass.SIGNAL_STRENGTH,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    "INTF_RSRQ": {
        "name": "Cellular Reference Signal Received Quality",
        "unit": "dB",
        "icon": "mdi:signal",
        "device_class": SensorDeviceClass.SIGNAL_STRENGTH,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    "INTF_SINR": {
        "name": "Cellular Signal-to-Noise Ratio",
        "unit": "dB",
        "icon": "mdi:signal",
        "device_class": SensorDeviceClass.SIGNAL_STRENGTH,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    "INTF_MCS": {
        "name": "Cellular Modulation and Coding Scheme",
        "unit": "",
        "icon": "mdi:signal",
        "device_class": None,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    "INTF_CQI": {
        "name": "Cellular Channel Quality Indicator",
        "unit": "",
        "icon": "mdi:signal",
        "device_class": None,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    "INTF_RI": {
        "name": "Cellular Rank Indicator",
        "unit": "",
        "icon": "mdi:signal",
        "device_class": None,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    "INTF_PMI": {
        "name": "Cellular Precoding Matrix Indicator",
        "unit": "",
        "icon": "mdi:signal",
        "device_class": None,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    "NSA_PhyCellID": {
        "name": "NSA Physical Cell ID",
        "unit": None,
        "icon": "mdi:antenna",
        "device_class": None,
        "state_class": None,
    },
    "NSA_RSRP": {
        "name": "NSA Reference Signal Received Power",
        "unit": "dBm",
        "icon": "mdi:signal",
        "device_class": SensorDeviceClass.SIGNAL_STRENGTH,
        "state_class": SensorStateClass.MEASUREMENT
    },
    "NSA_RSRQ": {
        "name": "NSA Reference Signal Received Quality",
        "unit": "dB",
        "icon": "mdi:signal",
        "device_class": SensorDeviceClass.SIGNAL_STRENGTH,
        "state_class": SensorStateClass.MEASUREMENT
    },
    "NSA_RSSI": {
        "name": "NSA Reference Signal Strength Indicator",
        "unit": "dBm",
        "icon": "mdi:signal",
        "device_class": SensorDeviceClass.SIGNAL_STRENGTH,
        "state_class": SensorStateClass.MEASUREMENT
    },
    "NSA_SINR": {
        "name": "NSA Signal-to-Noise Ratio",
        "unit": "dB",
        "icon": "mdi:signal",
        "device_class": SensorDeviceClass.SIGNAL_STRENGTH,
        "state_class": SensorStateClass.MEASUREMENT
    },
    "X_ZYXEL_TEMPERATURE_AMBIENT": {
        "name": "Ambient Temperature",
        "unit": "°C",
        "icon": "mdi:thermometer",
        "device_class": SensorDeviceClass.TEMPERATURE,
        "state_class": SensorStateClass.MEASUREMENT
    },
    "X_ZYXEL_TEMPERATURE_SDX": {
        "name": "SDX Temperature",
        "unit": "°C",
        "icon": "mdi:thermometer",
        "device_class": SensorDeviceClass.TEMPERATURE,
        "state_class": SensorStateClass.MEASUREMENT
    },
    "X_ZYXEL_TEMPERATURE_CPU0": {
        "name": "CPU Temperature",
        "unit": "°C",
        "icon": "mdi:thermometer",
        "device_class": SensorDeviceClass.TEMPERATURE,
        "state_class": SensorStateClass.MEASUREMENT
    },
    "BytesSent": {
        "name": "Bytes Sent",
        "unit": "B",
        "icon": "mdi:numeric-10-box",
        "device_class": SensorDeviceClass.DATA_SIZE,
        "state_class": SensorStateClass.TOTAL_INCREASING,
    },
    "BytesReceived": {
        "name": "Bytes Received",
        "unit": "B",
        "icon": "mdi:numeric-10-box",
        "device_class": SensorDeviceClass.DATA_SIZE,
        "state_class": SensorStateClass.TOTAL_INCREASING,
    },
    "UpTime": {
        "name": "Router uptime",
        "unit": "s",
        "icon": "mdi:timer-outline",
        "device_class": SensorDeviceClass.DURATION,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    "INTF_Current_Access_Technology": {
        "name": "Access technology",
        "unit": None,
        "icon": "mdi:access-point-network",
        "device_class": None,
        "state_class": None,
    },
    "INTF_Network_In_Use": {
        "name": "Mobile network",
        "unit": None,
        "icon": "mdi:radio-tower",
        "device_class": None,
        "state_class": None,
    },
    "INTF_Current_Band": {
        "name": "LTE band",
        "unit": None,
        "icon": "mdi:signal-4g",
        "device_class": None,
        "state_class": None,
    },
    "NSA_Band": {
        "name": "5G NSA band",
        "unit": None,
        "icon": "mdi:signal-5g",
        "device_class": None,
        "state_class": None,
    },
    "INTF_CA_COMBINATION": {
        "name": "Carrier aggregation",
        "unit": None,
        "icon": "mdi:signal-variant",
        "device_class": None,
        "state_class": None,
    },
    "INTF_Cell_ID": {
        "name": "Cell ID",
        "unit": None,
        "icon": "mdi:radio-tower",
        "device_class": None,
        "state_class": None,
    },
    "INTF_SiteID": {
        "name": "Site ID",
        "unit": None,
        "icon": "mdi:radio-tower",
        "device_class": None,
        "state_class": None,
    },
    "INTF_TAC": {
        "name": "Tracking area code",
        "unit": None,
        "icon": "mdi:map-marker-radius",
        "device_class": None,
        "state_class": None,
    },
}

_CLIENT_SENSOR_UNIQUE_ID = re.compile(
    r"^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}_"
)


def _remove_unexposed_sensor_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    intended_unique_ids: set[str],
    expose_all: bool,
    client_diagnostics: bool,
) -> None:
    """Remove registry entries no longer exposed by the entity modes."""
    registry = er.async_get(hass)
    prefix = f"{entry.entry_id}_"
    for registry_entry in er.async_entries_for_config_entry(
        registry, entry.entry_id
    ):
        if not registry_entry.entity_id.startswith("sensor."):
            continue
        unique_id = registry_entry.unique_id
        if (
            not unique_id.startswith(prefix)
            or unique_id in intended_unique_ids
        ):
            continue
        suffix = unique_id.removeprefix(prefix)
        is_client_sensor = _CLIENT_SENSOR_UNIQUE_ID.match(suffix) is not None
        if (is_client_sensor and client_diagnostics) or (
            not is_client_sensor and expose_all
        ):
            continue
        registry.async_remove(registry_entry.entity_id)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Zyxel sensors."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    expose_all = entry.options.get(
        CONF_EXPOSE_ALL_ROUTER_SENSORS,
        DEFAULT_EXPOSE_ALL_ROUTER_SENSORS,
    )
    client_diagnostics = entry.options.get(
        CONF_CLIENT_DIAGNOSTICS, DEFAULT_CLIENT_DIAGNOSTICS
    )
    flattened = flattened_scalars(coordinator.data or {})
    selected = select_unique_fields(
        coordinator.data or {}, set(KNOWN_SENSORS)
    )

    sensors = [
        ConfiguredZyxelSensor(
            coordinator,
            entry,
            path,
            KNOWN_SENSORS[field_name],
            field_name,
        )
        for field_name, (path, _value) in selected.items()
    ]
    if expose_all:
        sensors.extend(
            GenericZyxelSensor(coordinator, entry, path)
            for path in flattened
            if path.rsplit(".", 1)[-1] not in KNOWN_SENSORS
        )

    sensors.append(ZyxelConnectedClients(coordinator, entry))
    intended_unique_ids = {
        sensor.unique_id for sensor in sensors if sensor.unique_id is not None
    }
    _remove_unexposed_sensor_entities(
        hass,
        entry,
        intended_unique_ids,
        expose_all,
        client_diagnostics,
    )
    async_add_entities(sensors)

    if not client_diagnostics:
        return

    tracked: set[tuple[str, str]] = set()

    @callback
    def discover_clients() -> None:
        new_entities = []
        for mac, host in lan_hosts(coordinator).items():
            for spec in CLIENT_SENSOR_SPECS:
                entity_key = (mac, spec["id"])
                if entity_key in tracked:
                    continue
                try:
                    value = spec["value"](host)
                except (TypeError, ValueError):
                    value = None
                if value is None:
                    continue
                tracked.add(entity_key)
                new_entities.append(
                    ZyxelClientSensor(
                        coordinator, entry.entry_id, mac, spec
                    )
                )
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(discover_clients))
    discover_clients()


class AbstractZyxelSensor(CoordinatorEntity, SensorEntity):
    """Base class for Zyxel device sensors."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator, entry: ConfigEntry, key: str):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"Zyxel ({entry.data['host']})",
            manufacturer="Zyxel",
            model="",
        )

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        if not self.coordinator.last_update_success:
            return False

        # Check if the key exists in the data
        try:
            self._get_value_from_path()
            return True
        except (KeyError, AttributeError):
            return False

    def _get_value_from_path(self) -> Any:
        """Get a value from nested dictionaries using the flattened key."""
        keys = self._key.split(".")
        value = self.coordinator.data
        for k in keys:
            value = value[k]
        return value


class ConfiguredZyxelSensor(AbstractZyxelSensor):
    """Representation of a configured Zyxel sensor."""

    _attr_entity_registry_enabled_default = True

    def __init__(
        self,
        coordinator,
        entry: ConfigEntry,
        key: str,
        config: dict,
        field_name: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry, key)
        self._attr_unique_id = f"{entry.entry_id}_{field_name}"
        self._config = config
        self._attr_name = f"Zyxel {config['name']}"
        self._attr_native_unit_of_measurement = config["unit"]
        self._attr_icon = config["icon"]
        self._attr_device_class = config["device_class"]
        self._attr_state_class = config["state_class"]

    @property
    def state(self):
        """Return the state of the sensor."""
        try:
            return self._get_value_from_path()
        except (KeyError, AttributeError):
            return None


class GenericZyxelSensor(AbstractZyxelSensor):
    """Representation of a generic Zyxel sensor."""

    @property
    def name(self):
        """Return the name of the sensor."""
        name_parts = self._key.split(".")
        return f"Zyxel {'.'.join(name_parts)}"

    @property
    def state(self):
        """Return the state of the sensor."""
        try:
            return self._get_value_from_path()
        except (KeyError, AttributeError):
            return None

    @property
    def icon(self):
        """Return the icon."""
        return "mdi:router-wireless"


class ZyxelConnectedClients(CoordinatorEntity, SensorEntity):
    """Represent the number of clients currently connected."""

    _attr_icon = "mdi:lan-connect"
    _attr_name = "Connected devices"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        """Initialize the connected-client sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_connected_clients"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"Zyxel ({entry.data['host']})",
            manufacturer="Zyxel",
        )

    @property
    def native_value(self) -> int:
        """Return the active LAN client count."""
        return sum(
            1
            for host in lan_hosts(self.coordinator).values()
            if host.get("Active")
        )


def _is_wifi(host: dict) -> bool:
    return "WiFi" in (host.get("Layer1Interface") or "")


def _nonzero(value):
    return value if value not in (None, 0, "") else None


def _wifi_only(value_fn):
    return lambda host: value_fn(host) if _is_wifi(host) else None


def _kbps_to_mbps(value):
    if not isinstance(value, (int, float)) or not value:
        return None
    return round(value / 1000, 1)


CLIENT_SENSOR_SPECS = (
    {
        "id": "rssi",
        "name": "Signal strength",
        "device_class": SensorDeviceClass.SIGNAL_STRENGTH,
        "unit": "dBm",
        "state_class": SensorStateClass.MEASUREMENT,
        "value": _wifi_only(lambda host: _nonzero(host.get("X_ZYXEL_RSSI"))),
    },
    {
        "id": "snr",
        "name": "Signal-to-noise ratio",
        "unit": "dB",
        "icon": "mdi:signal",
        "state_class": SensorStateClass.MEASUREMENT,
        "value": _wifi_only(lambda host: _nonzero(host.get("X_ZYXEL_SNR"))),
    },
    {
        "id": "signal_quality",
        "name": "Signal quality",
        "unit": "%",
        "icon": "mdi:signal",
        "state_class": SensorStateClass.MEASUREMENT,
        "value": _wifi_only(
            lambda host: _nonzero(host.get("X_ZYXEL_SignalStrength"))
        ),
    },
    {
        "id": "link_rate",
        "name": "Link rate",
        "device_class": SensorDeviceClass.DATA_RATE,
        "unit": "Mbit/s",
        "state_class": SensorStateClass.MEASUREMENT,
        "value": lambda host: host.get("X_ZYXEL_PhyRate"),
    },
    {
        "id": "downlink_rate",
        "name": "Downlink rate",
        "device_class": SensorDeviceClass.DATA_RATE,
        "unit": "Mbit/s",
        "state_class": SensorStateClass.MEASUREMENT,
        "value": lambda host: _kbps_to_mbps(
            host.get("X_ZYXEL_LastDataDownlinkRate")
        ),
    },
    {
        "id": "uplink_rate",
        "name": "Uplink rate",
        "device_class": SensorDeviceClass.DATA_RATE,
        "unit": "Mbit/s",
        "state_class": SensorStateClass.MEASUREMENT,
        "value": lambda host: _kbps_to_mbps(
            host.get("X_ZYXEL_LastDataUplinkRate")
        ),
    },
    {
        "id": "bytes_received",
        "name": "Bytes received",
        "device_class": SensorDeviceClass.DATA_SIZE,
        "unit": "B",
        "state_class": SensorStateClass.TOTAL_INCREASING,
        "value": lambda host: host.get("X_ZYXEL_BytesReceived"),
    },
    {
        "id": "bytes_sent",
        "name": "Bytes sent",
        "device_class": SensorDeviceClass.DATA_SIZE,
        "unit": "B",
        "state_class": SensorStateClass.TOTAL_INCREASING,
        "value": lambda host: host.get("X_ZYXEL_BytesSent"),
    },
    {
        "id": "connected_duration",
        "name": "Connected duration",
        "device_class": SensorDeviceClass.DURATION,
        "unit": "s",
        "state_class": SensorStateClass.MEASUREMENT,
        "value": lambda host: host.get("X_ZYXEL_Duration"),
    },
    {
        "id": "ip_address",
        "name": "IP address",
        "icon": "mdi:ip-network",
        "value": lambda host: host.get("IPAddress") or None,
    },
    {
        "id": "ssid",
        "name": "SSID",
        "icon": "mdi:wifi",
        "value": _wifi_only(lambda host: host.get("WiFiname") or None),
    },
    {
        "id": "band",
        "name": "Band",
        "icon": "mdi:wifi",
        "value": _wifi_only(
            lambda host: host.get("SupportedFrequencyBands") or None
        ),
    },
    {
        "id": "network",
        "name": "Network",
        "icon": "mdi:wifi-cog",
        "value": _wifi_only(
            lambda host: (
                "main" if host.get("X_ZYXEL_MainSSID") else "guest"
            )
            if "X_ZYXEL_MainSSID" in host
            else None
        ),
    },
    {
        "id": "wifi_standard",
        "name": "Wi-Fi standard",
        "icon": "mdi:wifi",
        "value": _wifi_only(
            lambda host: host.get("X_ZYXEL_OperatingStandard") or None
        ),
    },
    {
        "id": "connection_type",
        "name": "Connection type",
        "icon": "mdi:lan",
        "value": lambda host: (
            "wifi"
            if _is_wifi(host)
            else ("ethernet" if host.get("Layer1Interface") else None)
        ),
    },
    {
        "id": "device_type",
        "name": "Device type",
        "icon": "mdi:devices",
        "value": lambda host: host.get("X_ZYXEL_HostType") or None,
    },
    {
        "id": "address_source",
        "name": "Address source",
        "icon": "mdi:ip",
        "value": lambda host: host.get("AddressSource") or None,
    },
)


class ZyxelClientSensor(CoordinatorEntity, SensorEntity):
    """Represent one diagnostic value for a LAN client."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator,
        entry_id: str,
        mac: str,
        spec: dict,
    ) -> None:
        """Initialize a client diagnostic sensor."""
        super().__init__(coordinator)
        self._mac = mac
        self._value_fn = spec["value"]
        self._attr_unique_id = f"{entry_id}_{mac}_{spec['id']}"
        self._attr_name = spec["name"]
        self._attr_device_class = spec.get("device_class")
        self._attr_native_unit_of_measurement = spec.get("unit")
        self._attr_state_class = spec.get("state_class")
        self._attr_icon = spec.get("icon")

        host = lan_hosts(coordinator).get(mac, {})
        friendly_name = host.get("curHostName") or host.get("HostName") or mac
        self._attr_device_info = DeviceInfo(
            connections={(CONNECTION_NETWORK_MAC, mac)},
            default_name=friendly_name,
        )

    @property
    def native_value(self):
        """Return the current client diagnostic value."""
        host = lan_hosts(self.coordinator).get(self._mac, {})
        try:
            return self._value_fn(host)
        except (TypeError, ValueError):
            return None
