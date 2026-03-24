"""Support for Zyxel device sensors."""
from __future__ import annotations

import logging
from typing import Any
from datetime import datetime, timezone, timedelta

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import *
from .coordinator import ZyxelDataUpdateCoordinator
from .entity import ZyxelBaseEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Zyxel sensors."""
    coordinator: ZyxelDataUpdateCoordinator = entry.runtime_data

    if not coordinator.data:
        return

    sensors = []
    sensors.append(LastRestartSensor(coordinator))

    configs_used = []

    # Process all keys in the JSON and create sensors for them
    # We'll use a flat structure for simplicity
    for key, value in coordinator.data.items():
        # Skip non-scalar values
        if not _is_value_scalar(value):
            continue

        # Check if this is a known sensor type
        sensor_config = KNOWN_SENSORS.get(key, KNOWN_SENSORS.get(key.split(".")[-1], None))

        if not sensor_config:
            sensor_config = { "icon": "mdi:router-wireless" }
        #if name used multiple times, use only the first time
        if 'name' in sensor_config and sensor_config['name']:
            if sensor_config['name'] in configs_used:
                sensor_config['name'] = None
                sensor_config['disabled'] = True
            else:
                configs_used.append(sensor_config['name'])
        else:
            sensor_config['disabled'] = True

        # Create a configured sensor for known types
        sensors.append(ZyxelSensorEntity(coordinator, key, sensor_config))

    async_add_entities(sensors)


class ZyxelSensorEntity(ZyxelBaseEntity, SensorEntity):
    """Representation of a configured Zyxel sensor."""

    def __init__(self, coordinator, key: str, config: dict):
        """Initialize the sensor."""
        super().__init__(coordinator, key, config)

    @property
    def state(self):
        """Return the state of the sensor."""
        try:
            return self._get_value_from_path()
        except (KeyError, AttributeError):
            return None

class LastRestartSensor(RestoreEntity, ZyxelBaseEntity, SensorEntity):
    """Sensor that shows the date/time of the last reboot."""

    def __init__(self, coordinator):        
        super().__init__(coordinator, "device.DeviceInfo.UpTime", None)
        self._attr_name = "Last Restart"
        self._attr_unique_id = self._attr_unique_id + "_timestamp"
        self._attr_device_class = "timestamp"
        self._attr_icon = "mdi:clock-check"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._last_restart = None
        self._last_uptime = None


    async def async_added_to_hass(self):
        """Called when added to Home Assistant."""
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            # Get status (date)
            if last_state.state and last_state.state != "unknown":
                try:
                    self._last_restart = datetime.fromisoformat(last_state.state)                    
                    # Get attributes
                    attrs = last_state.attributes
                    self._last_uptime = int(float(attrs["uptime"]))
                except ValueError:
                    self._last_restart = None

    @property
    def native_value(self):
        try:
            uptime = self._get_value_from_path() or self._last_uptime
            if not self._last_restart or uptime < self._last_uptime:
                self._last_uptime = uptime
                self._last_restart = datetime.now(timezone.utc) - timedelta(seconds=uptime)
                self.async_write_ha_state()
            return self._last_restart
        except (KeyError, AttributeError):
            return None
            
    @property
    def extra_state_attributes(self):
        """Add extra attributes"""
        return { "uptime": self._last_uptime }

def _is_value_scalar(value: Any) -> bool:
    """Check if a value is a scalar (string, number, bool)."""
    return isinstance(value, (str, int, float, bool)) or value is None
