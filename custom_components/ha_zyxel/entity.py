from __future__ import annotations

from typing import Optional, Any, Callable

from homeassistant.core import callback
from homeassistant.util import slugify
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .coordinator import ZyxelDataUpdateCoordinator
from .const import *

class ZyxelBaseEntity(CoordinatorEntity):
    """Base class for Zyxel device entity."""
    _attr_has_entity_name = True
    coordinator: ZyxelDataUpdateCoordinator | None = None

    def __init__(self, coordinator: ZyxelDataUpdateCoordinator, key: str, config: dict) -> None:
        super().__init__(coordinator)
        self.coordinator = coordinator
        self._key = key
        self._attr_name = key
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{key}"
        self.config = config
        if config:            
            if 'name' in config and config['name']:
                self._attr_name = config['name']
            if 'unit' in config and config['unit']:
                self._attr_native_unit_of_measurement = config['unit']
            if 'icon' in config and config['icon']:
                self._attr_icon = config['icon']
            if 'device_class' in config and config['device_class']:
                self._attr_device_class = config['device_class']
            if 'category' in config and config['category']:
                self._attr_entity_category = config['category']
            if 'state_class' in config and config['state_class']:
                self._attr_state_class = config['state_class']
            if 'disabled' in config and config['disabled']:
                self._attr_entity_registry_enabled_default = False
            



    @property
    def device_info(self):
        # Reuse the same DeviceInfo already created
        return self.coordinator.device_info

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
        value = self.coordinator.data[self._key]
        return value

