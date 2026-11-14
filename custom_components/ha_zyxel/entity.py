"""Shared entities and device helpers for the Zyxel integration."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, device_registry as dr
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .device_info import build_device_info


def get_parent_device_id(
    hass: HomeAssistant, entry: ConfigEntry, flattened_data: dict[str, Any]
) -> str | None:
    """Ensure the router device exists and return its registry ID if needed."""
    if not any("." in key for key in flattened_data):
        return None

    parent_device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        **build_device_info(entry, flattened_data),
    )
    return parent_device.id


def sync_subdevice_areas(
    hass: HomeAssistant,
    entry: ConfigEntry,
    parent_device_id: str | None,
    device_groups: set[str],
) -> str | None:
    """Assign every subdevice the same area as the router device."""
    if not parent_device_id:
        return None

    device_registry = dr.async_get(hass)
    parent_device = device_registry.async_get_device(parent_device_id)
    if not parent_device:
        return None

    area = (
        ar.async_get(hass).async_get_area(parent_device.area_id)
        if parent_device.area_id
        else None
    )
    via_device = (
        {"via_device_id": parent_device_id}
        if "via_device_id" in DeviceInfo.__annotations__
        else {"via_device": (DOMAIN, entry.entry_id)}
    )

    for device_group in device_groups:
        subdevice = device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, f"{entry.entry_id}_{device_group}")},
            name=f"Zyxel ({entry.data['host']}) {device_group}",
            manufacturer="Zyxel",
            model="",
            suggested_area=area.name if area else None,
            **via_device,
        )
        device_registry.async_update_device(
            subdevice.id,
            area_id=parent_device.area_id,
        )

    return area.name if area else None


def get_device_info(
    entry: ConfigEntry,
    device_group: str | None = None,
    parent_device_id: str | None = None,
    suggested_area: str | None = None,
    data: dict[str, Any] | None = None,
) -> DeviceInfo:
    """Build device information for a router or one of its subdevices."""
    if not device_group:
        return build_device_info(entry, data or {})

    device_info: DeviceInfo = {
        "identifiers": {(DOMAIN, f"{entry.entry_id}_{device_group}")},
        "name": f"Zyxel ({entry.data['host']}) {device_group}",
        "manufacturer": "Zyxel",
        "model": "",
    }
    if suggested_area:
        device_info["suggested_area"] = suggested_area
    if "via_device_id" in DeviceInfo.__annotations__ and parent_device_id:
        device_info["via_device_id"] = parent_device_id  # type: ignore[typeddict-unknown-key]
    else:
        device_info["via_device"] = (DOMAIN, entry.entry_id)
    return device_info


class ZyxelCoordinatorEntity(CoordinatorEntity):
    """Base coordinator entity with shared path and device handling."""

    def __init__(
        self,
        coordinator,
        entry: ConfigEntry,
        key: str,
        device_group: str | None = None,
        parent_device_id: str | None = None,
        suggested_area: str | None = None,
    ) -> None:
        """Initialize the coordinator entity."""
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = get_device_info(
            entry,
            device_group,
            parent_device_id,
            suggested_area,
            coordinator.data,
        )

    @property
    def available(self) -> bool:
        """Return whether the entity's value is available."""
        if not self.coordinator.last_update_success:
            return False

        try:
            self._get_value_from_path()
            return True
        except (KeyError, AttributeError):
            return False

    def _get_value_from_path(self) -> Any:
        """Get a value from nested dictionaries using the flattened key."""
        value = self.coordinator.data
        for key in self._key.split("."):
            value = value[key]
        return value
