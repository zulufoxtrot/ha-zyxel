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
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
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


KNOWN_SENSORS.update({
    "ChannelUtilization": {"name": "Channel utilization", "unit": "%",
        "icon": "mdi:wifi", "device_class": None, "state_class": SensorStateClass.MEASUREMENT},
    "TxPower": {"name": "Transmit power", "unit": "dBm",
        "icon": "mdi:antenna", "device_class": None, "state_class": SensorStateClass.MEASUREMENT},
    "Channel": {"name": "Channel", "unit": None,
        "icon": "mdi:wifi", "device_class": None, "state_class": None},
})


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

    if not coordinator.data:
        return

    if entry.data.get("device_type") == "nwa50ax":
        async_add_entities(
            ZyxelAPSensor(coordinator, entry, key, description)
            for key, value in _flatten_dict(coordinator.data).items()
            if _is_value_scalar(value)
            and (description := _ap_description(key)) is not None
        )
        return

    sensors = []

    # Process all keys in the JSON and create sensors for them
    # We'll use a flat structure for simplicity
    for key, value in _flatten_dict(coordinator.data).items():
        # Skip non-scalar values
        if not _is_value_scalar(value):
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
            model=coordinator.data.get("device", {}).get("model", ""),
            sw_version=coordinator.data.get("device", {}).get("firmware"),
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
        radio = key.split(".")[1] if key.startswith("radios.") else ""
        self._attr_name = f"Zyxel {radio} {config['name']}".replace("  ", " ")
        self._attr_native_unit_of_measurement = config["unit"]
        self._attr_icon = config["icon"]
        self._attr_device_class = config["device_class"]
        self._attr_state_class = config["state_class"]

    @property
    def native_value(self):
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
    def native_value(self):
        """Return the state of the sensor."""
        try:
            return self._get_value_from_path()
        except (KeyError, AttributeError):
            return None

    @property
    def icon(self):
        """Return the icon."""
        return "mdi:router-wireless"


# --- NWA access points -------------------------------------------------------

AP_RADIO_LABELS = {"radio_2g": "2.4 GHz", "radio_5g": "5 GHz", "radio_6g": "6 GHz"}

AP_DEVICE_SENSORS = {
    "model": {"name": "Model", "icon": "mdi:access-point", "diagnostic": True},
    "firmware": {"name": "Firmware", "icon": "mdi:chip", "diagnostic": True},
    "name": {"name": "Hostname", "icon": "mdi:tag-text", "diagnostic": True},
    "ip_address": {"name": "IP address", "icon": "mdi:ip-network", "diagnostic": True},
    "nebula_status": {"name": "Nebula status", "icon": "mdi:cloud-outline", "diagnostic": True},
}

AP_RADIO_SENSORS = {
    "Channel": {"name": "Channel", "icon": "mdi:wifi"},
    "ChannelUtilization": {"name": "Channel utilization", "icon": "mdi:chart-donut",
                           "unit": "%", "state_class": SensorStateClass.MEASUREMENT},
    "TxPower": {"name": "Transmit power", "icon": "mdi:antenna", "unit": "dBm",
                "state_class": SensorStateClass.MEASUREMENT},
    "BytesReceived": {"name": "Data received", "icon": "mdi:download-network",
                      "unit": "B", "suggested_unit": "GB",
                      "device_class": SensorDeviceClass.DATA_SIZE,
                      "state_class": SensorStateClass.TOTAL_INCREASING},
    "BytesSent": {"name": "Data sent", "icon": "mdi:upload-network",
                  "unit": "B", "suggested_unit": "GB",
                  "device_class": SensorDeviceClass.DATA_SIZE,
                  "state_class": SensorStateClass.TOTAL_INCREASING},
    "PacketsReceived": {"name": "Packets received", "icon": "mdi:arrow-down-bold-box-outline",
                        "unit": "packets", "state_class": SensorStateClass.TOTAL_INCREASING},
    "PacketsSent": {"name": "Packets sent", "icon": "mdi:arrow-up-bold-box-outline",
                    "unit": "packets", "state_class": SensorStateClass.TOTAL_INCREASING},
}


def _ap_description(key: str) -> dict | None:
    """Return name/unit settings for an AP data key, or None to skip it."""
    parts = key.split(".")
    if len(parts) == 2 and parts[0] == "device" and parts[1] in AP_DEVICE_SENSORS:
        return AP_DEVICE_SENSORS[parts[1]]
    if len(parts) == 3 and parts[0] == "radios" and parts[2] in AP_RADIO_SENSORS:
        band = AP_RADIO_LABELS.get(parts[1], parts[1])
        base = AP_RADIO_SENSORS[parts[2]]
        return {**base, "name": f"{band} {base['name'][0].lower()}{base['name'][1:]}"}
    return None


class ZyxelAPSensor(AbstractZyxelSensor):
    """A sensor of an NWA access point, named like '5 GHz channel'."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, entry: ConfigEntry, key: str, description: dict):
        """Initialize the sensor."""
        super().__init__(coordinator, entry, key)
        device = coordinator.data.get("device", {})
        self._attr_device_info["name"] = f"Zyxel {device.get('name') or device.get('model', 'AP')}"
        self._attr_name = description["name"]
        self._attr_icon = description["icon"]
        self._attr_native_unit_of_measurement = description.get("unit")
        self._attr_suggested_unit_of_measurement = description.get("suggested_unit")
        self._attr_device_class = description.get("device_class")
        self._attr_state_class = description.get("state_class")
        if self._attr_suggested_unit_of_measurement:
            self._attr_suggested_display_precision = 2
        if description.get("diagnostic"):
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        """Return the state of the sensor."""
        try:
            return self._get_value_from_path()
        except (KeyError, AttributeError):
            return None
