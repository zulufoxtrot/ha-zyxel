"""Support for Zyxel device sensors."""
from __future__ import annotations

import logging
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
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.ha_zyxel.const import DOMAIN
from custom_components.ha_zyxel.helpers import lan_hosts

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
}


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


def _is_value_scalar(value: Any) -> bool:
    """Check if a value is a scalar (string, number, bool)."""
    return isinstance(value, (str, int, float, bool)) or value is None


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Zyxel sensors."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    # Router telemetry sensors (flattened from the status data), created once.
    sensors = []
    for key, value in _flatten_dict(coordinator.data or {}).items():
        if not _is_value_scalar(value):
            continue
        sensor_config = KNOWN_SENSORS.get(key.split(".")[-1], None)
        if sensor_config:
            sensors.append(ConfiguredZyxelSensor(coordinator, entry, key, sensor_config))
        else:
            sensors.append(GenericZyxelSensor(coordinator, entry, key))

    # Router-level primary sensor: number of connected clients.
    sensors.append(ZyxelConnectedClients(coordinator, entry))
    async_add_entities(sensors)

    # Per-client diagnostic sensors (signal / link rate), discovered dynamically.
    tracked: set[str] = set()

    @callback
    def _discover_clients() -> None:
        new = []
        for mac in lan_hosts(coordinator):
            if mac not in tracked:
                tracked.add(mac)
                new.extend(
                    ZyxelClientAttrSensor(coordinator, mac, spec)
                    for spec in CLIENT_SENSOR_SPECS
                )
        if new:
            async_add_entities(new)

    entry.async_on_unload(coordinator.async_add_listener(_discover_clients))
    _discover_clients()


class AbstractZyxelSensor(CoordinatorEntity, SensorEntity):
    """Base class for Zyxel device sensors."""

    # Auto-generated router telemetry: treat as diagnostic and keep the long tail
    # off by default. Curated (Configured) sensors re-enable themselves below.
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
    """Representation of a configured (curated) Zyxel sensor."""

    # Curated sensors are useful enough to stay enabled (still diagnostic).
    _attr_entity_registry_enabled_default = True

    def __init__(self, coordinator, entry: ConfigEntry, key: str, config: dict):
        """Initialize the sensor."""
        super().__init__(coordinator, entry, key)
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
    """Number of devices currently connected to the router."""

    _attr_icon = "mdi:lan-connect"
    _attr_name = "Connected devices"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry: ConfigEntry):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_connected_clients"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"Zyxel ({entry.data['host']})",
            manufacturer="Zyxel",
        )

    @property
    def native_value(self) -> int:
        return sum(1 for h in lan_hosts(self.coordinator).values() if h.get("Active"))


class _ZyxelClientSensor(CoordinatorEntity, SensorEntity):
    """Base for per-client diagnostic sensors (attached to the client's device)."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator, mac: str):
        super().__init__(coordinator)
        self._mac = mac
        host = lan_hosts(coordinator).get(mac, {})
        friendly = host.get("curHostName") or host.get("HostName") or mac
        self._attr_device_info = DeviceInfo(
            connections={(CONNECTION_NETWORK_MAC, mac)},
            default_name=friendly,
        )

    @property
    def _host(self) -> dict:
        return lan_hosts(self.coordinator).get(self._mac, {})


def _is_wifi(host: dict) -> bool:
    return "WiFi" in (host.get("Layer1Interface") or "")


def _nz(value):
    """Treat 0 / empty as no reading (avoids fake -0 dBm etc.)."""
    return value if value not in (None, 0, "") else None


def _wifi_only(fn):
    """Only return a value for Wi-Fi clients (None for wired)."""
    return lambda host: (fn(host) if _is_wifi(host) else None)


def _kbps_to_mbps(value):
    return round(value / 1000, 1) if isinstance(value, (int, float)) and value else None


# Per-client diagnostic sensors, one entity each. All disabled by default; enable
# the ones you want per device. "id" is stable — it forms the entity unique_id.
CLIENT_SENSOR_SPECS: list[dict] = [
    {"id": "rssi", "name": "Signal strength", "device_class": SensorDeviceClass.SIGNAL_STRENGTH,
     "unit": "dBm", "state_class": SensorStateClass.MEASUREMENT,
     "fn": _wifi_only(lambda h: _nz(h.get("X_ZYXEL_RSSI")))},
    {"id": "snr", "name": "Signal-to-noise ratio", "unit": "dB", "icon": "mdi:signal",
     "state_class": SensorStateClass.MEASUREMENT,
     "fn": _wifi_only(lambda h: _nz(h.get("X_ZYXEL_SNR")))},
    {"id": "signal_quality", "name": "Signal quality", "unit": "%", "icon": "mdi:signal",
     "state_class": SensorStateClass.MEASUREMENT,
     "fn": _wifi_only(lambda h: _nz(h.get("X_ZYXEL_SignalStrength")))},
    {"id": "link_rate", "name": "Link rate", "device_class": SensorDeviceClass.DATA_RATE,
     "unit": "Mbit/s", "state_class": SensorStateClass.MEASUREMENT,
     "fn": lambda h: h.get("X_ZYXEL_PhyRate")},
    {"id": "downlink_rate", "name": "Downlink rate", "device_class": SensorDeviceClass.DATA_RATE,
     "unit": "Mbit/s", "state_class": SensorStateClass.MEASUREMENT,
     "fn": lambda h: _kbps_to_mbps(h.get("X_ZYXEL_LastDataDownlinkRate"))},
    {"id": "uplink_rate", "name": "Uplink rate", "device_class": SensorDeviceClass.DATA_RATE,
     "unit": "Mbit/s", "state_class": SensorStateClass.MEASUREMENT,
     "fn": lambda h: _kbps_to_mbps(h.get("X_ZYXEL_LastDataUplinkRate"))},
    {"id": "bytes_received", "name": "Bytes received", "device_class": SensorDeviceClass.DATA_SIZE,
     "unit": "B", "state_class": SensorStateClass.TOTAL_INCREASING,
     "fn": lambda h: h.get("X_ZYXEL_BytesReceived")},
    {"id": "bytes_sent", "name": "Bytes sent", "device_class": SensorDeviceClass.DATA_SIZE,
     "unit": "B", "state_class": SensorStateClass.TOTAL_INCREASING,
     "fn": lambda h: h.get("X_ZYXEL_BytesSent")},
    {"id": "connected_duration", "name": "Connected duration",
     "device_class": SensorDeviceClass.DURATION, "unit": "s",
     "state_class": SensorStateClass.MEASUREMENT,
     "fn": lambda h: h.get("X_ZYXEL_Duration")},
    {"id": "ip_address", "name": "IP address", "icon": "mdi:ip-network",
     "fn": lambda h: h.get("IPAddress") or None},
    {"id": "ssid", "name": "SSID", "icon": "mdi:wifi",
     "fn": _wifi_only(lambda h: h.get("WiFiname") or None)},
    {"id": "band", "name": "Band", "icon": "mdi:wifi",
     "fn": _wifi_only(lambda h: h.get("SupportedFrequencyBands") or None)},
    {"id": "network", "name": "Network", "icon": "mdi:wifi-cog",
     "fn": _wifi_only(lambda h: ("main" if h.get("X_ZYXEL_MainSSID") else "guest")
                      if "X_ZYXEL_MainSSID" in h else None)},
    {"id": "wifi_standard", "name": "Wi-Fi standard", "icon": "mdi:wifi",
     "fn": _wifi_only(lambda h: h.get("X_ZYXEL_OperatingStandard") or None)},
    {"id": "connection_type", "name": "Connection type", "icon": "mdi:lan",
     "fn": lambda h: ("wifi" if _is_wifi(h) else ("ethernet" if h.get("Layer1Interface") else None))},
    {"id": "device_type", "name": "Device type", "icon": "mdi:devices",
     "fn": lambda h: h.get("X_ZYXEL_HostType") or None},
    {"id": "address_source", "name": "Address source", "icon": "mdi:ip",
     "fn": lambda h: h.get("AddressSource") or None},
]


class ZyxelClientAttrSensor(_ZyxelClientSensor):
    """One diagnostic value for a client, driven by a CLIENT_SENSOR_SPECS entry."""

    def __init__(self, coordinator, mac: str, spec: dict):
        super().__init__(coordinator, mac)
        self._value_fn = spec["fn"]
        self._attr_unique_id = f"{mac}_{spec['id']}"
        self._attr_name = spec["name"]
        if spec.get("device_class"):
            self._attr_device_class = spec["device_class"]
        if spec.get("unit"):
            self._attr_native_unit_of_measurement = spec["unit"]
        if spec.get("state_class"):
            self._attr_state_class = spec["state_class"]
        if spec.get("icon"):
            self._attr_icon = spec["icon"]

    @property
    def native_value(self):
        try:
            return self._value_fn(self._host)
        except Exception:  # noqa: BLE001 - one bad client must not break the platform
            return None
