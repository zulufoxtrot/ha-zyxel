"""Shared device registry info for all Zyxel entities."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN


def _first(data: dict[str, Any] | None, *paths: tuple[str, ...]) -> str | None:
    """Return the first non-empty string found at any of the given key paths."""
    for path in paths:
        value: Any = data or {}
        for key in path:
            value = value.get(key) if isinstance(value, dict) else None
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def build_device_info(entry: ConfigEntry, data: dict[str, Any] | None) -> DeviceInfo:
    """Build DeviceInfo named after the model, e.g. 'Zyxel EX5601-T1'."""
    model = _first(
        data,
        ("device", "DeviceInfo", "ModelName"),
        ("cardpage", "DeviceInfo", "ModelName"),
    )
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=f"Zyxel {model}" if model else f"Zyxel ({entry.data['host']})",
        manufacturer="Zyxel",
        model=model or "",
        sw_version=_first(
            data,
            ("device", "DeviceInfo", "SoftwareVersion"),
            ("cardpage", "DeviceInfo", "SoftwareVersion"),
        ),
        serial_number=None,
        configuration_url=entry.data["host"],
    )
