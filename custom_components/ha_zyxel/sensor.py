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
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.ha_zyxel.const import DOMAIN

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


# Top-level keys of the coordinator data, see api._ENDPOINTS.
SECTION_NAMES = {
    "cellular": "Cellular",
    "traffic": "Traffic",
    "cardpage": "Overview",
    "lan": "LAN",
    "lanhosts": "LAN hosts",
    "wifi_mesh": "Wi-Fi mesh",
    "one_connect": "OneConnect",
    "device": "Device",
}

# Generic sensors are created for every scalar the modem reports (hundreds,
# mostly static configuration). Only these stay enabled by default; the rest
# are created disabled and can be enabled from the entity registry. Everything
# in the "cellular" section is always enabled by default.
ENABLED_BY_DEFAULT = {
    "device.DeviceInfo.UpTime",
    "device.DeviceInfo.SoftwareVersion",
    "device.DeviceInfo.ModelName",
    "device.SystemInfo.CPUUsage",
    "device.SystemInfo.Free",
    "device.SystemInfo.Total",
    "device.WanRate_RX",
    "device.WanRate_TX",
    "device.WanType",
    "lanhosts.wanInfo.wanStatus",
}

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _humanize(token: str) -> str:
    """Turn a raw key such as 'X_ZYXEL_InterfaceUpTime' into 'Interface Up Time'."""
    token = re.sub(r"^X_(ZYXEL_)?", "", token)
    words = []
    for part in token.split("_"):
        words.extend(_CAMEL_BOUNDARY.sub(" ", part).split())
    return " ".join(words).replace("Wi Fi", "Wi-Fi")


def _readable_name(key: str, leaf: str | None = None) -> str:
    """Build a readable, unique name from a flattened key.

    ``traffic.br0.BytesReceived`` -> ``Traffic br0 Bytes Received``. ``leaf``
    replaces the last path element (used for known sensors with curated names).
    """
    section, *path = key.split(".")
    if leaf is not None:
        path = path[:-1]
    parts = [SECTION_NAMES.get(section, _humanize(section))]
    # Skip empty path elements (the modem reports one traffic entry with no
    # interface name), otherwise the name contains a double space.
    parts.extend(h for h in (_humanize(p) for p in path) if h)
    # "Device" + "Device Info ..." would read "Device Device Info ...".
    if len(parts) > 1 and parts[1].split(" ")[0] == parts[0]:
        parts = parts[:1] + [parts[1].removeprefix(parts[0]).strip()] + parts[2:]
    if leaf is not None:
        # Curated names such as "Cellular RSSI" already carry the section.
        if len(parts) == 1 and leaf.startswith(parts[0]):
            parts = []
        parts.append(leaf)
    return " ".join(parts)


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


def _is_duplicate(key: str, data: dict) -> bool:
    """Return True if an "Overview" value just repeats the "Device" section."""
    section, _, path = key.partition(".")
    if section != "cardpage":
        return False
    device = data.get("device")
    for part in path.split("."):
        if not isinstance(device, dict) or part not in device:
            return False
        device = device[part]
    return device == _get_path(data["cardpage"], path)


def _get_path(data: dict, path: str) -> Any:
    for part in path.split("."):
        data = data[part]
    return data


def _is_value_scalar(value: Any) -> bool:
    """Check if a value is a scalar (string, number, bool)."""
    return isinstance(value, (str, int, float, bool)) or value is None


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Zyxel sensors."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    if not coordinator.data:
        return

    sensors = []

    # Process all keys in the JSON and create sensors for them
    # We'll use a flat structure for simplicity
    for key, value in _flatten_dict(coordinator.data).items():
        # Skip non-scalar values
        if not _is_value_scalar(value):
            continue

        # The Overview and Device sections report many identical values.
        if _is_duplicate(key, coordinator.data):
            continue

        # Check if this is a known sensor type
        sensor_config = KNOWN_SENSORS.get(key.split(".")[-1], None)

        if sensor_config:
            # Create a configured sensor for known types
            sensors.append(
                ConfiguredZyxelSensor(
                    coordinator,
                    entry,
                    key,
                    sensor_config
                )
            )
        else:
            # Create a generic sensor for unknown types
            sensors.append(
                GenericZyxelSensor(
                    coordinator,
                    entry,
                    key
                )
            )

    if sensors:
        async_add_entities(sensors)


class AbstractZyxelSensor(CoordinatorEntity, SensorEntity):
    """Base class for Zyxel device sensors."""

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

    def __init__(self, coordinator, entry: ConfigEntry, key: str, config: dict):
        """Initialize the sensor."""
        super().__init__(coordinator, entry, key)
        self._config = config
        self._attr_name = f"Zyxel {_readable_name(key, config['name'])}"
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

    def __init__(self, coordinator, entry: ConfigEntry, key: str):
        """Initialize the sensor."""
        super().__init__(coordinator, entry, key)
        self._attr_entity_registry_enabled_default = (
            key in ENABLED_BY_DEFAULT or key.startswith("cellular.")
        )

    @property
    def name(self):
        """Return the name of the sensor."""
        return f"Zyxel {_readable_name(self._key)}"

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
