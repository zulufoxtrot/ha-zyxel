"""Shared device registry info for all Zyxel entities."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN


def _first(data: dict[str, Any] | None, *paths: tuple[str, ...]) -> str | None:
    for path in paths:
        value: Any = (data or {}).get(".".join(path))
        if isinstance(value, (str, int, float)) and str(value).strip():
            return str(value).strip()

        value: Any = data or {}
        for key in path:
            value = value.get(key) if isinstance(value, dict) else None
        if isinstance(value, (str, int, float)) and str(value).strip():
            return str(value).strip()
    return None


def build_device_info(entry: ConfigEntry, data: dict[str, Any] | None) -> DeviceInfo:
    """Build DeviceInfo named after the model, e.g. 'Zyxel EX5601-T1'."""
    model = _first(
        data,
        ("device", "DeviceInfo", "ModelName"),
        ("cardpage", "DeviceInfo", "ModelName"),
    )
    manufacturer = _first(
        data,
        ("device", "DeviceInfo", "Manufacturer"),
        ("cardpage", "DeviceInfo", "Manufacturer"),
    )
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=f"Zyxel {model}" if model else f"Zyxel ({entry.data['host']})",
        manufacturer=manufacturer or "Zyxel",
        model=model or "",
        sw_version=_first(
            data,
            ("device", "DeviceInfo", "SoftwareVersion"),
            ("cardpage", "DeviceInfo", "SoftwareVersion"),
        ),
        hw_version=_first(
            data,
            ("device", "DeviceInfo", "HardwareVersion"),
            ("cardpage", "DeviceInfo", "HardwareVersion"),
        ),
        serial_number=_first(
            data,
            ("device", "DeviceInfo", "SerialNumber"),
            ("cardpage", "DeviceInfo", "SerialNumber"),
        ),
        configuration_url=entry.data["host"],
    )
