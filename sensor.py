# custom_components/zyxel_nr7101/sensor.py
"""Support for Zyxel NR7101 sensors."""
from __future__ import annotations

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

from .const import (
    DOMAIN,
    SENSOR_RSSI,
    SENSOR_SIGNAL_STRENGTH,
    SENSOR_CELL_ID,
    SENSOR_CONNECTION_STATUS,
    SENSOR_NETWORK_TYPE,
    SENSOR_FIRMWARE_VERSION,
    SENSOR_MODEL,
)


async def async_setup_entry(
        hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Zyxel NR7101 sensors."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    router = hass.data[DOMAIN][entry.entry_id]["router"]

    sensors = []

    # Add RSSI sensor
    if coordinator.data and SENSOR_RSSI in coordinator.data:
        sensors.append(ZyxelRSSISensor(coordinator, entry))

    # Add signal strength sensor
    if coordinator.data and SENSOR_SIGNAL_STRENGTH in coordinator.data:
        sensors.append(ZyxelSignalStrengthSensor(coordinator, entry))

    # Add connection status sensor
    if coordinator.data and SENSOR_CONNECTION_STATUS in coordinator.data:
        sensors.append(ZyxelConnectionStatusSensor(coordinator, entry))

    # Add network type sensor
    if coordinator.data and SENSOR_NETWORK_TYPE in coordinator.data:
        sensors.append(ZyxelNetworkTypeSensor(coordinator, entry))

    # Add cell ID sensor
    if coordinator.data and SENSOR_CELL_ID in coordinator.data:
        sensors.append(ZyxelCellIdSensor(coordinator, entry))

    async_add_entities(sensors)


class ZyxelSensorBase(CoordinatorEntity):
    """Base class for Zyxel NR7101 sensors."""

    def __init__(self, coordinator, entry):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{self._sensor_type}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"Zyxel NR7101 ({entry.data['host']})",
            manufacturer="Zyxel",
            model=coordinator.data.get(SENSOR_MODEL, "NR7101") if coordinator.data else "NR7101",
            sw_version=coordinator.data.get(SENSOR_FIRMWARE_VERSION, "Unknown") if coordinator.data else "Unknown",
        )


class ZyxelRSSISensor(ZyxelSensorBase):
    """Representation of a Zyxel NR7101 RSSI sensor."""

    _sensor_type = SENSOR_RSSI

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Zyxel NR7101 RSSI"

    @property
    def state(self):
        """Return the state of the sensor."""
        if self.coordinator.data and SENSOR_RSSI in self.coordinator.data:
            return self.coordinator.data[SENSOR_RSSI]
        return None

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement."""
        return "dBm"

    @property
    def icon(self):
        """Return the icon."""
        return "mdi:signal"

    @property
    def device_class(self):
        """Return the device class."""
        return SensorDeviceClass.SIGNAL_STRENGTH

    @property
    def state_class(self):
        """Return the state class."""
        return SensorStateClass.MEASUREMENT


class ZyxelSignalStrengthSensor(ZyxelSensorBase):
    """Representation of a Zyxel NR7101 signal strength sensor."""

    _sensor_type = SENSOR_SIGNAL_STRENGTH

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Zyxel NR7101 Signal Strength"

    @property
    def state(self):
        """Return the state of the sensor."""
        if self.coordinator.data and SENSOR_SIGNAL_STRENGTH in self.coordinator.data:
            return self.coordinator.data[SENSOR_SIGNAL_STRENGTH]
        return None

    @property
    def icon(self):
        """Return the icon."""
        return "mdi:signal"


class ZyxelConnectionStatusSensor(ZyxelSensorBase):
    """Representation of a Zyxel NR7101 connection status sensor."""

    _sensor_type = SENSOR_CONNECTION_STATUS

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Zyxel NR7101 Connection Status"

    @property
    def state(self):
        """Return the state of the sensor."""
        if self.coordinator.data and SENSOR_CONNECTION_STATUS in self.coordinator.data:
            return self.coordinator.data[SENSOR_CONNECTION_STATUS]
        return None

    @property
    def icon(self):
        """Return the icon."""
        return "mdi:router-wireless"


class ZyxelNetworkTypeSensor(ZyxelSensorBase):
    """Representation of a Zyxel NR7101 network type sensor."""

    _sensor_type = SENSOR_NETWORK_TYPE

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Zyxel NR7101 Network Type"

    @property
    def state(self):
        """Return the state of the sensor."""
        if self.coordinator.data and SENSOR_NETWORK_TYPE in self.coordinator.data:
            return self.coordinator.data[SENSOR_NETWORK_TYPE]
        return None

    @property
    def icon(self):
        """Return the icon."""
        return "mdi:network"


class ZyxelCellIdSensor(ZyxelSensorBase):
    """Representation of a Zyxel NR7101 cell ID sensor."""

    _sensor_type = SENSOR_CELL_ID

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Zyxel NR7101 Cell ID"

    @property
    def state(self):
        """Return the state of the sensor."""
        if self.coordinator.data and SENSOR_CELL_ID in self.coordinator.data:
            return self.coordinator.data[SENSOR_CELL_ID]
        return None

    @property
    def icon(self):
        """Return the icon."""
        return "mdi:cell-tower"