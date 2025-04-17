"""Support for Zyxel device sensors."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
)

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Define some known sensor types for proper configuration
SIGNAL_SENSORS = {
    "rssi": {
        "name": "INTF_RSSI",
        "unit": "dBm",
        "icon": "mdi:signal",
        "device_class": SensorDeviceClass.SIGNAL_STRENGTH,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    "signalStrength": {
        "name": "Signal Strength",
        "unit": "%",
        "icon": "mdi:signal",
        "device_class": None,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    "cellId": {
        "name": "Cell ID",
        "unit": None,
        "icon": "mdi:cell-tower",
        "device_class": None,
        "state_class": None,
    },
    "connectionStatus": {
        "name": "Connection Status",
        "unit": None,
        "icon": "mdi:router-wireless",
        "device_class": None,
        "state_class": None,
    },
    "networkType": {
        "name": "Network Type",
        "unit": None,
        "icon": "mdi:network",
        "device_class": None,
        "state_class": None,
    },
}


async def async_setup_entry(
        hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Zyxel sensors."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    if not coordinator.data:
        _LOGGER.error("No data received from Zyxel device")
        return

    sensors = []

    # Process all keys in the JSON and create sensors for them
    # We'll use a flat structure for simplicity
    for key, value in _flatten_dict(coordinator.data).items():
        # Skip non-scalar values
        if not _is_value_scalar(value):
            continue

        # Check if this is a known sensor type
        sensor_config = SIGNAL_SENSORS.get(key.split(".")[-1], None)

        if sensor_config:
            # Create a configured sensor for known types
            sensors.append(
                ZyxelConfiguredSensor(
                    coordinator,
                    entry,
                    key,
                    sensor_config
                )
            )
        else:
            # Create a generic sensor for unknown types
            sensors.append(
                ZyxelGenericSensor(
                    coordinator,
                    entry,
                    key
                )
            )

    async_add_entities(sensors)


def _flatten_dict(d: Dict[str, Any], parent_key: str = "") -> Dict[str, Any]:
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


class ZyxelSensorBase(CoordinatorEntity):
    """Base class for Zyxel device sensors."""

    def __init__(self, coordinator, entry, key):
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


class ZyxelConfiguredSensor(ZyxelSensorBase):
    """Representation of a configured Zyxel sensor."""

    def __init__(self, coordinator, entry, key, config):
        """Initialize the sensor."""
        super().__init__(coordinator, entry, key)
        self._config = config
        self._attr_name = f"Zyxel {config['name']}"
        self._attr_unit_of_measurement = config["unit"]
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


class ZyxelGenericSensor(ZyxelSensorBase):
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